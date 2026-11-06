"""Model manager — refcounted load/unload, HARD UNLOAD, idle safety net,
RAM-budget co-residency, per-phase RSS logging, and the JSON-mode router used by
the ``/v1`` proxy.

Choreography (the binding requirement — see HANDOFF §4.3):

    with manager.phase("small"):      # loads on enter
        ... many /v1 requests ...      # model stays resident for the whole phase
    # unloads on exit — success AND exception (finally) — and verifies RSS dropped

A per-slot refcount lets concurrent phases share one loaded model; the last
``refcount`` to hit zero triggers the deterministic unload. Requests that arrive
without an owning phase load on demand and are released by the idle monitor
(default 120 s) — so a crashed/abandoned phase can never pin RAM. Every phase
logs RSS before/after into the run log (the Model Manager + mem_check surface it).

JSON policy (§4.2): prompt-level strict JSON + a repair parser for both backends.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger
from .runtime import GenResult, detect_backend, make_runner, _proc_rss_mb
from .settings import get_settings

logger = get_logger("omnidroids.models.manager")

_JSON_MODE_TYPES = {"json_object", "json_schema"}


# ---------------------------------------------------------------------------
# Loaded-model record
# ---------------------------------------------------------------------------
@dataclass
class LoadedModel:
    key: str                     # slot key ('small'/'big'/'embed') or ad-hoc model id
    name: str
    backend: str
    runner: Any
    path: str
    refcount: int = 0
    loaded: bool = False
    last_used: float = field(default_factory=time.time)
    rss_at_load: int = -1

    def footprint_mb(self) -> int:
        try:
            return self.runner.memory_footprint_mb()
        except Exception:
            return -1


class ModelError(RuntimeError):
    """Actionable model-runtime error (surfaced verbatim to callers/UI)."""


class ModelManager:
    def __init__(self, store=None):
        from .store import ModelStore
        self.store = store or ModelStore()
        self._settings = get_settings()  # machine-local, re-read at acquire/idle time
        self._lock = threading.RLock()
        self._loaded: Dict[str, LoadedModel] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._idle_thread: Optional[threading.Thread] = None
        self._idle_stop = threading.Event()
        self._started = False

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Start the event-loop + idle-monitor threads (idempotent)."""
        with self._lock:
            if self._started:
                return
            self._started = True
        self._ensure_loop()
        self._idle_stop.clear()
        t = threading.Thread(target=self._idle_monitor, name="model-idle", daemon=True)
        t.start()
        self._idle_thread = t

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()

            def _run():
                asyncio.set_event_loop(loop)
                loop.run_forever()

            thr = threading.Thread(target=_run, name="model-loop", daemon=True)
            thr.start()
            self._loop = loop
            self._loop_thread = thr
            return loop

    def _run_async(self, coro):
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result()

    def shutdown(self) -> None:
        self._idle_stop.set()
        with self._lock:
            items = list(self._loaded.values())
        for lm in items:
            with contextlib.suppress(Exception):
                self._run_async(self._unload(lm, announce=False))

    # -- resolve which model a request/phase targets ---------------------
    def _roles(self) -> Dict[str, Optional[str]]:
        return self.store.get_roles()

    def slot_for_model(self, model: Optional[str]) -> Optional[str]:
        """Map a request model name to its assigned role slot, if any."""
        if not model:
            return None
        roles = self._roles()
        for slot, name in roles.items():
            if name and (name == model):
                return slot
        return None

    def _resolve(self, slot: Optional[str], model: Optional[str]) -> tuple[str, Dict[str, Any], Optional[str]]:
        """Return (key, registry_entry_or_synthesised, backend_override)."""
        # 1) explicit registered model name
        if model:
            entry = self.store.get_model(model)
            if entry:
                return (model, entry, entry.get("backend"))
        # 2) role-assigned model
        if slot:
            name = self.store.model_for_role(slot)
            if name:
                entry = self.store.get_model(name)
                if entry:
                    return (slot, entry, entry.get("backend"))
        # 3) fall back to small role for unnamed/default-model requests
        name = self.store.model_for_role("small")
        if name:
            entry = self.store.get_model(name)
            if entry:
                return ("small", entry, entry.get("backend"))
        raise ModelError(
            f"No local model is assigned for slot={slot!r} model={model!r}. "
            "Add one in the Model Manager and assign a role (small/big)."
        )

    # -- load / unload with hard-unload guarantees ------------------------
    def _estimate_mb(self, path: str) -> int:
        from .store import dir_size_bytes
        try:
            return int(dir_size_bytes(path) / (1024 * 1024) * 1.15)
        except Exception:
            return 0

    def _loaded_footprint_mb(self, exclude: Optional[str] = None) -> int:
        total = 0
        for key, lm in self._loaded.items():
            if key == exclude or not lm.loaded:
                continue
            mb = lm.footprint_mb()
            if mb > 0:
                total += mb
        return total

    def _loaded_count(self, exclude: Optional[str] = None) -> int:
        return sum(1 for key, lm in self._loaded.items()
                   if key != exclude and lm.loaded)

    @staticmethod
    def _limit_violation(*, other_loaded: int, est_mb: int,
                         settings: Dict[str, Any],
                         other_resident_mb: int = 0) -> Optional[str]:
        """Budget + concurrency gate against the LIVE settings (B2, #11)."""
        budget_mb = int(float(settings.get("ram_budget_gb", 11)) * 1024)
        if other_resident_mb + est_mb > budget_mb:
            return (f"RAM budget {budget_mb}MB: ~{est_mb}MB estimated + "
                    f"{other_resident_mb}MB resident exceeds it")
        concurrency = max(1, int(settings.get("concurrency", 4)))
        if other_loaded >= concurrency:
            return (f"concurrency {concurrency}: {other_loaded} model(s) already loaded — "
                    "raise the concurrency setting or close a model first")
        return None

    def _idle_seconds_now(self) -> int:
        """Idle timeout read live from settings (changes apply without restart)."""
        try:
            return max(5, int(self._settings.get()["idle_unload_seconds"]))
        except Exception:
            return max(5, Config.MODEL_IDLE_UNLOAD_SECONDS)

    def _slugify(self, s: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:60] or "model"

    async def _acquire(self, key: str, entry: Dict[str, Any], backend: Optional[str]) -> LoadedModel:
        with self._lock:
            lm = self._loaded.get(key)
            if lm is not None and lm.loaded:
                lm.last_used = time.time()
                lm.refcount += 1
                logger.info("model reuse %s (refcount=%d)", lm.key, lm.refcount)
                return lm

        name = entry["name"]
        path = entry.get("path") or ""
        forced = backend if backend in {"mlx", "gguf"} else None
        try:
            resolved = forced or detect_backend(path)
        except Exception:
            resolved = "gguf"

        # RAM-budget / concurrency co-residency check against live settings
        # (evict idle models first; refuse if impossible).
        settings = self._settings.get()
        est_mb = self._estimate_mb(path)
        with self._lock:
            other = self._loaded_footprint_mb(exclude=key)
            other_count = self._loaded_count(exclude=key)
        violation = self._limit_violation(other_loaded=other_count, est_mb=est_mb,
                                         settings=settings, other_resident_mb=other)
        if violation:
            # Try to release idle (refcount==0) models to make room.
            freed = await self._evict_idle_for(exclude=key, reserve_mb=est_mb,
                                               budget_mb=int(settings["ram_budget_gb"] * 1024))
            with self._lock:
                other = self._loaded_footprint_mb(exclude=key)
                other_count = self._loaded_count(exclude=key)
            violation = self._limit_violation(other_loaded=other_count, est_mb=est_mb,
                                             settings=settings, other_resident_mb=other)
            if violation:
                raise ModelError(
                    f"{violation} and the resident model(s) are in an active phase. "
                    "Close them, or raise the Model Manager settings.")

        runner = make_runner(name, path=path, backend=resolved)
        rss_before = _proc_rss_mb()
        logger.info("model loading %s (backend=%s, est=%dMB, rss=%dMB)", name, resolved, est_mb, rss_before)
        try:
            await runner.load()
        except Exception as exc:
            logger.error("model %s failed to load: %s", name, exc)
            raise
        rss_after = _proc_rss_mb()
        lm = LoadedModel(
            key=key, name=name, backend=resolved, runner=runner, path=path,
            refcount=1, loaded=True, rss_at_load=rss_after,
        )
        with self._lock:
            self._loaded[key] = lm
        logger.info(
            "MODEL LOADED %s [%s] rss_before=%dMB rss_after=%dMB est=%dMB",
            name, resolved, rss_before, rss_after, est_mb,
        )
        return lm

    async def _evict_idle_for(self, *, exclude: str, reserve_mb: int, budget_mb: int) -> int:
        """Unload refcount==0 models (LRU first) until reserve fits the budget."""
        freed = 0
        while True:
            with self._lock:
                candidates = [
                    (k, lm) for k, lm in self._loaded.items()
                    if k != exclude and lm.loaded and lm.refcount == 0
                ]
            if not candidates:
                return freed
            # release the least-recently-used idle model
            candidates.sort(key=lambda kv: kv[1].last_used)
            victim_key, _ = candidates[0]
            await self._unload(self._loaded.get(victim_key))
            freed += 1
            with self._lock:
                other = self._loaded_footprint_mb(exclude=exclude)
            if other + reserve_mb <= budget_mb:
                return freed

    async def _unload(self, lm: LoadedModel, announce: bool = True) -> None:
        if lm is None or not lm.loaded:
            with self._lock:
                self._loaded.pop(getattr(lm, "key", None), None)
            return
        rss_before = _proc_rss_mb()
        try:
            await lm.runner.unload()
        except Exception as exc:  # pragma: no cover
            logger.error("model %s unload error: %s", lm.name, exc)
        lm.loaded = False
        lm.refcount = 0
        with self._lock:
            self._loaded.pop(lm.key, None)
        rss_after = _proc_rss_mb()
        floor = Config.MODEL_UNLOAD_RSS_FLOOR_MB
        warn = ""
        if rss_before > 0 and rss_after > rss_before + floor:
            warn = f"  ⚠ footprint did NOT drop below floor (+{floor}MB)"
            logger.warning(
                "MODEL UNLOAD %s rss_before=%dMB rss_after=%dMB (delta=%+dMB)%s",
                lm.name, rss_before, rss_after, rss_after - rss_before, warn,
            )
        logger.info(
            "MODEL UNLOADED %s rss_before=%dMB rss_after=%dMB%s",
            lm.name, rss_before, rss_after, warn,
        )

    # -- public phase context manager (load on enter, ALWAYS unload on exit)
    @contextlib.contextmanager
    def phase(self, slot: Optional[str] = None, *, model: Optional[str] = None):
        self.start()
        key, entry, backend = self._resolve(slot, model)
        lm = self._run_async(self._acquire(key, entry, backend))
        try:
            yield lm
        finally:
            should_unload = False
            with self._lock:
                cur = self._loaded.get(key)
                if cur is lm:
                    lm.refcount -= 1
                    if lm.refcount <= 0:
                        lm.refcount = 0
                        should_unload = True
                    else:
                        lm.last_used = time.time()
                else:
                    # already unloaded/reloaded; nothing to do
                    lm.refcount = max(0, lm.refcount - 1)
            # NEVER call the blocking _run_async().result() while holding
            # self._lock — the _unload coroutine runs on the loop and would
            # block on the same lock -> deadlock. Unload outside the lock.
            if should_unload:
                self._run_async(self._unload(lm))

    # -- ensure (transient) load for a stateless proxy request --------------
    async def _ensure_loaded(self, slot: Optional[str], model: Optional[str]) -> LoadedModel:
        key, entry, backend = self._resolve(slot, model)
        with self._lock:
            lm = self._loaded.get(key)
            if lm is not None and lm.loaded:
                lm.last_used = time.time()
                return lm
        lm = await self._acquire(key, entry, backend)
        # A transient acquire bumps refcount to 1; drop it to 0 so the idle
        # monitor (not a caller phase) owns the unload.
        with self._lock:
            lm.refcount = 0
            lm.last_used = time.time()
        return lm

    # -- generation entry the /v1 proxy calls (synchronous) ---------------
    def generate_sync(
        self,
        *,
        model: Optional[str],
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.0,
        response_format: Optional[Dict[str, Any]] = None,
        slot: Optional[str] = None,
    ) -> GenResult:
        self.start()
        lm = self._run_async(self._ensure_loaded(slot, model))
        json_mode = bool(
            response_format and isinstance(response_format, dict)
            and str(response_format.get("type", "")).lower() in _JSON_MODE_TYPES
        )
        eff_messages = messages
        eff_response_format = response_format
        if json_mode:
            eff_messages = self._augment_strict_json(messages)
            eff_response_format = None
        try:
            result = self._run_async(lm.runner.generate(
                messages=eff_messages, max_tokens=max_tokens,
                temperature=temperature, response_format=eff_response_format,
                model=lm.name,
            ))
        except Exception as exc:
            logger.error("model %s generate failed: %s", lm.name, exc)
            raise
        with self._lock:
            lm.last_used = time.time()
        if json_mode:
            repaired = self._repair_json(result.content)
            result.content = repaired
        return result

    # -- JSON-mode helpers (prompt-level + repair for mlx/gguf) -----------
    @staticmethod
    def _augment_strict_json(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        instruction = (
            "You are a strict JSON API. Respond with a SINGLE valid JSON value and "
            "absolutely nothing else: no prose, no explanation, no markdown code fences."
        )
        out = list(messages)
        if out and out[0].get("role") == "system":
            out[0] = dict(out[0])
            out[0]["content"] = instruction + "\n\n" + out[0].get("content", "")
        else:
            out.insert(0, {"role": "system", "content": instruction})
        return out

    @staticmethod
    def _repair_json(text: str) -> str:
        """Extract + re-serialize the first JSON object/array in a response.

        Keeps the proxy contract intact (downstream calls ``json.loads``): if we
        can recover a complete JSON value we return it clean; otherwise the raw
        text is returned untouched so the caller's own parser raises/retries.
        """
        if not text:
            return text
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        start = next((i for i, ch in enumerate(cleaned) if ch in "{["), None)
        if start is None:
            return text
        decoder = json.JSONDecoder()
        try:
            value, _end = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            return text
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return text

    # -- idle safety net ---------------------------------------------------
    def _idle_monitor(self) -> None:
        logger.info("model idle monitor armed")
        while not self._idle_stop.wait(5.0):
            idle = self._idle_seconds_now()  # live re-read (settings apply w/o restart)
            try:
                now = time.time()
                to_unload: List[LoadedModel] = []
                with self._lock:
                    for lm in self._loaded.values():
                        if lm.loaded and lm.refcount <= 0 and (now - lm.last_used) > idle:
                            to_unload.append(lm)
                for lm in to_unload:
                    logger.info("idle hard-unload %s (idle>%ds)", lm.name, idle)
                    self._run_async(self._unload(lm))
            except Exception as exc:  # pragma: no cover
                logger.debug("idle monitor tick error: %s", exc)
        logger.info("model idle monitor stopped")

    def unload_by_name(self, name: Optional[str] = None) -> list:
        """Force-unload a loaded model by name (or all). Ignores refcount; used by
        the Model Manager 'unload now' action and shutdown."""
        self.start()
        targets: List[LoadedModel] = []
        with self._lock:
            for lm in self._loaded.values():
                if (name is None or lm.name == name) and lm.loaded:
                    targets.append(lm)
        for lm in targets:
            with self._lock:
                lm.refcount = 0
            self._run_async(self._unload(lm))
        return [lm.name for lm in targets]

    # -- status for the UI / mem_check ------------------------------------
    def status(self) -> Dict[str, Any]:
        settings = self._settings.get()
        with self._lock:
            loaded = []
            for lm in self._loaded.values():
                loaded.append({
                    "key": lm.key, "name": lm.name, "backend": lm.backend,
                    "loaded": lm.loaded, "refcount": lm.refcount,
                    "idle_s": round(time.time() - lm.last_used, 1),
                    "footprint_mb": lm.footprint_mb(),
                    "rss_at_load_mb": lm.rss_at_load,
                })
            return {
                "backend_default": Config.default_model_backend(),
                "ram_budget_mb": int(settings["ram_budget_gb"] * 1024),
                "concurrency": int(settings["concurrency"]),
                "resident_mb": self._loaded_footprint_mb(),
                "process_rss_mb": _proc_rss_mb(),
                "idle_unload_seconds": int(settings["idle_unload_seconds"]),
                "loaded": loaded,
                "roles": self.store.get_roles(),
            }


_MANAGER: Optional[ModelManager] = None
_MANAGER_LOCK = threading.Lock()


def get_model_manager() -> ModelManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = ModelManager()
        return _MANAGER
