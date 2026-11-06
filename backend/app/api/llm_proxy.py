"""
Local LLM runtime endpoint (OpenAI compatible) — Omnidroids.

Every MiroFish/OASIS client keeps sending OpenAI-format requests to
``LLM_BASE_URL`` (default ``http://localhost:5001/v1``). This blueprint receives
``POST /v1/chat/completions`` and routes it to a **local model runner** through
the ModelManager (MLX / llama.cpp-GGUF), which loads on demand
and **hard-unloads** the model when the phase/task finishes (HANDOFF §4). The
rest of the app and the vendored Graphiti client never change.

JSON policy (§4.2): prompt-level strict JSON + repair parser for both backends;
``mlx``/``gguf`` get prompt-level strict JSON + a repair parser (handled in the
manager). This endpoint only meters and re-shapes the OpenAI response.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from flask import request, Response

from . import llm_proxy_bp
from ..config import Config
from ..utils import llm_metrics
from ..utils.logger import get_logger
from ..models.manager import get_model_manager, ModelError

logger = get_logger("omnidroids.llm_proxy")


def _openai_response(result, model: str) -> Dict[str, Any]:
    """Shape a runner GenResult into an OpenAI chat.completion payload."""
    usage = {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    if result.prompt_tokens is not None or result.completion_tokens is not None:
        pt = int(result.prompt_tokens or 0)
        ct = int(result.completion_tokens or 0)
        usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
    return {
        "id": f"chatcmpl-{int(time.time()*1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result.content or ""},
            "finish_reason": result.finish_reason or "stop",
        }],
        "usage": usage,
    }


@llm_proxy_bp.route("/chat/completions", methods=["POST"])
def chat_completions():
    body = request.get_json(force=True, silent=True) or {}
    model = body.get("model") or Config.LLM_MODEL_NAME
    messages = body.get("messages") or []
    max_tokens = body.get("max_tokens")
    temperature = body.get("temperature", 0.7) or 0.0
    response_format = body.get("response_format")
    stream = bool(body.get("stream"))
    started = time.time()

    effort = llm_metrics.effective_reasoning_effort(Config.LLM_REASONING_EFFORT)
    manager = get_model_manager()
    slot = manager.slot_for_model(model)

    try:
        result = manager.generate_sync(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            slot=slot,
        )
    except ModelError as exc:
        elapsed = time.time() - started
        llm_metrics.record(model=model, reasoning_effort=effort, prompt_tokens=None,
                           completion_tokens=None, seconds=elapsed, ok=False,
                           finish_reason=None, error=str(exc)[:200])
        payload = json.dumps({"error": {"message": str(exc),
                                        "type": "model_unavailable"}})
        return Response(payload, status=503, mimetype="application/json")
    except Exception as exc:
        elapsed = time.time() - started
        llm_metrics.record(model=model, reasoning_effort=effort, prompt_tokens=None,
                           completion_tokens=None, seconds=elapsed, ok=False,
                           finish_reason=None, error=str(exc)[:200])
        logger.error("chat.completions upstream error: %s", exc)
        payload = json.dumps({"error": {"message": f"local model error: {exc}",
                                        "type": "upstream_unavailable"}})
        return Response(payload, status=502, mimetype="application/json")

    elapsed = time.time() - started
    llm_metrics.record(
        model=result.model or model,
        reasoning_effort=effort,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        seconds=elapsed,
        ok=True,
        finish_reason=result.finish_reason,
        stream=stream,
    )

    payload = _openai_response(result, result.model or model)

    if stream:
        # Emulate streaming: deliver the (already complete) result as one SSE
        # delta + done. Local runners stream marginally for this app; buffering
        # keeps the protocol correct without a full SSE bridge.
        def _sse():
            chunk = {"id": payload["id"], "object": "chat.completion.chunk",
                     "created": payload["created"], "model": payload["model"],
                     "choices": [{"index": 0, "delta": {"role": "assistant",
                                                        "content": result.content or ""},
                                 "finish_reason": None}]}
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            tail = {"id": payload["id"], "object": "chat.completion.chunk",
                    "created": payload["created"], "model": payload["model"],
                    "choices": [{"index": 0, "delta": {}, "finish_reason":
                                 result.finish_reason or "stop"}]}
            yield f"data: {json.dumps(tail, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        return Response(_sse(), content_type="text/event-stream")

    return Response(json.dumps(payload, ensure_ascii=False), status=200,
                    mimetype="application/json")
