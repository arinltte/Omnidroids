"""Model Manager API — local model inventory, roles, load state.

Phase 1: list / assign roles / unload-now / runtime status. The paste-HF-repo-id →
download flow (progress polling) arrives in Phase 2. Everything here stays local;
no endpoints touch a cloud service.
"""

from __future__ import annotations

from flask import request

from . import models_bp
from ..models.manager import get_model_manager, ModelError
from ..models.runtime import detect_backend
from ..models.store import ModelStore, dir_size_bytes
from ..config import Config
from ..utils.logger import get_logger

logger = get_logger("omnidroids.models.api")


def _store() -> ModelStore:
    return get_model_manager().store


@models_bp.route("", methods=["GET"])
@models_bp.route("/", methods=["GET"])
def list_models():
    manager = get_model_manager()
    status = manager.status()
    loaded_by_name = {lm["name"]: lm for lm in status["loaded"]}
    roles = _store().get_roles()
    models = []
    for m in _store().list_models():
        lm = loaded_by_name.get(m["name"])
        models.append({
            **m,
            "roles": [r for r, n in roles.items() if n == m["name"]],
            "loaded": bool(lm and lm.get("loaded")),
            "footprint_mb": (lm or {}).get("footprint_mb"),
        })
    return {"code": 0, "data": {
        "models": models,
        "roles": roles,
        "runtime": {k: status[k] for k in (
            "backend_default", "ram_budget_mb", "resident_mb", "process_rss_mb",
            "idle_unload_seconds")},
        "loaded": status["loaded"],
    }}


@models_bp.route("/status", methods=["GET"])
def status():
    return {"code": 0, "data": get_model_manager().status()}


@models_bp.route("/role", methods=["POST"])
def set_role():
    body = request.get_json(force=True, silent=True) or {}
    role = (body.get("role") or "").strip().lower()
    name = body.get("name")
    try:
        _store().set_role(role, name)
    except ValueError as exc:
        return {"code": 400, "message": str(exc)}, 400
    return {"code": 0, "data": _store().get_roles()}


@models_bp.route("/unload", methods=["POST"])
def unload_now():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name")  # unload one model by name, or all if omitted
    manager = get_model_manager()
    manager.start()
    unloaded = manager.unload_by_name(name)
    return {"code": 0, "data": {"unloaded": unloaded, "status": manager.status()}}


@models_bp.route("/detect", methods=["POST"])
def detect():
    """Reports the auto-detected backend for a local path (Model Manager helper)."""
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    override = (body.get("override") or "").strip().lower() or None
    import os
    if not path or not os.path.exists(path):
        return {"code": 400, "message": "path does not exist", "data": None}, 400
    backend = detect_backend(path, override)
    return {"code": 0, "data": {"path": path, "backend": backend,
                                "size_bytes": dir_size_bytes(path)}}
