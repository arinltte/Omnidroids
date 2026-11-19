# SPDX-License-Identifier: AGPL-3.0-or-later
"""A finished run must reach a terminal status while its child serves interviews.

``run_parallel_simulation.py`` keeps the OASIS environment alive after
``simulation_end`` so agents can still be interviewed, and
``_monitor_simulation`` used to publish its terminal status only once the child
exits — which it never does by itself. The run therefore stayed ``running``
forever, and the report route refuses a non-terminal run. (Upstream MiroFish
tracks the same hole as #845/#855/#968; the fix here is this repo's own shape.)

Pinned on the monitor's real loop, with a fake child that stays alive after
every platform has written ``simulation_end``:

* the terminal status is published (COMPLETED, with ``completed_at``),
* the Zep drain barrier still runs first,
* the live child is kept reachable for interviews, and Stop closes it without
  rewriting the run's result.

The phase's own 60 s grace window is shortened for the test so a real child
exit is still followed through.
"""

import threading
from types import SimpleNamespace

import pytest

from app.services import zep_graph_memory_updater as updater_module
from app.services.simulation_runner import RunnerStatus, SimulationRunner


class _FakeProcess:
    """A child that stays alive until ``trigger_exit`` is set."""

    def __init__(self):
        self._exit = threading.Event()
        self.returncode = 0
        self.pid = 4242

    def poll(self):
        return None if not self._exit.is_set() else 0

    def trigger_exit(self):
        self._exit.set()


def _write_logs(sim_dir, twitter=True, reddit=True):
    import json
    import os

    for platform, enabled in (("twitter", twitter), ("reddit", reddit)):
        if not enabled:
            continue
        directory = os.path.join(sim_dir, platform)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "actions.jsonl"), "w",
                  encoding="utf-8") as handle:
            handle.write(json.dumps({"event_type": "simulation_end",
                                     "total_rounds": 72}) + "\n")


@pytest.fixture
def monitor_env(tmp_path, monkeypatch):
    # The monitor derives its sim dir from RUN_STATE_DIR; point it at the
    # test's tmp_path so no real simulation directory is touched.
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path),
                        raising=False)
    state = SimpleNamespace(
        simulation_id="sim-tools",
        runner_status=RunnerStatus.RUNNING,
        current_round=72,
        total_rounds=72,
        twitter_completed=False,
        reddit_completed=False,
        twitter_running=True,
        reddit_running=True,
        error=None,
        completed_at=None,
    )
    monkeypatch.setattr(SimulationRunner, "COMPLETED_GRACE_S", 0.2, raising=False)
    monkeypatch.setattr(
        SimulationRunner, "get_run_state",
        classmethod(lambda _cls, _sid: state),
    )
    monkeypatch.setattr(
        SimulationRunner, "_save_run_state",
        classmethod(lambda _cls, value: None),
    )
    monkeypatch.setattr(
        SimulationRunner, "_sync_simulation_status",
        classmethod(lambda *_a, **_k: None),
    )
    monkeypatch.setattr(
        SimulationRunner, "_check_all_platforms_completed",
        classmethod(lambda _cls, value: bool(
            value.twitter_completed and value.reddit_completed)),
    )
    monkeypatch.setattr(
        SimulationRunner, "_read_action_log",
        classmethod(lambda _cls, path, position, value, platform: (
            setattr(value, f"{platform}_completed", True), position)[1]),
    )
    return state


def _run_monitor(monkeypatch, state, process, tmp_path, sim_dir):
    calls = {"stop_updater": 0}

    def _stop_updater(_simulation_id):
        calls["stop_updater"] += 1

    monkeypatch.setattr(
        updater_module.ZepGraphMemoryManager, "stop_updater",
        classmethod(lambda _cls, _sid: _stop_updater(_sid)),
    )
    # Graph memory off by default; the barrier's own path is covered elsewhere.
    monkeypatch.setattr(
        SimulationRunner, "_graph_memory_enabled", {}, raising=False)
    monkeypatch.setattr(
        SimulationRunner, "_manual_stop_requests", set(), raising=False)
    monkeypatch.setattr(
        SimulationRunner, "_processes", {"sim-tools": process}, raising=False)
    monkeypatch.setattr(
        SimulationRunner, "_interview_processes", {}, raising=False)
    monkeypatch.setattr(
        SimulationRunner, "_action_queues", {}, raising=False)
    monkeypatch.setattr(
        SimulationRunner, "_monitor_threads", {}, raising=False)
    monkeypatch.setattr(
        SimulationRunner, "_stdout_files", {}, raising=False)
    monkeypatch.setattr(
        SimulationRunner, "_stderr_files", {}, raising=False)
    # No monitor thread to join: the synchronous barrier runs instead.
    monkeypatch.setattr(
        SimulationRunner, "_monitor_threads", {}, raising=False)

    # Run the monitor body in this thread (it is a plain loop, not a thread).
    SimulationRunner._monitor_simulation("sim-tools")
    return calls


def test_completed_while_the_child_serves_interviews(monitor_env, tmp_path,
                                                    monkeypatch):
    import os

    sim_dir = tmp_path / "sim-tools"
    _write_logs(str(sim_dir))
    process = _FakeProcess()
    state = monitor_env

    _run_monitor(monkeypatch, state, process, tmp_path, sim_dir)

    # The terminal status was published...
    assert state.runner_status == RunnerStatus.COMPLETED
    assert state.completed_at is not None
    # ...the child is still alive and reachable for interviews...
    assert process.poll() is None
    assert SimulationRunner._interview_processes["sim-tools"] is process
    assert "sim-tools" not in SimulationRunner._processes


def test_a_child_that_exits_inside_the_grace_window_is_judged_on_its_code(
        monitor_env, tmp_path, monkeypatch):
    import os

    sim_dir = tmp_path / "sim-tools"
    _write_logs(str(sim_dir))
    process = _FakeProcess()
    state = monitor_env

    # Exit with a failure code before the monitor's grace window closes.
    original_poll = process.poll

    def _poll():
        if state.twitter_completed and state.reddit_completed:
            process.returncode = 3
            return 3
        return original_poll()

    process.poll = _poll
    _run_monitor(monkeypatch, state, process, tmp_path, sim_dir)

    # A dead child with a non-zero exit is a failure, not a completion.
    assert state.runner_status == RunnerStatus.FAILED
    assert state.error and "进程退出码: 3" in state.error
    assert "sim-tools" not in SimulationRunner._interview_processes


def test_stop_closes_a_completed_runs_interviews_without_rewriting_it(
        monitor_env, tmp_path, monkeypatch):
    import os

    sim_dir = tmp_path / "sim-tools"
    _write_logs(str(sim_dir))
    process = _FakeProcess()
    state = monitor_env

    _run_monitor(monkeypatch, state, process, tmp_path, sim_dir)
    assert state.runner_status == RunnerStatus.COMPLETED

    # Now stop it: the run's result stands, only the child goes away.
    monkeypatch.setattr(
        SimulationRunner, "_terminate_process",
        classmethod(lambda _cls, proc, _sid, timeout=10: proc.trigger_exit()),
    )
    SimulationRunner._manual_stop_requests = set()
    SimulationRunner.stop_simulation("sim-tools")

    assert process.poll() == 0
    assert state.runner_status == RunnerStatus.COMPLETED
    assert state.error is None
    assert "sim-tools" not in SimulationRunner._interview_processes


def test_stop_refuses_a_completed_run_with_no_live_interviews(
        monitor_env, tmp_path, monkeypatch):
    state = monitor_env
    state.runner_status = RunnerStatus.COMPLETED
    SimulationRunner._interview_processes = {}
    SimulationRunner._manual_stop_requests = set()

    with pytest.raises(ValueError):
        SimulationRunner.stop_simulation("sim-tools")


def test_cleanup_includes_interview_processes(monkeypatch):
    process = _FakeProcess()
    monkeypatch.setattr(
        SimulationRunner, "_interview_processes", {"sim-x": process},
        raising=False)
    monkeypatch.setattr(SimulationRunner, "_processes", {}, raising=False)
    monkeypatch.setattr(SimulationRunner, "_graph_memory_enabled", {},
                        raising=False)
    terminated = []
    monkeypatch.setattr(
        SimulationRunner, "get_run_state",
        classmethod(lambda _cls, _sid: None))
    monkeypatch.setattr(
        SimulationRunner, "_terminate_process",
        classmethod(lambda _cls, proc, sid, timeout=10: terminated.append(sid)))
    monkeypatch.setattr(SimulationRunner, "_cleanup_done", False, raising=False)
    monkeypatch.setattr(
        updater_module.ZepGraphMemoryManager, "get_simulation_ids",
        classmethod(lambda _cls: []))
    monkeypatch.setattr(
        updater_module.ZepGraphMemoryManager, "get_updater",
        classmethod(lambda _cls, _sid: None))

    SimulationRunner.cleanup_all_simulations()

    assert terminated == ["sim-x"]