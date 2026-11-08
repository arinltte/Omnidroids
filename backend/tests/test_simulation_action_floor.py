# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket #52 (spec #50 item E): a simulation round must never act zero times.

The measured defect: the step-4 run executed 6 rounds with ZERO agent actions and
still reported success. Both causes live in the round-selection path of the three
OASIS scripts (``run_reddit_simulation.py``, ``run_twitter_simulation.py``,
``run_parallel_simulation.py``):

1. ``target_count = int(random.uniform(base_min, base_max) * multiplier)`` floors to 0
   off-peak — ``base_min=5``/``base_max=20`` with ``off_peak_activity_multiplier =
   0.05`` gives ``int(0.25 … 1.0)`` = 0 for almost every draw.
2. The simulated clock starts at hour 0 while every generated ``active_hours`` set
   sits inside 07:00-23:59, so ``if current_hour not in active_hours: continue``
   empties the candidate pool and the loop's ``if not active_agents: continue``
   silently turns the round into a no-op.

These tests drive the real selection helpers with a fake env (selection only calls
``env.agent_graph.get_agent``) and a controlled ``random``, so no OASIS env is
constructed. Import mechanism: ``scripts.<module>``, the one
``tests/test_zep_cloud_validation_script.py`` already uses — ``python -m pytest`` is
run from ``backend/``, which puts the CWD on ``sys.path``.
"""

import json
from types import SimpleNamespace

import pytest

from scripts import run_parallel_simulation as parallel_script
from scripts import run_reddit_simulation as reddit_script
from scripts import run_twitter_simulation as twitter_script

# 深夜（off-peak）配置：与 app/services/simulation_config_generator.py 写入的一致
OFF_PEAK_CONFIG = {
    "time_config": {
        "agents_per_hour_min": 5,
        "agents_per_hour_max": 20,
        "off_peak_hours": [0, 1, 2, 3, 4, 5],
        "off_peak_activity_multiplier": 0.05,
    },
    "agent_configs": [
        {
            "agent_id": agent_id,
            "active_hours": list(range(8, 23)),
            "activity_level": 0.5,
        }
        for agent_id in range(6)
    ],
}

SCRIPT_MODULES = [
    pytest.param(reddit_script, id="run_reddit_simulation"),
    pytest.param(twitter_script, id="run_twitter_simulation"),
    pytest.param(parallel_script, id="run_parallel_simulation"),
]


class _FakeAgentGraph:
    """Stands in for the OASIS agent graph: selection only calls ``get_agent``."""

    def get_agent(self, agent_id):
        return f"agent-{agent_id}"


class _FixedRandom:
    """Deterministic stand-in for the script's module-level ``random``.

    ``uniform``/``random`` are pinned (the defect is arithmetic, not a dice streak)
    and ``sample`` takes the first ``count`` ids so a selection can never come up
    empty by chance.
    """

    def __init__(self, uniform_value, draw=0.0):
        self._uniform_value = uniform_value
        self._draw = draw

    def uniform(self, _low, _high):
        return self._uniform_value

    def random(self):
        return self._draw

    def sample(self, population, count):
        assert 0 <= count <= len(population)
        return list(population)[:count]


def _make_runner(script_module, tmp_path, config):
    """Instantiate the standalone runner without an env: ``__init__`` only reads the config."""
    config_path = tmp_path / "simulation_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    runner_class = (
        reddit_script.RedditSimulationRunner
        if script_module is reddit_script
        else twitter_script.TwitterSimulationRunner
    )
    return runner_class(str(config_path))


def _select_agents(script_module, tmp_path, monkeypatch, config, current_hour, drawn):
    if drawn is not None:
        monkeypatch.setattr(script_module, "random", _FixedRandom(drawn))
    if script_module is parallel_script:
        return parallel_script.get_active_agents_for_round(
            SimpleNamespace(agent_graph=_FakeAgentGraph()),
            config,
            current_hour,
            0,
        )
    runner = _make_runner(script_module, tmp_path, config)
    return runner._get_active_agents_for_round(
        SimpleNamespace(agent_graph=_FakeAgentGraph()),
        current_hour,
        0,
    )


@pytest.mark.parametrize("script_module", SCRIPT_MODULES)
@pytest.mark.parametrize("drawn", [5.0, 12.0, 19.9])
def test_off_peak_multiplier_still_selects_one_agent(
    script_module, tmp_path, monkeypatch, drawn
):
    """Cause 1: off-peak *reduces* activity, it must not *suppress* the round."""
    selected = _select_agents(
        script_module, tmp_path, monkeypatch, OFF_PEAK_CONFIG, 0, drawn
    )

    assert len(selected) >= 1, (
        f"{script_module.__name__}: drawn={drawn} at hour 0 with a 0.05 multiplier "
        "selected nobody — int(uniform(5,20) * 0.05) floors to 0"
    )


@pytest.mark.parametrize("script_module", SCRIPT_MODULES)
def test_empty_active_hours_pool_falls_back_to_all_agents(
    script_module, tmp_path, monkeypatch
):
    """Cause 2: hour 0 is outside every generated ``active_hours`` set (07:00-23:59)."""
    config = json.loads(json.dumps(OFF_PEAK_CONFIG))
    for agent_config in config["agent_configs"]:
        agent_config["active_hours"] = list(range(7, 24))
    configured_ids = {cfg["agent_id"] for cfg in config["agent_configs"]}

    selected = _select_agents(script_module, tmp_path, monkeypatch, config, 0, 5.0)

    assert selected, (
        f"{script_module.__name__}: no agent has hour 0 in active_hours, so the "
        "round selected nobody instead of falling back to the full agent pool"
    )
    assert {agent_id for agent_id, _ in selected} <= configured_ids
    # The fallback is deterministic: the same round can never come up empty twice.
    assert _select_agents(script_module, tmp_path, monkeypatch, config, 0, 5.0) == selected


@pytest.mark.parametrize("script_module", SCRIPT_MODULES)
def test_the_measured_six_round_schedule_acts_every_round(script_module, tmp_path, monkeypatch):
    """The reported run: 72 h @ 30 min/round → 144 rounds truncated to 6 → hours 0/0/0/1/1/1."""
    config = json.loads(json.dumps(OFF_PEAK_CONFIG))
    for agent_config in config["agent_configs"]:
        agent_config["active_hours"] = list(range(7, 24))

    per_round_actions = []
    for round_num in range(6):
        simulated_hour = (round_num * 30) // 60
        selected = _select_agents(
            script_module, tmp_path, monkeypatch, config, simulated_hour, None
        )
        per_round_actions.append(len(selected))

    assert per_round_actions == [1, 1, 1, 1, 1, 1], (
        f"{script_module.__name__}: the six off-peak rounds acted "
        f"{per_round_actions} times — every round must act at least once"
    )


@pytest.mark.parametrize("script_module", SCRIPT_MODULES)
def test_run_summary_reports_a_vacuous_run_as_zero_actions(script_module):
    """Emptiness must be visible: the summary carries the total, not silence."""
    summary = script_module._format_action_summary([0, 0, 0, 0, 0, 0])

    assert "总动作数: 0" in summary
    assert "有动作轮数: 0/6" in summary
    assert "跳过轮数: 6" in summary


@pytest.mark.parametrize("script_module", SCRIPT_MODULES)
def test_run_summary_separates_acted_rounds_from_skipped_ones(script_module):
    """The summary reports total agent actions and how many rounds acted."""
    summary = script_module._format_action_summary([3, 0, 5])

    assert "总动作数: 8" in summary
    assert "有动作轮数: 2/3" in summary
    assert "跳过轮数: 1" in summary
