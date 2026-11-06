"""
供“AI 监控”界面读取/控制的只读+少量写端点。

  GET  /api/llm/status   -> 本地模型运行时状态、已加载模型(占用内存)、当前 LLM 配置
  GET  /api/llm/metrics  -> 经由 /v1 的累计使用量 + 最近调用明细
  GET  /api/llm/config   -> 当前生效的思考模式 / 并发 / 上下文
  POST /api/llm/config   -> 运行时实时切换 {reasoning_effort, num_parallel, num_ctx}
"""

from __future__ import annotations

from flask import jsonify, request

from . import llm_monitor_bp  # 使用 api/__init__.py 中定义的蓝图
from ..config import Config
from ..utils import llm_metrics


def _effective_config() -> dict:
    effort = llm_metrics.effective_reasoning_effort(Config.LLM_REASONING_EFFORT)
    return {
        "base_url": Config.LLM_BASE_URL,
        "model": Config.LLM_MODEL_NAME,
        "reasoning_effort": effort or "",
        "thinking": llm_metrics.thinking_label(effort),
        "thinking_off": llm_metrics.is_thinking_off(effort),
        "num_parallel": llm_metrics.effective_num_parallel(Config.LLM_NUM_PARALLEL),
        "num_ctx": llm_metrics.effective_num_ctx(Config.LLM_CONTEXT_LENGTH),
    }


@llm_monitor_bp.route("/config", methods=["GET"])
def get_config():
    return jsonify({"success": True, "config": _effective_config()})


@llm_monitor_bp.route("/config", methods=["POST"])
def set_config():
    data = request.get_json(silent=True) or {}
    reasoning_effort = data.get("reasoning_effort")
    if reasoning_effort is not None and str(reasoning_effort).strip().lower() not in (
        {"", "none", "low", "medium", "high"}
    ):
        return jsonify({"success": False,
                        "error": "reasoning_effort 必须是 '', 'none', 'low', 'medium', 'high'"}), 400
    llm_metrics.set_runtime_config(
        reasoning_effort=reasoning_effort,
        num_parallel=data.get("num_parallel"),
        num_ctx=data.get("num_ctx"),
    )
    return jsonify({"success": True, "config": _effective_config()})


@llm_monitor_bp.route("/status", methods=["GET"])
def status():
    runtime = llm_metrics.runtime_status()
    return jsonify({"success": True, "runtime": runtime, "config": _effective_config()})


@llm_monitor_bp.route("/metrics", methods=["GET"])
def metrics():
    snap = llm_metrics.snapshot(limit_recent=int(request.args.get("recent", 80)))
    return jsonify({"success": True, "metrics": snap, "config": _effective_config()})
