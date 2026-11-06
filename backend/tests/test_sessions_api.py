# SPDX-License-Identifier: AGPL-3.0-or-later
"""§8 (#17): Sessions API — main-page list, detail with durable task rows,
resume (both the graph-build delegation and the plain clear), and 404s.
"""

from types import SimpleNamespace

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from app.config import Config
    from app.models import task as task_mod
    from app.models.project import ProjectManager
    from app.sessions import store as store_mod

    db = tmp_path / "omnidroids.db"
    monkeypatch.setattr(Config, "SESSIONS_DB_PATH", str(db))
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "uploads" / "projects"))
    store_mod.SessionStore.reset_instance()
    task_mod.TaskManager._instance = None
    return SimpleNamespace(store=store_mod.SessionStore(), uploads=tmp_path / "uploads")


@pytest.fixture()
def client(env, monkeypatch):
    from app import create_app
    from app.models import manager as manager_mod

    monkeypatch.setattr(manager_mod, "_MANAGER", None)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _project(name="Wuhan Report"):
    from app.models.project import ProjectManager, ProjectStatus

    project = ProjectManager.create_project(name=name)
    ProjectManager.save_extracted_text(project.project_id, "chunk text " * 50)
    project.status = ProjectStatus.GRAPH_BUILDING
    project.graph_id = "graph_test"
    ProjectManager.save_project(project)
    return project


def test_list_sessions_projects_every_project(env, client):
    first = _project("First")
    second = _project("Second")

    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    sessions = body["data"]["sessions"]
    assert [s["title"] for s in sessions] == ["Second", "First"]
    # a mid-build project with no live worker reads as interrupted (resume is
    # offered), never as a plain idle session
    assert {s["stage"] for s in sessions} == {"graph_build"}
    assert {s["status"] for s in sessions} == {"interrupted"}


def test_list_sessions_includes_durable_task_rows(env, client):
    project = _project()
    from app.models.task import TaskManager, TaskStatus

    manager = TaskManager()
    task_id = manager.create_task("graph_build")
    manager.update_task(task_id, status=TaskStatus.PROCESSING, progress=30)
    # the build endpoint records the task id on the project; sync_session links
    # the durable task row to the session through it (the real flow)
    from app.models.project import ProjectManager
    project.graph_build_task_id = task_id
    ProjectManager.save_project(project)

    resp = client.get("/api/sessions")
    tasks = resp.get_json()["data"]["sessions"][0]["tasks"]
    assert any(t["task_id"] == task_id and t["progress"] == 30 for t in tasks)


def test_get_session_detail_returns_tasks_and_stage(env, client):
    project = _project()
    session = env.store.ensure_session(project.project_id, title=project.name)

    resp = client.get(f"/api/sessions/{session['id']}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["id"] == session["id"]
    assert data["project_id"] == project.project_id
    assert data["stage"] == "graph_build"
    assert "tasks" in data


def test_unknown_session_is_404(env, client):
    resp = client.get("/api/sessions/sess_missing")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


def test_resume_non_graph_stage_clears_interruption(env, client):
    project = _project()
    session = env.store.ensure_session(project.project_id)
    env.store.upsert_session(
        session["id"], stage="simulate", status="interrupted", error="boom",
        meta={"resumable": True},
    )

    resp = client.post(f"/api/sessions/{session['id']}/resume", json={})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["resumed"] is True

    after = env.store.get_session(session["id"])
    assert after["status"] == "idle"
    assert after["error"] is None
    assert after["meta"]["resumable"] is False


def test_resume_graph_build_stage_delegates_to_build(env, client, monkeypatch):
    """The graph path must re-enter /api/graph/build with the project id and
    the requested force flag (§8.2 resume from the last checkpointed chunk)."""

    from app.api import graph as graph_api

    captured = {}

    def fake_build(project_id_override=None, force_override=None):
        captured["project_id"] = project_id_override
        captured["force"] = force_override
        from flask import jsonify

        return jsonify({
            "success": True,
            "data": {"project_id": project_id_override, "task_id": "t_fake"},
        })

    monkeypatch.setattr(graph_api, "_build_graph_impl", fake_build)

    project = _project()
    session = env.store.ensure_session(project.project_id)
    env.store.upsert_session(session["id"], stage="graph_build", status="interrupted")

    resp = client.post(f"/api/sessions/{session['id']}/resume", json={"force": True})
    assert resp.status_code == 200
    assert captured == {"project_id": project.project_id, "force": True}

    after = env.store.get_session(session["id"])
    assert after["status"] == "running"
    assert after["last_task_id"] == "t_fake"
    assert after["meta"]["resumable"] is False


def test_resume_propagates_build_failure(env, client, monkeypatch):
    from app.api import graph as graph_api
    from flask import jsonify

    def failing_build(project_id_override=None, force_override=None):
        return jsonify({"success": False, "error": "no resumable batch"}), 409

    monkeypatch.setattr(graph_api, "_build_graph_impl", failing_build)

    project = _project()
    session = env.store.ensure_session(project.project_id)
    env.store.upsert_session(session["id"], stage="graph_build", status="interrupted")

    resp = client.post(f"/api/sessions/{session['id']}/resume", json={})
    assert resp.status_code == 409
    # the session stays interrupted so the user can retry or force-rebuild
    assert env.store.get_session(session["id"])["status"] == "interrupted"
