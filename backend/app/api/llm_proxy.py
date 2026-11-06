"""
本地 LLM 透明转发端点（OpenAI 兼容）。

MiroFish 现有的 LLMClient 与 OASIS/camel-ai 都按 OpenAI 兼容协议把请求发到
LLM_BASE_URL（默认 http://localhost:5001/v1）。本蓝图接收 POST /v1/chat/completions，
做三件“透传”小事后转发到本地 Ollama：

  1) 注入 `reasoning_effort`（none=关闭思考，high=开启思考）——GUI 可实时切换；
  2) 注入 `options.num_parallel` / `options.num_ctx`，把并发/上下文目标落到每一次请求，
     从而不依赖 Ollama 守护进程如何启动，都能保证“同一模型实例并发、不额外拉进程”；
  3) 记录每一次调用的 tokens/耗时/是否思考，供“AI 监控”界面展示。

不改变、不替代任何现有客户端；只是本地的一层薄转发 + 计量。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

import urllib.request
import urllib.error
from flask import request, Response, current_app

from . import llm_proxy_bp  # 使用 api/__init__.py 中定义的蓝图(避免重复注册到空蓝图)
from ..config import Config
from ..utils import llm_metrics
from ..utils.logger import get_logger

logger = get_logger("mirofish.llm_proxy")

_UPSTREAM_BASE = (Config.LLM_UPSTREAM_BASE_URL or "http://localhost:11434").rstrip("/")
_COMPLETIONS_URL = f"{_UPSTREAM_BASE}/v1/chat/completions"
_TIMEOUT = float(os.environ.get("LLM_PROXY_TIMEOUT", "600"))


def _forward(body: Dict[str, Any], *, stream: bool):
    """POST to Ollama's OpenAI endpoint, return (status, bytes, content_type)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        _COMPLETIONS_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        upstream = urllib.request.urlopen(req, timeout=_TIMEOUT)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        return exc.code, detail.encode("utf-8"), "application/json"
    except Exception as exc:  # upstream down / timeout
        payload = json.dumps({"error": {"message": f"local LLM upstream error: {exc}",
                                        "type": "upstream_unavailable"}})
        return 502, payload.encode("utf-8"), "application/json"

    raw = upstream.read()
    upstream.close()
    return 200, raw, "application/json"


@llm_proxy_bp.route("/chat/completions", methods=["POST"])
def chat_completions():
    body = request.get_json(force=True, silent=True) or {}
    model = body.get("model") or Config.LLM_MODEL_NAME

    # --- 1) thinking mode -------------------------------------------------
    effort = llm_metrics.effective_reasoning_effort(Config.LLM_REASONING_EFFORT)
    if effort and "reasoning_effort" not in body:
        body["reasoning_effort"] = effort

    # --- 2) concurrency / context targets ---------------------------------
    options = dict(body.get("options") or {})
    num_parallel = llm_metrics.effective_num_parallel(Config.LLM_NUM_PARALLEL)
    num_ctx = llm_metrics.effective_num_ctx(Config.LLM_CONTEXT_LENGTH)
    if num_parallel:
        options.setdefault("num_parallel", num_parallel)
    if num_ctx:
        options.setdefault("num_ctx", num_ctx)
    if options:
        body["options"] = options

    stream = bool(body.get("stream"))
    started = time.time()

    if stream:
        # 流式：把上游字节原样转发（SSE 透传），计量按 best-effort（不解析 usage 流）。
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(_COMPLETIONS_URL, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            upstream = urllib.request.urlopen(req, timeout=_TIMEOUT)
        except Exception as exc:  # pragma: no cover
            llm_metrics.record(model=model, reasoning_effort=effort, prompt_tokens=None,
                               completion_tokens=None, seconds=time.time() - started, ok=False,
                               stream=True, error=str(exc)[:200])
            return Response(json.dumps({"error": {"message": str(exc)}}), status=502,
                            mimetype="application/json")

        def _pump():
            try:
                while True:
                    chunk = upstream.read(8192)
                    if not chunk:
                        break
                    yield chunk
            finally:
                upstream.close()
                llm_metrics.record(model=model, reasoning_effort=effort, prompt_tokens=None,
                                   completion_tokens=None, seconds=time.time() - started, ok=True,
                                   finish_reason="stream", stream=True)

        return Response(_pump(), content_type="text/event-stream")

    # --- 非流式：转发、解析 usage、记录指标、原样返回 ----------------------
    status, raw, ctype = _forward(body, stream=False)
    elapsed = time.time() - started

    if status != 200:
        detail = raw.decode("utf-8", "replace")
        llm_metrics.record(model=model, reasoning_effort=effort, prompt_tokens=None,
                           completion_tokens=None, seconds=elapsed, ok=False,
                           finish_reason=None, error=detail[:200])
        return Response(raw, status=status, mimetype=ctype)

    try:
        payload = json.loads(raw)
        usage = payload.get("usage") or {}
        finish = None
        if payload.get("choices"):
            finish = payload["choices"][0].get("finish_reason")
        llm_metrics.record(
            model=model,
            reasoning_effort=effort,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            seconds=elapsed,
            ok=True,
            finish_reason=finish,
        )
    except Exception:
        llm_metrics.record(model=model, reasoning_effort=effort, prompt_tokens=None,
                           completion_tokens=None, seconds=elapsed, ok=True, finish_reason="stop")

    return Response(raw, status=200, mimetype="application/json")
