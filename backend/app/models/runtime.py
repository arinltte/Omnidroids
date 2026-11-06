"""Model runtime — interchangeable local backends behind the /v1 proxy (HARD UNLOAD).

Three backends, one interface:

* **mlx**   — macOS Apple Silicon. ``mlx_vlm`` loads the model *in this process*
  (``mlx_vlm.load`` returns ``(model, processor)``; the config rides on
  ``model.config``). ``generate`` applies no chat template — we template the
  conversation ourselves (text-only path: no image/audio/video), run the blocking
  Metal call in a worker thread so the event loop stays responsive, and read the
  normalized ``GenerationResult``. ``unload`` drops the references, ``gc.collect()``
  and clears the Metal cache — our own RSS falls, the cleanest proof of release.

* **gguf**  — cross-platform (incl. Windows). A ``llama-server`` subprocess serves
  an OpenAI-compatible ``/v1``. ``load`` spawns it and waits for ``/health``;
  ``unload`` sends **SIGTERM** (never ``kill -9``) and waits for exit — the whole
  process, and its RAM, is released.

* **ollama** — *legacy* compatibility only (off by default). HTTP to a running
  Ollama; ``json_schema`` constrained decoding is only used here. ``unload`` asks
  Ollama to evict the model (``keep_alive=0``).

Backend detection (:func:`detect_backend`) inspects the downloaded files
(``*.gguf`` → gguf ; ``config.json`` + ``*.safetensors`` → mlx) and can be
overridden per model by the user in the Model Manager.

Every runner returns a normalized :class:`GenResult`; the JSON-mode policy
(prompt-level strict JSON + repair parser for mlx/gguf, ``json_schema`` for
ollama) lives in the router (``manager.py``).
"""

from __future__ import annotations

import asyncio
import gc
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import psutil

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger("omnidroids.models.runtime")


# ---------------------------------------------------------------------------
# Normalized generation result
# ---------------------------------------------------------------------------
@dataclass
class GenResult:
    content: str
    finish_reason: Optional[str] = "stop"
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    model: Optional[str] = None


# ---------------------------------------------------------------------------
# Memory helpers (RSS before/after every phase — the hard-unload evidence)
# ---------------------------------------------------------------------------
def _proc_rss_mb(proc: Optional[psutil.Process] = None) -> int:
    try:
        proc = proc or psutil.Process()
        return int(proc.memory_info().rss / (1024 * 1024))
    except Exception:
        return -1


def _proc_tree_rss_mb(proc: Optional[psutil.Process] = None) -> int:
    """RSS of a process plus all its children (a llama-server's workers)."""
    try:
        proc = proc or psutil.Process()
        total = proc.memory_info().rss
        try:
            for child in proc.children(recursive=True):
                total += child.memory_info().rss
        except Exception:
            pass
        return int(total / (1024 * 1024))
    except Exception:
        return -1


def _find_proc_listening(port: int) -> Optional[psutil.Process]:
    """Find the process accepting on a TCP port (locate Ollama)."""
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                try:
                    return psutil.Process(conn.pid)
                except Exception:
                    continue
    except Exception:
        return None
    return None


def _post_json(url: str, obj: Dict[str, Any], timeout: float) -> bytes:
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_json(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------
def detect_backend(path: str, override: Optional[str] = None) -> str:
    """Return 'mlx' | 'gguf' | 'ollama' for a local model path."""
    if override in {"mlx", "gguf", "ollama"}:
        return override
    p = os.path.abspath(path)
    try:
        entries = os.listdir(p) if os.path.isdir(p) else [os.path.basename(p)]
    except Exception:
        entries = [os.path.basename(p)]
    # *.gguf anywhere (dir, or a passed-in .gguf file) -> llama.cpp
    has_gguf = any(e.endswith(".gguf") for e in entries) or p.endswith(".gguf")
    if has_gguf:
        return "gguf"
    has_config = "config.json" in entries
    has_weights = any(
        e.endswith((".safetensors", ".bin", ".pt")) for e in entries
    )
    if has_config and has_weights:
        # safetensors+config => MLX-converted repo (or a HF repo mlx_lm can pull);
        # GGUF is the only non-mlx weight layout we auto-detect locally.
        return "mlx"
    return "gguf"


def _pick_gguf_file(path: str) -> Optional[str]:
    """Pick the loadable .gguf (first shard for split models, else any)."""
    if os.path.isfile(path):
        # A directly-specified weights file (incl. a raw blob used for testing).
        return path
    if not os.path.isdir(path):
        return None
    ggufs = sorted(
        f for f in os.listdir(path) if f.endswith(".gguf")
    )
    if not ggufs:
        return None
    # Prefer the '00001-of-...' first shard; else the first alphabetically.
    return os.path.join(path, ggufs[0])


# ---------------------------------------------------------------------------
# Base runner
# ---------------------------------------------------------------------------
class _BaseRunner:
    backend = "?"

    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def load(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    async def unload(self) -> None:  # pragma: no cover - abstract
        self._loaded = False

    async def generate(
        self,
        *,
        messages: List[Dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.0,
        response_format: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> GenResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def memory_footprint_mb(self) -> int:
        return _proc_rss_mb()


# ---------------------------------------------------------------------------
# MLX (macOS, in-process)
# ---------------------------------------------------------------------------
class MLXRunner(_BaseRunner):
    backend = "mlx"

    def __init__(self, name: str, path: str):
        super().__init__(name, path)
        self._model = None
        self._processor = None

    async def load(self) -> None:
        try:
            import mlx_vlm  # noqa: F401
        except Exception as exc:  # pragma: no cover - install-time guard
            raise RuntimeError(
                "MLX backend requires macOS with 'mlx-vlm' installed "
                "(cd backend && uv sync --extra mlx)"
            ) from exc
        from concurrent.futures import ThreadPoolExecutor

        import mlx.core as mx

        path = self.path

        def _load():
            import mlx_vlm
            # mlx_vlm.load returns (model, processor) — a 2-tuple; the model config
            # (chat template, eos ids) rides on model.config.
            model, processor = mlx_vlm.load(path)
            mx.eval(model.parameters())
            return model, processor

        with ThreadPoolExecutor(max_workers=1) as ex:
            self._model, self._processor = ex.submit(_load).result()
        self._loaded = True
        logger.info(
            "mlx loaded %s (rss=%dMB)", self.name, self.memory_footprint_mb()
        )

    async def generate(
        self, *, messages, max_tokens=None, temperature=0.0,
        response_format=None, model=None,
    ) -> GenResult:
        if not self._loaded:
            raise RuntimeError(f"MLX model {self.name} not loaded")

        def _gen() -> GenResult:
            import mlx_vlm
            # mlx_vlm.generate applies NO chat template: template the conversation
            # ourselves. apply_chat_template accepts the message list directly
            # (mlx_vlm/chat.py passes self.history the same way). Text-only path:
            # image/audio/video stay None.
            try:
                prompt = mlx_vlm.apply_chat_template(
                    self._processor, self._model.config, messages,
                    add_generation_prompt=True,
                )
            except Exception:
                # Fallback: naive role-tagged prompt.
                prompt = "\n".join(
                    f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
                )
            kwargs: Dict[str, Any] = {"verbose": False}
            if max_tokens and max_tokens > 0:
                kwargs["max_tokens"] = int(max_tokens)
            if temperature and temperature > 0:
                # mlx_vlm resolves sampling defaults from the checkpoint's
                # generation_config.json when temperature is omitted; pass it
                # through only when the caller asks for it.
                kwargs["temperature"] = float(temperature)
            result = mlx_vlm.generate(self._model, self._processor, prompt, **kwargs)
            finish = result.finish_reason or ("length" if max_tokens else "stop")
            return GenResult(
                content=(getattr(result, "text", "") or "").strip(),
                finish_reason=finish,
                prompt_tokens=result.prompt_tokens or None,
                completion_tokens=result.generation_tokens or None,
                model=self.name,
            )

        return await asyncio.to_thread(_gen)

    async def unload(self) -> None:
        if not self._loaded:
            return
        logger.info("mlx unloading %s (rss before=%dMB)", self.name, self.memory_footprint_mb())
        self._model = None
        self._processor = None
        try:
            import mlx.core as mx
            gc.collect()
            mx.metal.clear_cache()
        except Exception as exc:  # pragma: no cover
            logger.debug("mlx cache clear: %s", exc)
        gc.collect()
        self._loaded = False
        logger.info("mlx unloaded %s (rss after=%dMB)", self.name, self.memory_footprint_mb())


# ---------------------------------------------------------------------------
# GGUF (llama-server subprocess, cross-platform)
# ---------------------------------------------------------------------------
class GGUFRunner(_BaseRunner):
    backend = "gguf"

    def __init__(self, name: str, path: str, port: int = 0):
        super().__init__(name, path)
        self._port = port or self._alloc_port()
        self._proc: Optional[subprocess.Popen] = None
        self._base = f"http://127.0.0.1:{self._port}/v1"

    @staticmethod
    def _alloc_port() -> int:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _ps_proc(self) -> Optional[psutil.Process]:
        return psutil.Process(self._proc.pid) if self._proc else None

    def memory_footprint_mb(self) -> int:
        return _proc_tree_rss_mb(self._ps_proc())

    async def load(self) -> None:
        gguf = _pick_gguf_file(self.path)
        if not gguf:
            raise RuntimeError(f"no .gguf file found under {self.path}")
        binaries = [Config.LLAMA_SERVER_BIN, "llama-server"]
        exe = next((shutil.which(b) for b in binaries if shutil.which(b)), None)
        if not exe:
            raise RuntimeError(
                "llama-server not found on PATH. Install llama.cpp (see README) "
                "or set LLAMA_SERVER_BIN=</path/to/llama-server>."
            )
        logger_dir = os.path.join(Config.PROJECT_ROOT, "logs", "models")
        try:
            os.makedirs(logger_dir, exist_ok=True)
            rid = "".join(c if (c.isalnum() or c in "._-") else "_" for c in self.name)[:40]
            self._server_log = open(os.path.join(logger_dir, f"{rid}.log"), "wb")
        except Exception:
            self._server_log = subprocess.DEVNULL
        args = [
            exe, "-m", gguf,
            "--host", "127.0.0.1", "--port", str(self._port),
            "--ctx-size", str(Config.MODEL_GGUF_CTX),
        ]
        if Config.MODEL_GGUF_THREADS > 0:
            args += ["--threads", str(Config.MODEL_GGUF_THREADS)]
        # Launch in its own process group so SIGTERM reaches the whole tree cleanly.
        self._proc = subprocess.Popen(
            args, stdout=self._server_log, stderr=subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
        )

        def _tail() -> str:
            try:
                name = getattr(self._server_log, "name", None)
                if name:
                    with open(name, "rb") as fh:
                        return fh.read()[-600:].decode("utf-8", "replace")
            except Exception:
                pass
            return "(no server log)"

        # Wait for /health.
        deadline = time.time() + 180
        last_err = "not ready"
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited early (code {self._proc.returncode}) for "
                    f"{self.name}: {_tail()}"
                )
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self._port}/health", timeout=2) as r:
                    if r.status == 200:
                        self._loaded = True
                        logger.info(
                            "gguf loaded %s on :%d (rss=%dMB)", self.name, self._port,
                            self.memory_footprint_mb(),
                        )
                        return
            except Exception as exc:
                last_err = str(exc)
            await _sleep(0.5)
        await self.unload()
        raise RuntimeError(f"llama-server did not become healthy: {last_err}")

    async def generate(
        self, *, messages, max_tokens=None, temperature=0.0,
        response_format=None, model=None,
    ) -> GenResult:
        if not self._loaded:
            raise RuntimeError(f"GGUF model {self.name} not loaded")
        body: Dict[str, Any] = {
            "model": self.name,
            "messages": messages,
            "temperature": float(temperature or 0.0),
            "stream": False,
        }
        if max_tokens and max_tokens > 0:
            body["max_tokens"] = int(max_tokens)
        # NOTE: response_format is intentionally NOT forwarded for gguf — the
        # router applies prompt-level strict JSON + a repair parser (§4.2).
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base}/chat/completions", data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=Config.LLM_PROXY_TIMEOUT) as r:
                payload = json.loads(r.read())
        except urllib.error.HTTPError as exc:  # pragma: no cover
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"llama-server HTTP {exc.code}: {detail[:300]}") from exc
        choice = (payload.get("choices") or [{}])[0]
        usage = payload.get("usage") or {}
        return GenResult(
            content=((choice.get("message") or {}).get("content") or "").strip(),
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            model=self.name,
        )

    async def unload(self) -> None:
        if not self._proc:
            self._loaded = False
            return
        logger.info("gguf unloading %s (rss before=%dMB)", self.name, self.memory_footprint_mb())
        proc, self._proc = self._proc, None
        if proc.poll() is None:
            # Graceful terminate only (never SIGKILL) — lets the KV cache flush.
            try:
                proc.terminate()
                if os.name == "posix":
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception as exc:  # pragma: no cover
                logger.debug("gguf terminate: %s", exc)
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=30)
            except Exception:  # pragma: no cover
                logger.warning("gguf %s did not exit on SIGTERM; killing process group", self.name)
                try:
                    if os.name == "posix":
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        proc.kill()
                except Exception:
                    pass
        self._loaded = False
        logger.info("gguf unloaded %s (rss after=%dMB)", self.name, self.memory_footprint_mb())


# ---------------------------------------------------------------------------
# Ollama (legacy compatibility, off by default)
# ---------------------------------------------------------------------------
class OllamaRunner(_BaseRunner):
    """Only used when MODEL_RUNTIME_BACKEND=ollama. Supports json_schema
    constrained decoding (the one backend that does). Unload = keep_alive=0."""

    backend = "ollama"

    def __init__(self, name: str, tag: Optional[str] = None):
        # name is the ollama model tag (e.g. 'lfm3:latest'); path is the upstream URL.
        super().__init__(name, tag or name)
        upstream = (Config.LLM_UPSTREAM_BASE_URL or "http://localhost:11434").rstrip("/")
        self._base = upstream

    async def load(self) -> None:
        # Warm the model (async load) and pin a long-enough keep_alive for the phase.
        try:
            await asyncio.to_thread(
                _post_json, f"{self._base}/api/generate",
                {"model": self.name, "keep_alive": "5m", "prompt": ""},
                max(Config.LLM_PROXY_TIMEOUT, 600),
            )
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"ollama warm failed HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        self._loaded = True
        logger.info("ollama warmed %s (resident≈%dMB)", self.name, self.memory_footprint_mb())

    async def generate(
        self, *, messages, max_tokens=None, temperature=0.0,
        response_format=None, model=None,
    ) -> GenResult:
        if not self._loaded:
            raise RuntimeError(f"ollama model {self.name} not warmed")
        body: Dict[str, Any] = {
            "model": self.name,
            "messages": messages,
            "temperature": float(temperature or 0.0),
            "stream": False,
        }
        if response_format is not None:
            body["response_format"] = response_format  # ollama GBNF constrained decoding
        if max_tokens and max_tokens > 0:
            body["max_tokens"] = int(max_tokens)
        # Effort/parallel knobs, honouring the AI-monitor live overrides.
        try:
            from ..utils import llm_metrics
            effort = llm_metrics.effective_reasoning_effort(Config.LLM_REASONING_EFFORT)
            num_parallel = llm_metrics.effective_num_parallel(Config.LLM_NUM_PARALLEL)
            num_ctx = llm_metrics.effective_num_ctx(Config.LLM_CONTEXT_LENGTH)
        except Exception:
            effort = (Config.LLM_REASONING_EFFORT or "").strip() or None
            num_parallel = num_ctx = None
        if effort:
            body["reasoning_effort"] = effort
        options = body.setdefault("options", {})
        if num_parallel:
            options.setdefault("num_parallel", num_parallel)
        if num_ctx:
            options.setdefault("num_ctx", num_ctx)
        try:
            raw = await asyncio.to_thread(
                _post_json, f"{self._base}/v1/chat/completions", body, Config.LLM_PROXY_TIMEOUT
            )
            payload = json.loads(raw)
        except urllib.error.HTTPError as exc:  # pragma: no cover
            raise RuntimeError(f"ollama HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        choice = (payload.get("choices") or [{}])[0]
        usage = payload.get("usage") or {}
        return GenResult(
            content=((choice.get("message") or {}).get("content") or "").strip(),
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            model=self.name,
        )

    def memory_footprint_mb(self) -> int:
        # Resident weight size Ollama holds for loaded models (≈ RAM in use).
        try:
            data = _get_json(f"{self._base}/api/ps", 3)
            total = 0
            for m in (data.get("models", []) if isinstance(data, dict) else []):
                total += max(int(m.get("size") or 0), int(m.get("size_vram") or 0))
            return int(total / (1024 * 1024))
        except Exception:
            proc = _find_proc_listening(11434)
            return _proc_tree_rss_mb(proc) if proc else -1

    async def unload(self) -> None:
        if not self._loaded:
            return
        logger.info("ollama evicting %s (resident before≈%dMB)", self.name, self.memory_footprint_mb())
        try:
            await asyncio.to_thread(
                _post_json, f"{self._base}/api/generate",
                {"model": self.name, "keep_alive": 0}, 30,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("ollama evict %s failed: %s", self.name, exc)
        self._loaded = False
        logger.info("ollama evicted %s (resident after≈%dMB)", self.name, self.memory_footprint_mb())


async def _sleep(sec: float) -> None:
    import asyncio
    await asyncio.sleep(sec)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def make_runner(name: str, *, path: str, backend: Optional[str], ollama_tag: Optional[str] = None) -> _BaseRunner:
    if backend == "ollama":
        return OllamaRunner(name, ollama_tag or name)
    resolved = backend or detect_backend(path)
    if resolved == "mlx":
        return MLXRunner(name, path)
    return GGUFRunner(name, path)


__all__ = [
    "GenResult", "detect_backend", "make_runner",
    "MLXRunner", "GGUFRunner", "OllamaRunner",
]
