# SPDX-License-Identifier: AGPL-3.0-or-later
"""Step → model assignment (owner decision, issue #22).

Steps 4–5 (Report Generation, Deep Interaction — both served by ``ReportAgent``)
resolve the ``big`` role from the Model Manager registry; steps 1–3 keep
resolving ``Config.LLM_MODEL_NAME``. Hermetic by design: the registry is a temp
file, the LLM client is a recorder, and nothing touches the network, a model
weight or Kuzu.
"""

import json
from pathlib import Path

import pytest

from app.config import Config
from app.models.assignment import (
    StepModelUnavailableError,
    resolve_report_model,
)
from app.services.ontology_generator import OntologyGenerator
from app.services.report_agent import ReportAgent

SMALL = "LFM3-1.7B-MLX-4bit"
BIG = "Ornith-1.5-9B-MLX-6bit"


class _RecordingLLMClient:
    """Stands in for ``LLMClient`` and records the model it was built with."""

    def __init__(self, *args, model=None, **kwargs):
        # same default as LLMClient: no explicit model -> Config.LLM_MODEL_NAME
        self.model = model or Config.LLM_MODEL_NAME
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return "Final Answer: routed answer"

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return {}


class _RecordingZepTools:
    """Stands in for ``ZepToolsService`` (no graph client, no key)."""

    def __init__(self, llm_client=None):
        self.llm_client = llm_client


def _write_registry(tmp_path, monkeypatch, *, roles, models=(SMALL, BIG)):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "models": {
            name: {
                "name": name,
                "repo_id": f"repo/{name}",
                "path": str(tmp_path / name),
                "backend": "mlx",
                "size_bytes": 1,
            }
            for name in models
        },
        "roles": roles,
    }), encoding="utf-8")
    monkeypatch.setattr(Config, "MODELS_REGISTRY_PATH", str(path))
    return path


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    """Registry with both roles assigned; the override defaults to empty."""

    _write_registry(tmp_path, monkeypatch, roles={"small": SMALL, "big": BIG})
    monkeypatch.setattr(Config, "REPORT_LLM_MODEL_NAME", "")
    monkeypatch.setattr(Config, "REPORT_LLM_ROLE", "big")
    return tmp_path / "registry.json"


def _report_agent(monkeypatch):
    """Build a ReportAgent with recorded collaborators (steps 4 and 5)."""

    monkeypatch.setattr("app.services.report_agent.LLMClient", _RecordingLLMClient)
    monkeypatch.setattr("app.services.report_agent.ZepToolsService", _RecordingZepTools)
    return ReportAgent(
        graph_id="graph_test",
        simulation_id="sim_test",
        simulation_requirement="requirement",
    )


# -- the resolver -----------------------------------------------------------
def test_report_step_model_resolves_the_assigned_big_role(registry):
    assert resolve_report_model() == BIG


def test_explicit_model_override_wins_over_the_role(registry, monkeypatch):
    monkeypatch.setattr(Config, "REPORT_LLM_MODEL_NAME", SMALL)

    assert resolve_report_model() == SMALL


def test_config_override_points_the_step_at_another_model(tmp_path, monkeypatch):
    """The owner's swappable-big-model path: another installed model, no code change."""

    _write_registry(tmp_path, monkeypatch,
                    roles={"small": SMALL, "big": None},
                    models=(SMALL, BIG, "Other-9B-MLX-6bit"))
    monkeypatch.setattr(Config, "REPORT_LLM_MODEL_NAME", "Other-9B-MLX-6bit")

    assert resolve_report_model() == "Other-9B-MLX-6bit"


# -- steps 4 and 5 construct their client on the big-role model -------------
def test_report_agent_constructs_its_client_on_the_big_role_model(registry, monkeypatch):
    agent = _report_agent(monkeypatch)

    assert agent.llm.model == BIG
    # the agent's own tools must not silently drop back to the small model
    assert agent.zep_tools.llm_client is agent.llm


def test_chat_endpoint_constructs_the_big_role_model(registry, tmp_path, monkeypatch):
    """Step 5 (Deep Interaction) goes through /api/report/chat."""

    from app import create_app
    from app.api import report as report_api
    from app.models import manager as manager_mod
    from app.models.project import ProjectStatus

    class _State:
        project_id = "proj_test"
        graph_id = "graph_test"

    class _Project:
        project_id = "proj_test"
        graph_id = "graph_test"
        status = ProjectStatus.GRAPH_COMPLETED
        simulation_requirement = "requirement"

    recorded = []
    monkeypatch.setattr("app.services.report_agent.LLMClient",
                        lambda *a, **k: recorded.append(k.get("model")) or _RecordingLLMClient(**k))
    monkeypatch.setattr("app.services.report_agent.ZepToolsService", _RecordingZepTools)
    monkeypatch.setattr(report_api.SimulationManager, "get_simulation", lambda self, _sid: _State())
    monkeypatch.setattr(report_api.ProjectManager, "get_project", lambda _pid: _Project())
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setattr(manager_mod, "_MANAGER", None)

    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    resp = client.post("/api/report/chat", json={
        "simulation_id": "sim_test",
        "message": "what happened?",
    })

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["success"] is True
    assert recorded == [BIG]


# -- unassigned role fails loudly, never falls back -------------------------
def test_unassigned_big_role_fails_with_an_actionable_error(tmp_path, monkeypatch):
    _write_registry(tmp_path, monkeypatch, roles={"small": SMALL, "big": None})

    with pytest.raises(StepModelUnavailableError) as excinfo:
        resolve_report_model()

    message = str(excinfo.value)
    assert "'big'" in message                      # names the role
    assert "Model Manager" in message              # names where to fix it
    assert "REPORT_LLM_MODEL_NAME" in message      # names the config escape hatch
    assert "issue #22" in message                  # names the decision behind it
    # never a silent downgrade to the small model
    assert SMALL not in message
    assert "No fallback to the 'small' role" in message


def test_missing_big_model_weights_fail_the_same_way(tmp_path, monkeypatch):
    """Role assigned to a model that is no longer installed is the same error."""

    _write_registry(tmp_path, monkeypatch, roles={"small": SMALL, "big": BIG},
                    models=(SMALL,))  # BIG deleted from the registry

    with pytest.raises(StepModelUnavailableError) as excinfo:
        resolve_report_model()

    assert "'big'" in str(excinfo.value)
    assert SMALL not in str(excinfo.value)


def test_unknown_override_fails_instead_of_silently_using_small(tmp_path, monkeypatch):
    _write_registry(tmp_path, monkeypatch, roles={"small": SMALL, "big": BIG})
    monkeypatch.setattr(Config, "REPORT_LLM_MODEL_NAME", "typo-9B")

    with pytest.raises(StepModelUnavailableError) as excinfo:
        resolve_report_model()

    message = str(excinfo.value)
    assert "typo-9B" in message
    assert "Model Manager" in message
    assert SMALL not in message


def test_report_agent_refuses_to_fall_back_to_the_small_model(tmp_path, monkeypatch):
    _write_registry(tmp_path, monkeypatch, roles={"small": SMALL, "big": None})
    monkeypatch.setattr("app.services.report_agent.LLMClient", _RecordingLLMClient)
    monkeypatch.setattr("app.services.report_agent.ZepToolsService", _RecordingZepTools)

    with pytest.raises(StepModelUnavailableError):
        ReportAgent(
            graph_id="graph_test",
            simulation_id="sim_test",
            simulation_requirement="requirement",
        )


def test_reported_step_keys_are_documented_in_env_example():
    """The owner's knobs stay next to the other LLM keys in ``.env.example``."""

    contents = (Path(__file__).resolve().parents[2] / ".env.example").read_text(
        encoding="utf-8")

    assert "REPORT_LLM_ROLE" in contents
    assert "REPORT_LLM_MODEL_NAME" in contents
    assert "MODEL_SMALL_ROLE" not in contents   # phantom keys do nothing


# -- steps 1–3 keep resolving exactly as they do today ----------------------
def test_small_role_steps_still_resolve_config_llm_model_name(monkeypatch):
    """Regression guard for steps 1–3: no role indirection on their path."""

    monkeypatch.setattr(Config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", SMALL)

    recorded = []
    monkeypatch.setattr(
        "app.services.ontology_generator.LLMClient",
        lambda *a, **k: recorded.append(k.get("model") or Config.LLM_MODEL_NAME) or object(),
    )
    OntologyGenerator()

    from app.services.oasis_profile_generator import OasisProfileGenerator
    from app.services.simulation_config_generator import SimulationConfigGenerator

    assert recorded == [SMALL]           # step 1 (ontology generation)
    assert OasisProfileGenerator().model_name == SMALL      # step 3 (simulation)
    assert SimulationConfigGenerator().model_name == SMALL  # step 3 (simulation)
