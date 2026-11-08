"""Model runtime — interchangeable local backends behind the /v1 proxy (HARD UNLOAD).

Two backends, one interface (the legacy Ollama path was removed in B5, #14):

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

Backend detection (:func:`detect_backend`) inspects the downloaded files
(``*.gguf`` → gguf ; ``config.json`` + ``*.safetensors`` → mlx) and can be
overridden per model by the user in the Model Manager.

Every runner returns a normalized :class:`GenResult`; the JSON-mode policy
(prompt-level strict JSON + repair parser, used for both backends) lives in the
router (``manager.py``).
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
    """Return 'mlx' | 'gguf' for a local model path."""
    if override in {"mlx", "gguf"}:
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
# Token accounting
# ---------------------------------------------------------------------------
class PromptTooLongError(RuntimeError):
    """The assembled prompt does not fit the configured context window.

    Raised BEFORE generation (#51): the step-1 prepare 502 turned out to be two
    stacked defects -- a dropped ``max_tokens`` and a 17,189-token prompt going
    in unchecked against ``LLM_CONTEXT_LENGTH=8192``. An oversized prompt is
    never silently truncated here; the caller gets the exact size, the limit,
    the overage and what contributed most to it.
    """


def _model_tokenizer(processor) -> Any:
    """The tokenizer object mlx_vlm's own generation path tokenizes with."""

    return getattr(processor, "tokenizer", None) or processor


def _count_tokens(tokenizer, text: str) -> Optional[int]:
    """Exact token count for ``text``, or None when the tokenizer cannot."""

    try:
        return len(tokenizer.encode(text))
    except Exception:
        pass
    try:
        return len(tokenizer(text)["input_ids"])
    except Exception:
        return None


def _prompt_size_report(
    prompt: str,
    messages: List[Dict[str, Any]],
    processor,
    context_length: int,
) -> tuple[Optional[int], Optional[tuple[str, int]]]:
    """(prompt tokens, largest message as (label, tokens)) — an exact count.

    The count comes from the model's own tokenizer, the same one
    ``mlx_vlm.generate`` feeds the templated prompt to, so the guard compares
    real tokens instead of estimating from a character count. The per-message
    attribution is only counted once the prompt is over the limit (nothing needs
    attributing otherwise), and ``(None, None)`` means no tokenizer is reachable
    on the processor, so the size is unknown.
    """

    tokenizer = _model_tokenizer(processor)
    total = _count_tokens(tokenizer, prompt)
    if total is None or total <= context_length:
        return total, None
    largest: Optional[tuple[str, int]] = None
    for index, message in enumerate(messages):
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            content = ""
        tokens = _count_tokens(tokenizer, content) or 0
        label = f"{message.get('role', 'user')} message #{index + 1}"
        if largest is None or tokens > largest[1]:
            largest = (label, tokens)
    return total, largest


# ---------------------------------------------------------------------------
# MLX (macOS, in-process)
# ---------------------------------------------------------------------------
class MLXRunner(_BaseRunner):
    backend = "mlx"

    def __init__(self, name: str, path: str):
        super().__init__(name, path)
        self._model = None
        self._processor = None
        # ONE dedicated worker thread per loaded model, created by load() and
        # kept for the runner's lifetime. MLX's GPU streams are thread local
        # and mlx_vlm.generate mutates shared model state (the KV cache), so
        # every Metal call for this model must run on the same thread: the old
        # asyncio.to_thread dispatch put concurrent graphiti calls
        # (GRAPHITI_MAX_COROUTINES=2) on different threads and answered 17 of
        # the 9B full-ingestion calls with 502 "There is no Stream(gpu, N) in
        # current thread" (scratch/check24_9b_full_a.log, 4 of 6 chunks
        # failed). The same executor also serialises generation on one model,
        # which is what one GPU wants anyway; LLM_NUM_PARALLEL only queues.
        self._executor = None

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

        # Created here, NOT in a `with` block: the worker thread must outlive
        # load() so generate() can reuse it (tests/test_model_runtime_
        # threading.py pins load-thread == generate-thread).
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"mlx-{self.name}"
        )
        try:
            self._model, self._processor = self._executor.submit(_load).result()
        except Exception:
            self._executor.shutdown(wait=True)
            self._executor = None
            raise
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
            effective_max_tokens: Optional[int] = None
            if max_tokens and max_tokens > 0:
                # The caller's explicit budget: forwarded verbatim, which is
                # what the ontology call used to lose (#51).
                effective_max_tokens = int(max_tokens)
                kwargs["max_tokens"] = effective_max_tokens
            # else: the caller asked for no clamp, so the kwarg stays absent and
            # the provider applies its own default. That is a decision, not an
            # accident -- it is logged below rather than inherited silently.
            if temperature and temperature > 0:
                # mlx_vlm resolves sampling defaults from the checkpoint's
                # generation_config.json when temperature is omitted; pass it
                # through only when the caller asks for it.
                kwargs["temperature"] = float(temperature)
            # The prompt we are about to submit, counted with the model's own
            # tokenizer, checked against the resolved context window.
            prompt_tokens, largest_message = _prompt_size_report(
                prompt, messages, self._processor, Config.LLM_CONTEXT_LENGTH
            )
            logger.info(
                "mlx %s generate: prompt_tokens=%s max_tokens=%s context=%d",
                self.name,
                prompt_tokens if prompt_tokens is not None else "?",
                effective_max_tokens
                if effective_max_tokens is not None
                else "provider default",
                Config.LLM_CONTEXT_LENGTH,
            )
            if prompt_tokens is not None and prompt_tokens > Config.LLM_CONTEXT_LENGTH:
                over_by = prompt_tokens - Config.LLM_CONTEXT_LENGTH
                contributor = (
                    f" largest contributor: {largest_message[0]}, "
                    f"{largest_message[1]} tokens"
                    if largest_message
                    else ""
                )
                raise PromptTooLongError(
                    f"prompt of {prompt_tokens} tokens exceeds the "
                    f"{Config.LLM_CONTEXT_LENGTH}-token context window of model "
                    f"{self.name} (over by {over_by}).{contributor} Shorten the "
                    f"input (fewer or smaller documents, a shorter requirement) "
                    f"or raise LLM_CONTEXT_LENGTH; an oversized prompt is never "
                    f"silently truncated."
                )
            result = mlx_vlm.generate(self._model, self._processor, prompt, **kwargs)
            finish = result.finish_reason or ("length" if max_tokens else "stop")
            return GenResult(
                content=(getattr(result, "text", "") or "").strip(),
                finish_reason=finish,
                prompt_tokens=result.prompt_tokens or None,
                completion_tokens=result.generation_tokens or None,
                model=self.name,
            )

        if not self._executor:
            raise RuntimeError(
                f"MLX model {self.name} has no worker thread (load it first)"
            )

        # Same thread for every call: the model was loaded on this executor's
        # worker and MLX's GPU streams are thread local, so generating elsewhere
        # raises "There is no Stream(gpu, N) in current thread" once calls overlap.
        return await asyncio.get_running_loop().run_in_executor(self._executor, _gen)

    async def unload(self) -> None:
        if not self._loaded:
            return
        logger.info("mlx unloading %s (rss before=%dMB)", self.name, self.memory_footprint_mb())

        def _release() -> None:
            # Runs ON the model's worker thread: the two-pass release belongs to
            # the thread that owns the device state, just as load/generate do.
            self._model = None
            self._processor = None
            try:
                import mlx.core as mx
                # Two-pass release: the first gc.collect() frees the Python
                # objects holding the model/processor (probe: ~340MB is only
                # reclaimable AFTER gc, not by the cache clear alone);
                # mx.clear_cache() then returns the Metal allocator's cache.
                # mx.metal.clear_cache() is deprecated in mlx 0.32 — use the
                # top-level API.
                gc.collect()
                mx.clear_cache()
                gc.collect()
                mx.clear_cache()
            except Exception as exc:  # pragma: no cover
                logger.debug("mlx cache clear: %s", exc)

        executor, self._executor = self._executor, None
        try:
            if executor is not None:
                await asyncio.get_running_loop().run_in_executor(executor, _release)
        finally:
            if executor is not None:
                executor.shutdown(wait=True)
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
            await asyncio.sleep(0.5)
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
# Factory
# ---------------------------------------------------------------------------
def make_runner(name: str, *, path: str, backend: Optional[str]) -> _BaseRunner:
    resolved = backend or detect_backend(path)
    if resolved == "mlx":
        return MLXRunner(name, path)
    return GGUFRunner(name, path)


__all__ = [
    "GenResult", "detect_backend", "make_runner",
    "MLXRunner", "GGUFRunner",
]
