"""
LLM metrics + live runtime tuning.

All MiroFish LLM traffic (preprocessing services AND the OASIS agent
simulation subprocess) is routed through the local `/v1` forwarding endpoint
(see app/api/llm_proxy.py). This module keeps a single, process-wide,
thread-safe record of that traffic so the GUI can show model usage in real
time, and holds a few *runtime* knobs (thinking mode, parallelism, context)
that can be changed from the GUI without restarting the backend.

Nothing here talks to a language model itself - it is pure bookkeeping +
URL plumbing (stdlib only, no new dependencies).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional


# Which "reasoning_effort" values mean thinking is essentially OFF vs ON.
THINKING_OFF = "none"
THINKING_ON = "high"
_VALID_EFFORTS = {"none", "low", "medium", "high"}


class _MetricsState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()

        self.total_requests = 0
        self.ok_requests = 0
        self.error_requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.total_seconds = 0.0
        self.thinking_off_requests = 0
        self.thinking_on_requests = 0
        self.by_model: Dict[str, Dict[str, float]] = {}
        # Newest-first ring buffer of individual calls.
        self.recent: Deque[Dict[str, Any]] = deque(maxlen=300)

        # Live overrides. None => fall back to the value from Config/env.
        self.runtime_reasoning_effort: Optional[str] = None
        self.runtime_num_parallel: Optional[int] = None
        self.runtime_num_ctx: Optional[int] = None


STATE = _MetricsState()


# --------------------------------------------------------------------------
# Runtime config (editable from the GUI, read on every proxied request)
# --------------------------------------------------------------------------
def set_runtime_config(
    *,
    reasoning_effort: Optional[str] = None,
    num_parallel: Optional[int] = None,
    num_ctx: Optional[int] = None,
) -> None:
    with STATE._lock:
        if reasoning_effort is not None:
            reasoning_effort = str(reasoning_effort).strip().lower()
            STATE.runtime_reasoning_effort = (
                reasoning_effort if reasoning_effort in _VALID_EFFORTS else None
            )
        if num_parallel is not None:
            STATE.runtime_num_parallel = int(num_parallel) if int(num_parallel) > 0 else None
        if num_ctx is not None:
            STATE.runtime_num_ctx = int(num_ctx) if int(num_ctx) > 0 else None


def effective_reasoning_effort(config_default: str) -> Optional[str]:
    """Return the reasoning_effort to send (runtime override wins). '' => don't send."""
    with STATE._lock:
        eff = STATE.runtime_reasoning_effort
    if eff:
        return eff
    cfg = (config_default or "").strip().lower()
    return cfg or None


def effective_num_parallel(config_default: int) -> Optional[int]:
    with STATE._lock:
        v = STATE.runtime_num_parallel
    return v or (config_default if config_default and config_default > 0 else None)


def effective_num_ctx(config_default: int) -> Optional[int]:
    with STATE._lock:
        v = STATE.runtime_num_ctx
    return v or (config_default if config_default and config_default > 0 else None)


def thinking_label(reasoning_effort: Optional[str]) -> str:
    if not reasoning_effort:
        return "on (model default)"
    return "off" if reasoning_effort == THINKING_OFF else "on"


def is_thinking_off(reasoning_effort: Optional[str]) -> bool:
    return reasoning_effort == THINKING_OFF


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------
def record(
    *,
    model: Optional[str],
    reasoning_effort: Optional[str],
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    seconds: float,
    ok: bool,
    finish_reason: Optional[str] = None,
    stream: bool = False,
    error: Optional[str] = None,
) -> None:
    pt = int(prompt_tokens or 0)
    ct = int(completion_tokens or 0)
    with STATE._lock:
        STATE.total_requests += 1
        if ok:
            STATE.ok_requests += 1
        else:
            STATE.error_requests += 1
        STATE.prompt_tokens += pt
        STATE.completion_tokens += ct
        STATE.total_tokens += pt + ct
        STATE.total_seconds += max(0.0, float(seconds))

        if is_thinking_off(reasoning_effort):
            STATE.thinking_off_requests += 1
        else:
            STATE.thinking_on_requests += 1

        key = model or "(unknown)"
        agg = STATE.by_model.setdefault(key, {"requests": 0, "tokens": 0, "seconds": 0.0})
        agg["requests"] += 1
        agg["tokens"] += pt + ct
        agg["seconds"] += max(0.0, float(seconds))

        STATE.recent.appendleft(
            {
                "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "model": key,
                "reasoning": reasoning_effort or "default",
                "thinking": thinking_label(reasoning_effort),
                "prompt_tokens": pt or None,
                "completion_tokens": ct or None,
                "seconds": round(max(0.0, float(seconds)), 3),
                "ok": bool(ok),
                "finish": finish_reason,
                "stream": bool(stream),
                "error": error,
            }
        )


def snapshot(limit_recent: int = 60) -> Dict[str, Any]:
    with STATE._lock:
        total = STATE.total_requests
        secs = STATE.total_seconds
        data = {
            "uptime_seconds": round(time.time() - STATE.started_at, 1),
            "started_at": datetime.fromtimestamp(STATE.started_at).isoformat(timespec="seconds"),
            "total_requests": total,
            "ok_requests": STATE.ok_requests,
            "error_requests": STATE.error_requests,
            "prompt_tokens": STATE.prompt_tokens,
            "completion_tokens": STATE.completion_tokens,
            "total_tokens": STATE.total_tokens,
            "total_seconds": round(secs, 3),
            "avg_latency_s": round(secs / total, 3) if total else 0.0,
            "req_per_min": round((total / (secs / 60.0)), 1) if secs > 0 else 0.0,
            "thinking_off_requests": STATE.thinking_off_requests,
            "thinking_on_requests": STATE.thinking_on_requests,
            "by_model": {
                k: {
                    "requests": int(v["requests"]),
                    "tokens": int(v["tokens"]),
                    "seconds": round(v["seconds"], 2),
                }
                for k, v in STATE.by_model.items()
            },
            "runtime": {
                "reasoning_effort": STATE.runtime_reasoning_effort,
                "num_parallel": STATE.runtime_num_parallel,
                "num_ctx": STATE.runtime_num_ctx,
            },
            "recent": list(STATE.recent)[:limit_recent],
        }
    return data


# --------------------------------------------------------------------------
# Ollama server introspection (loaded models + memory) for the GUI
# --------------------------------------------------------------------------
def _http_get_json(url: str, timeout: float = 6.0) -> Any:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as exc:  # pragma: no cover - surfaced to UI as status
        raise


def ollama_status(upstream_base: str) -> Dict[str, Any]:
    """Return Ollama version + loaded models (name/size/context/VRAM) if reachable."""
    base = (upstream_base or "http://localhost:11434").rstrip("/")
    status: Dict[str, Any] = {"reachable": False, "version": None, "models": [], "error": None}
    try:
        version = _http_get_json(f"{base}/api/version")
        status["reachable"] = True
        status["version"] = (version or {}).get("version")
    except Exception as exc:
        status["error"] = f"version: {exc}"

    try:
        ps = _http_get_json(f"{base}/api/ps")
        models = []
        for m in ps.get("models", []) if isinstance(ps, dict) else []:
            models.append(
                {
                    "name": m.get("name") or m.get("model"),
                    "size": m.get("size"),
                    "size_vram": m.get("size_vram"),
                    "parameter_size": m.get("details", {}).get("parameter_size"),
                    "quantization": m.get("details", {}).get("quantization_level"),
                    "expires_at": m.get("expires_at"),
                }
            )
        status["models"] = models
        status["reachable"] = True
    except Exception as exc:
        status["error"] = (status.get("error") + " | " if status.get("error") else "") + f"ps: {exc}"

    return status
