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
from ..models.downloader import get_download_registry, DownloadRejected
from ..models.settings import get_settings, SettingsError
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


@models_bp.route("/settings", methods=["GET"])
def get_model_settings():
    """Model Manager settings (concurrency / RAM budget / idle seconds)."""
    return {"code": 0, "data": get_settings().get()}


@models_bp.route("/settings", methods=["PUT"])
def update_model_settings():
    """Partial update; validated + persisted to models/settings.json, and read
    live by the manager (no restart)."""
    body = request.get_json(force=True, silent=True) or {}
    try:
        data = get_settings().update(body)
    except SettingsError as exc:
        return {"code": 400, "message": str(exc)}, 400
    return {"code": 0, "data": data}


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


_RESERVED_NAMES = {"", "download", "downloads", "settings", "role", "unload", "detect", "status"}


@models_bp.route("/<name>", methods=["DELETE"])
def delete_model(name):
    """Delete an installed model: its weights folder + registry entry (B3).

    Refuses while the model is loaded inside an active phase (unload first);
    an idle-resident model is hard-unloaded before its folder is removed. The
    folder must live under models/ — the deletion never escapes the folder.
    """
    if name in _RESERVED_NAMES:
        return {"code": 400, "message": f"reserved name {name!r}"}, 400
    manager = get_model_manager()
    manager.start()
    loaded = {lm["name"]: lm for lm in manager.status()["loaded"]}
    lm = loaded.get(name)
    if lm and lm.get("loaded") and lm.get("refcount", 0) > 0:
        return {"code": 409, "message":
                f"{name!r} is loaded in an active phase — unload it first"}, 409
    if lm and lm.get("loaded"):
        manager.unload_by_name(name)
    entry = _store().get_model(name)
    if entry is None:
        return {"code": 404, "message": f"model {name!r} is not installed"}, 404
    path = entry.get("path") or ""
    if path:
        import os
        import shutil
        real = os.path.realpath(path)
        root = os.path.realpath(Config.MODELS_DIR)
        if os.path.commonpath([root, real]) == root and os.path.isdir(real):
            shutil.rmtree(real, ignore_errors=True)
        else:
            logger.warning("delete %s: path %s outside %s; leaving files", name, real, root)
    _store().remove_model(name)
    return {"code": 0, "data": {"deleted": name}}


# ---------------------------------------------------------------------------
# Phase 2: HF downloads — job registry, progress polling, cancel (B1)
# ---------------------------------------------------------------------------
@models_bp.route("/download", methods=["POST"])
def start_download():
    """Paste a HF repo id -> confined download into models/ (+optional role).

    Preflight rejects over-budget / unknown / path-escaping repo ids BEFORE any
    bytes move; on acceptance the job streams and is polled via the routes below.
    """
    body = request.get_json(force=True, silent=True) or {}
    repo_id = (body.get("repo_id") or "").strip()
    role = (body.get("role") or "").strip().lower() or None
    if not repo_id:
        return {"code": 400, "message": "repo_id is required"}, 400
    try:
        job = get_download_registry().submit(repo_id, role=role)
    except DownloadRejected as exc:
        return {"code": exc.http_status, "message": str(exc)}, exc.http_status
    return {"code": 0, "data": job.to_dict()}, 202


@models_bp.route("/downloads", methods=["GET"])
def list_downloads():
    return {"code": 0, "data": {"jobs": get_download_registry().list()}}


@models_bp.route("/downloads/<job_id>", methods=["GET"])
def get_download(job_id):
    job = get_download_registry().get(job_id)
    if job is None:
        return {"code": 404, "message": f"unknown download job {job_id!r}"}, 404
    return {"code": 0, "data": job}


@models_bp.route("/downloads/<job_id>/cancel", methods=["POST"])
def cancel_download(job_id):
    state = get_download_registry().cancel(job_id)
    if state is None:
        return {"code": 404, "message": f"unknown download job {job_id!r}"}, 404
    if state == "not-cancellable":
        return {"code": 409, "message": "job is already in a terminal state"}, 409
    return {"code": 0, "data": {"job_id": job_id, "state": state,
                                "job": get_download_registry().get(job_id)}}
