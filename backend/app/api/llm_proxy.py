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
from ..models.runtime import PromptTooLongError
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
    message: Dict[str, Any] = {
        "role": "assistant", "content": result.content or "",
    }
    finish_reason = result.finish_reason or "stop"
    if result.tool_calls:
        # A tool-calling client (camel/OASIS) executes these and nothing else:
        # answering with prose and no tool_calls is why no agent action ever ran.
        message["tool_calls"] = result.tool_calls
        finish_reason = "tool_calls"
    return {
        "id": f"chatcmpl-{int(time.time()*1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
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
    # Tool-calling (the OASIS agent's action channel): camel sends OpenAI
    # `tools` and executes `message.tool_calls`, so the proxy has to carry both
    # in and out. Dropping them made every agent call a prose answer that camel
    # could not turn into an action.
    tools = body.get("tools") if Config.AGENT_LLM_TOOLS else None
    tool_choice = body.get("tool_choice") if Config.AGENT_LLM_TOOLS else None
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
            tools=tools,
            tool_choice=tool_choice,
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
        # #51: tag the context guard's rejection so the client seam can re-raise
        # it as LLMResponseError and the operator sees the precise reason
        # (prompt tokens, the window, the overage, the biggest contributor)
        # instead of a generic "LLM provider request failed (HTTP 502)".
        error_type = (
            "prompt_too_long"
            if isinstance(exc, PromptTooLongError)
            else "upstream_unavailable"
        )
        payload = json.dumps({"error": {"message": f"local model error: {exc}",
                                        "type": error_type}})
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
            if result.tool_calls:
                tool_chunk = {"id": payload["id"],
                              "object": "chat.completion.chunk",
                              "created": payload["created"],
                              "model": payload["model"],
                              "choices": [{"index": 0,
                                           "delta": {"tool_calls": result.tool_calls},
                                           "finish_reason": None}]}
                yield f"data: {json.dumps(tool_chunk, ensure_ascii=False)}\n\n"
            tail = {"id": payload["id"], "object": "chat.completion.chunk",
                    "created": payload["created"], "model": payload["model"],
                    "choices": [{"index": 0, "delta": {},
                                 "finish_reason":
                                 payload["choices"][0]["finish_reason"]}]}
            yield f"data: {json.dumps(tail, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        return Response(_sse(), content_type="text/event-stream")

    return Response(json.dumps(payload, ensure_ascii=False), status=200,
                    mimetype="application/json")
