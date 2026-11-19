# SPDX-License-Identifier: AGPL-3.0-or-later
"""§8: Session main page — durable SQLite sessions + task mirror.

Small stdlib-sqlite3 store (WAL) at ``Omnidroids/data/omnidroids.db``
(git-ignored). It mirrors long-running tasks durably so a restarted backend can

* reload in-flight tasks and mark the ones with no live worker ``interrupted``
  (``reconcile_on_boot``), and
* offer **Resume** for a session whose graph build/persona phase died mid-way —
  graph batches checkpoint per chunk on disk (§3.1), so a resume continues from
  the last successful chunk instead of from zero.

The table writes are tiny (<1 ms); this adds no measurable memory pressure.
"""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger("omnidroids.sessions.store")

# §8.1 stage ladder (process order of a prediction run)
STAGES = ("extract", "ontology", "graph_build", "personas", "simulate", "report", "qa")
# session status values
STATUSES = ("idle", "running", "completed", "failed", "interrupted")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    stage        TEXT NOT NULL DEFAULT 'extract',
    status       TEXT NOT NULL DEFAULT 'idle',
    project_id   TEXT,
    graph_id     TEXT,
    sim_id       TEXT,
    last_task_id TEXT,
    error        TEXT,
    meta_json    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE TABLE IF NOT EXISTS tasks (
    task_id    TEXT PRIMARY KEY,
    session_id TEXT,
    type       TEXT,
    status     TEXT,
    progress   INTEGER NOT NULL DEFAULT 0,
    message    TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
"""


class SessionStore:
    """Thread-safe wrapper around the sessions/tasks SQLite tables."""

    _instance: Optional["SessionStore"] = None
    _singleton_lock = threading.Lock()

    def __new__(cls) -> "SessionStore":
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._init_db()
                    cls._instance = inst
        return cls._instance

    # -- setup ---------------------------------------------------------------

    def _init_db(self) -> None:
        self._db_path = Config.SESSIONS_DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        # RLock: the task mirror (§8.2) can fire from inside a locked section
        # (e.g. reload_inflight_tasks) on the same thread.
        self._write_lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._write_lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    @classmethod
    def reset_instance(cls) -> None:
        """Test hook: drop the singleton (call with the DB file path patched)."""

        with cls._singleton_lock:
            if cls._instance is not None:
                try:
                    cls._instance._conn.close()
                except Exception as exc:  # P2: log, never swallow silently
                    logger.warning(
                        "session store close failed during reset: %s: %s",
                        type(exc).__name__, exc,
                    )
                cls._instance = None

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["meta"] = json.loads(data.pop("meta_json", "{}") or "{}")
        return data

    # -- sessions ------------------------------------------------------------

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        cur = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def get_session_by_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM sessions WHERE project_id = ? ORDER BY updated_at DESC LIMIT 1",
            (project_id,),
        )
        row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def list_sessions(self, limit: Optional[int] = 50) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM sessions ORDER BY updated_at DESC"
        params: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def ensure_session(self, project_id: str, title: str = "") -> Dict[str, Any]:
        """Return the session for a project, creating it on first sight."""

        existing = self.get_session_by_project(project_id)
        if existing:
            return existing
        now = self._now()
        session = {
            "id": f"sess_{uuid.uuid4().hex[:12]}",
            "title": title or f"Prediction {now[:10]}",
            "created_at": now,
            "updated_at": now,
            "stage": "extract",
            "status": "idle",
            "project_id": project_id,
        }
        with self._write_lock:
            self._conn.execute(
                """INSERT INTO sessions
                   (id, title, created_at, updated_at, stage, status, project_id,
                    graph_id, sim_id, last_task_id, error, meta_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, '{}')""",
                (
                    session["id"],
                    session["title"],
                    session["created_at"],
                    session["updated_at"],
                    session["stage"],
                    session["status"],
                    session["project_id"],
                ),
            )
            self._conn.commit()
        # return the normalized row so callers see every derived column (meta,
        # graph_id, last_task_id, ...) even before the first update
        return self.get_session(session["id"])

    def upsert_session(self, session_id: str, **fields: Any) -> Dict[str, Any]:
        """Insert-or-update a session; always refreshes ``updated_at``."""

        session = self.get_session(session_id)
        if session is None:
            if "project_id" not in fields:
                raise KeyError(f"unknown session {session_id} and no project_id to create it")
            created = self.ensure_session(fields["project_id"], fields.get("title", ""))
            session = self.get_session(created["id"])
            session_id = created["id"]

        column_map = {
            "title": "title",
            "stage": "stage",
            "status": "status",
            "graph_id": "graph_id",
            "sim_id": "sim_id",
            "last_task_id": "last_task_id",
            "error": "error",
        }
        nullable = {"graph_id", "sim_id", "last_task_id", "error"}  # None clears
        sets: List[str] = []
        values: List[Any] = []
        for key, column in column_map.items():
            if key not in fields:
                continue
            value = fields[key]
            if value is None and key not in nullable:
                continue
            sets.append(f"{column} = ?")
            values.append(value)
        if "meta" in fields and isinstance(fields["meta"], dict):
            sets.append("meta_json = ?")
            values.append(json.dumps(fields["meta"], ensure_ascii=False))
        if not sets:
            # nothing actually changed — a no-op sync must not touch updated_at
            # (otherwise every main-page read reshuffles the recency order)
            return session
        sets.append("updated_at = ?")
        values.append(self._now())
        values.append(session_id)
        with self._write_lock:
            self._conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", values
            )
            self._conn.commit()
        return self.get_session(session_id)  # type: ignore[return-value]

    def delete_session(self, session_id: str) -> bool:
        with self._write_lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.execute("DELETE FROM tasks WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # -- durable task mirror (§8.2) ------------------------------------------

    def record_task(
        self,
        task_id: str,
        *,
        session_id: Optional[str] = None,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
    ) -> None:
        """Upsert the durable mirror row for a TaskManager task."""

        now = self._now()
        status = status or ("pending" if progress is None else "pending")
        with self._write_lock:
            self._conn.execute(
                """INSERT INTO tasks (task_id, session_id, type, status, progress,
                                      message, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(task_id) DO UPDATE SET
                       session_id = COALESCE(excluded.session_id, tasks.session_id),
                       type       = COALESCE(excluded.type, tasks.type),
                       status     = COALESCE(excluded.status, tasks.status),
                       progress   = COALESCE(excluded.progress, tasks.progress),
                       message    = COALESCE(excluded.message, tasks.message),
                       updated_at = excluded.updated_at""",
                (
                    task_id,
                    session_id,
                    task_type,
                    status,
                    progress or 0,
                    message or "",
                    now,
                ),
            )
            self._conn.commit()

    def link_task(self, task_id: str, session_id: str) -> None:
        with self._write_lock:
            self._conn.execute(
                "UPDATE tasks SET session_id = ? WHERE task_id = ?", (session_id, task_id)
            )
            self._conn.commit()

    def list_tasks(
        self, session_id: Optional[str] = None, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM tasks"
        clauses: List[str] = []
        params: List[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"
        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def tasks_for_sessions(self) -> Dict[str, List[Dict[str, Any]]]:
        rows = self.list_tasks()
        by_session: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            if row.get("session_id"):
                by_session.setdefault(row["session_id"], []).append(row)
        return by_session

    # -- reconciliation (§8.2/§12.4) ----------------------------------------

    def reload_inflight_tasks(self) -> List[Dict[str, Any]]:
        """Reload durably-mirrored tasks into TaskManager memory before serving.

        A fresh process has no live workers for anything it reloads, so every
        still-``processing`` task is marked ``interrupted`` — that is the
        "processing with no live worker" rule from §8.2.
        """

        from ..models.task import TaskManager, TaskStatus

        manager = TaskManager()
        reloaded: List[Dict[str, Any]] = []
        with self._write_lock:
            for row in self.list_tasks():
                task_id = row["task_id"]
                manager.restore_task(
                    task_id, row.get("type") or "task",
                    metadata={"recovered_from": row.get("updated_at"),
                              "session_id": row.get("session_id")},
                )
                if row.get("status") == "processing":
                    # No live worker can exist for a reloaded task — a fresh
                    # process lost the daemon threads that drove it.
                    manager.update_task(
                        task_id,
                        status=TaskStatus.FAILED,
                        message="interrupted: the backend restarted mid-task",
                        error="interrupted: the backend restarted mid-task",
                    )
                    self._conn.execute(
                        "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                        ("interrupted", self._now(), task_id),
                    )
                    self._conn.commit()
                    reloaded.append({"task_id": task_id, "status": "interrupted"})
                else:
                    reloaded.append({"task_id": task_id, "status": row.get("status")})
        return reloaded

    def reconcile_sessions(self) -> Dict[str, int]:
        """Boot reconciliation (§8.2/§12.4): a session in graph_build/personas
        with no live processing task becomes ``interrupted`` — the UI offers
        Resume, and batches checkpoint per chunk so it continues from the last
        successful chunk (not from zero)."""

        interrupted = 0
        for session in self.list_sessions(limit=None):
            if session["status"] not in ("idle", "running"):
                continue  # failed stays failed; interrupted stays interrupted
            if session["stage"] not in ("graph_build", "personas"):
                continue
            active = self.list_tasks(session_id=session["id"], status="processing")
            if active:
                continue
            self.upsert_session(
                session["id"],
                status="interrupted",
                error="interrupted before completion; resume continues from the last checkpointed chunk",
                meta={**session["meta"], "resumable": True},
            )
            interrupted += 1
        return {"interrupted": interrupted}


def get_session_store() -> SessionStore:
    """Process-wide SessionStore singleton."""

    return SessionStore()
