# SPDX-License-Identifier: AGPL-3.0-or-later
"""P2: the session projection never swallows an exception silently.

`app/sessions/` and the `*project*` modules are on the read path the main page
(and the CLI console) depend on. A `except Exception: pass` there turns a
broken downstream lookup into an invisible dead-end — the session silently
stops routing onward and nothing says why. These tests pin the contract the
pre-commit hook's S110 pass guards: every swallow logs WARNING **with the
exception type**, and the remaining items still project.
"""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
#: The projection paths P2 governs (the hook's scoped S110 pass names the same).
PROJECTION_PATHS = (
    APP_ROOT / "sessions" / "service.py",
    APP_ROOT / "sessions" / "store.py",
    APP_ROOT / "models" / "project.py",
)


class _Collector(logging.Handler):
    """A direct handler — the app logger sets ``propagate = False`` (logger.py),
    so pytest's ``caplog`` never sees its records (the same idiom
    ``test_graphiti_extraction_schema.py`` uses)."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture()
def projection_env(tmp_path, monkeypatch):
    """A fresh session store + project store on tmp paths."""
    from app.config import Config
    from app.models import task as task_mod
    from app.models.project import ProjectManager
    from app.sessions import store as store_mod

    monkeypatch.setattr(Config, "SESSIONS_DB_PATH", str(tmp_path / "omnidroids.db"))
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        ProjectManager, "PROJECTS_DIR", str(tmp_path / "uploads" / "projects")
    )
    store_mod.SessionStore.reset_instance()
    task_mod.TaskManager._instance = None  # a fresh in-memory TaskManager per test
    return SimpleNamespace(store=store_mod.SessionStore(), uploads=tmp_path / "uploads")


def _graph_completed_project(name="Wuhan Report"):
    """A project whose graph is built — the state that enters `_downstream_ids`."""
    from app.models.project import ProjectManager, ProjectStatus

    project = ProjectManager.create_project(name=name)
    project.status = ProjectStatus.GRAPH_COMPLETED
    project.graph_id = "graph_test"
    ProjectManager.save_project(project)
    return ProjectManager.get_project(project.project_id)


def _warnings_of(records, logger_name):
    return [
        record for record in records
        if record.name == logger_name and record.levelno == logging.WARNING
    ]


def test_simulation_enumeration_failure_logs_warning_with_the_type(projection_env):
    """A broken simulation list must say so — and must not stop the projection."""
    from app.sessions import service

    project = _graph_completed_project()

    class _Boom:
        def list_simulations(self):
            raise RuntimeError("sim store unreadable")

    import app.services.simulation_manager  # noqa: PLC0415 - patch the source

    original = app.services.simulation_manager.SimulationManager
    app.services.simulation_manager.SimulationManager = _Boom
    collector = _Collector()
    logging.getLogger("omnidroids.sessions.service").addHandler(collector)
    try:
        session = service.sync_session(projection_env.store, project)
    finally:
        app.services.simulation_manager.SimulationManager = original
        logging.getLogger("omnidroids.sessions.service").removeHandler(collector)

    # the projection still produced the session row (personas is the stage the
    # evidence supports: no simulation could be found)
    assert session["project_id"] == project.project_id
    assert session["stage"] == "personas"
    warnings = _warnings_of(collector.records, "omnidroids.sessions.service")
    assert warnings, "a failed simulation enumeration must log a WARNING"
    assert "RuntimeError" in warnings[0].getMessage()  # the type, not just prose
    assert project.project_id in warnings[0].getMessage()  # the project is named


def test_report_lookup_failure_logs_warning_with_the_type(projection_env):
    """A report store that raises: the session routes to simulate, not nowhere."""
    from app.sessions import service

    project = _graph_completed_project()
    fake_sim = SimpleNamespace(simulation_id="sim_test", project_id=project.project_id)

    class _Sims:
        def list_simulations(self):
            return [fake_sim]

    class _Reports:
        @staticmethod
        def get_report_by_simulation(_simulation_id):
            raise ValueError("report store corrupt")

    import app.services.report_agent as report_mod
    import app.services.simulation_manager as sim_mod

    original_report = report_mod.ReportManager
    original_sim = sim_mod.SimulationManager
    report_mod.ReportManager = _Reports
    sim_mod.SimulationManager = _Sims
    collector = _Collector()
    logging.getLogger("omnidroids.sessions.service").addHandler(collector)
    try:
        session = service.sync_session(projection_env.store, project)
    finally:
        report_mod.ReportManager = original_report
        sim_mod.SimulationManager = original_sim
        logging.getLogger("omnidroids.sessions.service").removeHandler(collector)

    # the simulation was found, so the session routes to simulate; the report
    # lookup failed but said so instead of silently capping the stage
    assert session["stage"] == "simulate"
    assert session.get("sim_id") == "sim_test"
    warnings = _warnings_of(collector.records, "omnidroids.sessions.service")
    assert warnings and "ValueError" in warnings[0].getMessage()


def test_a_broken_project_is_named_and_skipped_the_rest_still_sync(projection_env):
    """sync_all_projects: one broken project must not take the main page down —
    and must not take it down silently."""
    from app.sessions import service

    good = _graph_completed_project("Good Project")
    bad = _graph_completed_project("Broken Project")

    original_sync = service.sync_session

    def _flaky_sync(store, project):
        if project.project_id == bad.project_id:
            raise RuntimeError("this project's projection is broken")
        return original_sync(store, project)

    collector = _Collector()
    logging.getLogger("omnidroids.sessions.service").addHandler(collector)
    service.sync_session = _flaky_sync
    try:
        sessions = service.sync_all_projects(limit=50)
    finally:
        service.sync_session = original_sync
        logging.getLogger("omnidroids.sessions.service").removeHandler(collector)

    titles = [session["title"] for session in sessions]
    assert titles == ["Good Project"]  # the good project still projects
    warnings = _warnings_of(collector.records, "omnidroids.sessions.service")
    assert warnings, "a skipped project must log a WARNING"
    message = warnings[0].getMessage()
    assert bad.project_id in message  # named, not anonymous
    assert "RuntimeError" in message  # its exception type travels with it


def test_reset_instance_close_failure_logs_warning_with_the_type(projection_env):
    """The store's own close path logs instead of swallowing (P2's first site)."""
    from app.sessions import store as store_mod

    store_mod.SessionStore.reset_instance()
    store = store_mod.SessionStore()

    class _Boom:
        def close(self):
            raise RuntimeError("db already closed")

    store._conn = _Boom()
    collector = _Collector()
    logging.getLogger("omnidroids.sessions.store").addHandler(collector)
    try:
        store_mod.SessionStore.reset_instance()  # must not raise
    finally:
        logging.getLogger("omnidroids.sessions.store").removeHandler(collector)

    warnings = _warnings_of(collector.records, "omnidroids.sessions.store")
    assert warnings and "RuntimeError" in warnings[0].getMessage()


def test_no_projection_module_swallows_an_exception_silently():
    """The guard: `except ...: pass` is banned on the projection paths — the
    same shape the pre-commit hook's scoped `ruff --select S110` pass rejects."""

    offenders = []
    for path in PROJECTION_PATHS:
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = [stmt for stmt in node.body if not isinstance(stmt, ast.Pass)]
            if not body:  # the whole handler is a silent swallow
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, "silent swallow(s) on the projection paths: " + ", ".join(offenders)