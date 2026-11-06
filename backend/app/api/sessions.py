# SPDX-License-Identifier: AGPL-3.0-or-later
"""§8.3: Sessions API — the main page's data source.

- GET    /api/sessions            → main-page list (project store → session projection)
- GET    /api/sessions/<id>       → one session + its durable task rows
- POST   /api/sessions/<id>/resume → clear the interrupted state and re-enter the
                                     build from the last checkpointed chunk
- DELETE /api/sessions/<id>       → remove the session row (optionally the files)
"""

from flask import jsonify, make_response, request

from . import sessions_bp
from ..models.project import ProjectManager
from ..models.task import TaskManager
from ..sessions.service import sync_all_projects, sync_session
from ..sessions.store import get_session_store
from ..utils.logger import get_logger
from ..utils.locale import t

logger = get_logger("omnidroids.sessions.api")


def _ok(data):
    return jsonify({"success": True, "data": data})


def _err(message, status=400):
    return jsonify({"success": False, "error": message}), status


def _as_response(result):
    """Normalize a (Response, status) tuple — Flask only unwraps those at the
    view boundary, and _build_graph_impl returns them on its error paths."""

    if isinstance(result, tuple):
        body = result[0]
        status = result[1] if len(result) > 1 else 200
        return make_response(body, status)
    return result


@sessions_bp.route("", methods=["GET"])
@sessions_bp.route("/", methods=["GET"])
def list_sessions():
    limit = request.args.get("limit", type=int) or 50
    sessions = sync_all_projects(limit=limit)
    tasks_by_session = get_session_store().tasks_for_sessions()
    for session in sessions:
        session["tasks"] = tasks_by_session.get(session["id"], [])[:5]
    return _ok({"sessions": sessions, "count": len(sessions)})


@sessions_bp.route("/<session_id>", methods=["GET"])
def get_session(session_id: str):
    store = get_session_store()
    session = store.get_session(session_id)
    if session is None:
        return _err(t("api.sessionNotFound", id=session_id), 404)
    if session.get("project_id"):
        project = ProjectManager.get_project(session["project_id"])
        if project is not None:
            session = sync_session(store, project)
    session["tasks"] = store.list_tasks(session_id=session_id)
    return _ok(session)


@sessions_bp.route("/<session_id>/resume", methods=["POST"])
def resume_session(session_id: str):
    """Resume an interrupted session.

    Graph batches checkpoint per chunk on disk (§3.1), so re-entering the build
    continues from the last successful chunk — ``_build_graph_impl`` already
    reconciles a persisted batch when the project input is unchanged. This
    endpoint only clears the interrupted marker and delegates to that path.
    """

    store = get_session_store()
    session = store.get_session(session_id)
    if session is None:
        return _err(t("api.sessionNotFound", id=session_id), 404)
    project_id = session.get("project_id")
    project = ProjectManager.get_project(project_id) if project_id else None
    if project is None:
        return _err(t("api.projectNotFound", id=project_id), 404)

    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))

    if session.get("stage") in ("graph_build", "personas") and project.graph_id:
        # Import inside the function: api.graph imports this module at app
        # assembly time, so a module-level import would be circular.
        from ..api.graph import _build_graph_impl

        response = _as_response(
            _build_graph_impl(project_id_override=project_id, force_override=force)
        )
        if response.status_code >= 400:
            return response  # the session stays interrupted so the user can retry
        payload = response.get_json(silent=True) or {}
        task_id = (payload.get("data") or {}).get("task_id")
        store.upsert_session(
            session_id,
            status="running",
            error=None,
            last_task_id=task_id or session.get("last_task_id"),
            meta={**session.get("meta", {}), "resumable": False},
        )
        if task_id:
            store.link_task(task_id, session_id)
        return _ok({"session_id": session_id, "resumed": True, **payload})

    # non-graph stages: clearing the interruption is enough — the stage is
    # re-entered from the session detail UI
    store.upsert_session(
        session_id,
        status="idle",
        error=None,
        meta={**session.get("meta", {}), "resumable": False},
    )
    return _ok({"session_id": session_id, "resumed": True, "stage": session.get("stage")})


@sessions_bp.route("/<session_id>", methods=["DELETE"])
def delete_session(session_id: str):
    store = get_session_store()
    session = store.get_session(session_id)
    if session is None:
        return _err(t("api.sessionNotFound", id=session_id), 404)
    if request.args.get("with_project") == "1" and session.get("project_id"):
        ProjectManager.delete_project(session["project_id"])
    deleted = store.delete_session(session_id)
    return _ok({"session_id": session_id, "deleted": deleted})
