# SPDX-License-Identifier: AGPL-3.0-or-later
"""§8: project → session projection.

Sessions are a lightweight durable view over the on-disk project store
(``uploads/projects/<id>/project.json``), simulations and reports. The projection
is recomputed on read (``sync_all``) so no pipeline stage needs to know about it;
the only write hooks are the task mirror (§8.2) and the boot reconciliation.

Stage ladder: extract → ontology → graph_build → personas → simulate → report → qa.
"""

from typing import Any, Dict, List, Optional

from ..models.project import Project, ProjectManager, ProjectStatus
from .store import SessionStore, get_session_store


def derive_stage(project: Project) -> str:
    """The furthest stage a project has visibly reached."""

    if project.status == ProjectStatus.GRAPH_BUILDING:
        return "graph_build"
    if project.status == ProjectStatus.ONTOLOGY_GENERATED:
        return "ontology"
    if project.status == ProjectStatus.GRAPH_COMPLETED:
        return _downstream_stage(project, _downstream_ids(project))
    # CREATED / FAILED — the run never got past file capture/ontology
    return "ontology" if project.ontology else "extract"


def _downstream_ids(project: Project) -> Dict[str, str]:
    """After the graph exists: personas → simulation → report. Return the ids of
    the furthest artifacts that exist so the main page can route "continue"."""

    ids: Dict[str, str] = {}
    simulations = []
    try:
        from ..services.simulation_manager import SimulationManager

        simulations = [
            s for s in SimulationManager().list_simulations()
            if s.project_id == project.project_id
        ]
    except Exception:
        pass
    if simulations:
        # the latest simulation for this project
        ids["sim_id"] = simulations[-1].simulation_id
    try:
        # ``get_report_by_simulation`` lives on ReportManager (the persisted
        # report store), not on ReportAgent (the generator): calling it on the
        # generator raised AttributeError that this except swallowed, so a
        # finished report never routed the session past ``simulate``.
        from ..services.report_agent import ReportManager

        for simulation in simulations:
            report = ReportManager.get_report_by_simulation(simulation.simulation_id)
            if report:
                ids["report_id"] = report.report_id
                break
    except Exception:
        pass
    return ids


def _downstream_stage(project: Project, ids: Dict[str, str]) -> str:
    """Stage from the furthest artifact that exists (report > simulate > personas)."""

    if ids.get("report_id"):
        return "report"
    if ids.get("sim_id"):
        return "simulate"
    return "personas"


def _live_processing(store: SessionStore, session_id: str) -> Optional[Dict[str, Any]]:
    from ..models.task import TaskManager, TaskStatus

    manager = TaskManager()
    for row in store.list_tasks(session_id=session_id):
        if row.get("status") == "processing":
            task = manager.get_task(row["task_id"])
            if task is not None and task.status == TaskStatus.PROCESSING:
                return row
    return None


def _session_status(
    store: SessionStore, session: Dict[str, Any], project: Project
) -> Optional[str]:
    """Derive the session status from live workers + durable task rows.

    A mid-build session with no live worker is **interrupted** (resume offered) —
    never idle, which would read as "nothing happened here".
    """

    if _live_processing(store, session["id"]) is not None:
        return "running"
    rows = store.list_tasks(session_id=session["id"])
    if any(row.get("status") in ("pending", "processing") for row in rows):
        # a pending row is a build that started but has not reported progress yet
        return "running"
    if derive_stage(project) in ("graph_build", "personas"):
        return "interrupted"
    return None


def sync_session(store: SessionStore, project: Project) -> Dict[str, Any]:
    """Project → one durable session row. Idempotent; safe to call on read."""

    session = store.ensure_session(project.project_id, title=project.name or "")
    ids = _downstream_ids(project) if project.status == ProjectStatus.GRAPH_COMPLETED else {}
    stage = derive_stage(project)
    fields: Dict[str, Any] = {
        "stage": stage,
        "title": project.name or session["title"],
    }
    if project.graph_id and project.graph_id != session.get("graph_id"):
        fields["graph_id"] = project.graph_id
    if ids.get("sim_id") and ids["sim_id"] != session.get("sim_id"):
        fields["sim_id"] = ids["sim_id"]
    if ids.get("report_id"):
        fields["meta"] = {**session.get("meta", {}), "report_id": ids["report_id"]}
    if project.graph_build_task_id and project.graph_build_task_id != session.get("last_task_id"):
        fields["last_task_id"] = project.graph_build_task_id
        store.link_task(project.graph_build_task_id, session["id"])

    # status: a live worker wins, then failure, then the recorded interruption
    derived = _session_status(store, session, project)
    if derived is not None:
        fields["status"] = derived
    elif project.status == ProjectStatus.FAILED:
        fields["status"] = "failed"
        fields["error"] = project.error
    elif session["status"] == "interrupted":
        fields["status"] = "interrupted"  # only an explicit resume clears this
    else:
        fields["status"] = "idle"
    if stage == "graph_build":
        fields["meta"] = {**session.get("meta", {}), "chunks": project.total_text_length}
    return store.upsert_session(session["id"], **fields)


def sync_all_projects(limit: Optional[int] = 50) -> List[Dict[str, Any]]:
    """Recompute every session from its project; return the sorted main-page list.

    Projects are walked oldest-first so the newest session is synced (and thus
    touched) last — the main page sorts by ``updated_at`` desc, so the newest
    prediction lands on top instead of the most recently *projected* one.
    """

    store = get_session_store()
    projects = sorted(
        ProjectManager.list_projects(limit=limit), key=lambda p: p.created_at
    )
    for project in projects:
        try:
            sync_session(store, project)
        except Exception:
            # a broken project must never break the whole main page
            continue
    return store.list_sessions(limit=limit)
