# SPDX-License-Identifier: AGPL-3.0-or-later
"""§8 (#17): durable sessions store — schema, task mirror, boot reconciliation,
and the project→session projection (§8.1/§8.2).
"""

import os
import sqlite3
from types import SimpleNamespace

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from app.config import Config
    from app.models import task as task_mod
    from app.models.task import TaskManager
    from app.sessions import store as store_mod

    db = tmp_path / "omnidroids.db"
    monkeypatch.setattr(Config, "SESSIONS_DB_PATH", str(db))
    store_mod.SessionStore.reset_instance()
    task_mod.TaskManager._instance = None  # fresh in-memory TaskManager per test
    store = store_mod.SessionStore()
    return SimpleNamespace(store=store, db=db)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A persisted project on disk the way ProjectManager reads it."""

    from app.config import Config
    from app.models.project import ProjectManager

    projects_dir = tmp_path / "uploads" / "projects"
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(projects_dir))
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    return ProjectManager.create_project(name="Wuhan Report")


# ---------------------------------------------------------------------------
# schema + CRUD
# ---------------------------------------------------------------------------

def test_db_created_with_wal_and_tables(store):
    assert os.path.exists(store.db)
    conn = sqlite3.connect(str(store.db))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"sessions", "tasks"} <= tables
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_ensure_session_is_idempotent_per_project(store):
    first = store.store.ensure_session("proj_1", title="A")
    second = store.store.ensure_session("proj_1", title="A")
    assert first["id"] == second["id"]
    rows = store.store.list_sessions()
    assert len(rows) == 1
    assert rows[0]["project_id"] == "proj_1"
    assert rows[0]["meta"] == {}  # meta_json parsed back into meta


def test_upsert_session_updates_and_touches_updated_at(store):
    session = store.store.ensure_session("proj_1")
    updated = store.store.upsert_session(
        session["id"], stage="graph_build", status="running", meta={"chunks": 53}
    )
    assert updated["stage"] == "graph_build"
    assert updated["status"] == "running"
    assert updated["meta"]["chunks"] == 53
    assert updated["updated_at"] >= session["updated_at"]


def test_list_sessions_sorted_by_updated_at_desc(store):
    s1 = store.store.ensure_session("proj_1")
    s2 = store.store.ensure_session("proj_2")
    store.store.upsert_session(s1["id"], stage="graph_build")  # touches s1
    ids = [s["id"] for s in store.store.list_sessions(limit=10)]
    assert ids == [s1["id"], s2["id"]]


# ---------------------------------------------------------------------------
# durable task mirror
# ---------------------------------------------------------------------------

def test_task_manager_update_mirrors_to_sessions_db(store):
    from app.models.task import TaskManager, TaskStatus

    manager = TaskManager()
    task_id = manager.create_task("graph_build", metadata={"graph_name": "g"})
    manager.update_task(task_id, status=TaskStatus.PROCESSING, progress=42,
                        message="chunking")
    rows = store.store.list_tasks()
    assert len(rows) == 1
    assert rows[0]["task_id"] == task_id
    assert rows[0]["status"] == "processing"
    assert rows[0]["progress"] == 42


def test_record_task_and_link_join_by_session(store):
    session = store.store.ensure_session("proj_1")
    store.store.record_task("t1", session_id=session["id"], status="completed")
    store.store.record_task("t2", session_id=session["id"], status="processing")
    by_session = store.store.tasks_for_sessions()
    assert {t["task_id"] for t in by_session[session["id"]]} == {"t1", "t2"}


# ---------------------------------------------------------------------------
# boot reconciliation
# ---------------------------------------------------------------------------

def test_reload_inflight_tasks_marks_processing_interrupted(store):
    from app.models.task import TaskManager, TaskStatus

    # a "previous process" left a processing task on disk
    store.store.record_task("t_live", session_id="sess_x", task_type="graph_build",
                            status="processing", progress=55)
    store.store.record_task("t_done", session_id="sess_x", task_type="report",
                            status="completed", progress=100)

    reloaded = store.store.reload_inflight_tasks()
    assert reloaded[0]["task_id"] == "t_done"      # updated_at DESC order
    assert reloaded[1]["task_id"] == "t_live"
    by_id = {r["task_id"]: r["status"] for r in reloaded}
    assert by_id["t_live"] == "interrupted"
    assert by_id["t_done"] == "completed"

    # the durable row is flipped too, and TaskManager holds the failure
    row = {t["task_id"]: t for t in store.store.list_tasks()}["t_live"]
    assert row["status"] == "interrupted"
    task = TaskManager().get_task("t_live")
    assert task.status == TaskStatus.FAILED
    assert "interrupted" in (task.error or "")


def test_reconcile_sessions_flips_incomplete_graph_build_only(store):
    build = store.store.ensure_session("proj_building")
    store.store.upsert_session(build["id"], stage="graph_build", status="running")
    personas = store.store.ensure_session("proj_personas")
    store.store.upsert_session(personas["id"], stage="personas", status="running")
    done = store.store.ensure_session("proj_done")
    store.store.upsert_session(done["id"], stage="report", status="running")
    failed = store.store.ensure_session("proj_failed")
    store.store.upsert_session(failed["id"], stage="graph_build", status="failed")

    result = store.store.reconcile_sessions()
    assert result == {"interrupted": 2}
    by_project = {
        s["project_id"]: s
        for s in store.store.list_sessions(limit=None)
    }
    assert by_project["proj_building"]["status"] == "interrupted"
    assert by_project["proj_building"]["meta"]["resumable"] is True
    assert by_project["proj_personas"]["status"] == "interrupted"
    assert by_project["proj_done"]["status"] == "running"  # later stage untouched
    assert by_project["proj_failed"]["status"] == "failed"  # failures kept

    # idempotent: a second boot does not re-count already-interrupted sessions
    assert store.store.reconcile_sessions() == {"interrupted": 0}


# ---------------------------------------------------------------------------
# project → session projection
# ---------------------------------------------------------------------------

def test_sync_session_projects_project_state(store, project):
    from app.sessions.service import sync_session
    from app.models.project import ProjectStatus

    session = sync_session(store.store, project)
    assert session["project_id"] == project.project_id
    assert session["stage"] == "extract"
    assert session["status"] == "idle"

    project.status = ProjectStatus.ONTOLOGY_GENERATED
    session = sync_session(store.store, project)
    assert session["stage"] == "ontology"

    project.status = ProjectStatus.GRAPH_BUILDING
    session = sync_session(store.store, project)
    assert session["stage"] == "graph_build"
    assert session["project_id"] == project.project_id


def test_sync_session_keeps_interrupted_until_resume(store, project):
    from app.sessions.service import sync_session
    from app.models.project import ProjectStatus

    session = sync_session(store.store, project)
    project.status = ProjectStatus.GRAPH_BUILDING
    sync_session(store.store, project)
    store.store.upsert_session(session["id"], status="interrupted")
    # a plain re-sync (e.g. the main page polling) must not clear the marker
    again = sync_session(store.store, project)
    assert again["status"] == "interrupted"
