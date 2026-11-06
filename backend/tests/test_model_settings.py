# SPDX-License-Identifier: AGPL-3.0-or-later
"""B2 (#11): Model Manager settings — round-trip API, persistence to
models/settings.json (git-ignored), validation, and no-restart wiring into the
manager's budget / concurrency / idle behavior.
"""

import json
import os
from types import SimpleNamespace

import pytest

from app import create_app


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from app.config import Config
    from app.models import manager as manager_mod
    from app.models import settings as settings_mod

    models_dir = tmp_path / "models"
    monkeypatch.setattr(Config, "MODELS_DIR", str(models_dir))
    monkeypatch.setattr(Config, "MODELS_REGISTRY_PATH", str(models_dir / "registry.json"))
    monkeypatch.setattr(Config, "MODEL_SETTINGS_PATH", str(models_dir / "settings.json"))
    monkeypatch.setattr(Config, "MODEL_RAM_BUDGET_GB", 11.0)
    monkeypatch.setattr(Config, "MODEL_IDLE_UNLOAD_SECONDS", 120)
    monkeypatch.setattr(manager_mod, "_MANAGER", None)
    monkeypatch.setattr(settings_mod, "_SETTINGS", None)
    return SimpleNamespace(models_dir=models_dir, settings_mod=settings_mod,
                           manager_mod=manager_mod)


@pytest.fixture()
def app_client(env):
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


# ---------------------------------------------------------------------------
# defaults + round-trip
# ---------------------------------------------------------------------------
def test_defaults_read_from_config(env):
    from app.models.settings import get_settings
    s = get_settings()
    assert s.get() == {"concurrency": 4, "ram_budget_gb": 11.0, "idle_unload_seconds": 120}


def test_settings_round_trip_through_api(env, app_client):
    resp = app_client.put("/api/models/settings", json={
        "concurrency": 2, "ram_budget_gb": 8.5, "idle_unload_seconds": 45,
    })
    assert resp.status_code == 200
    assert resp.get_json()["code"] == 0
    data = resp.get_json()["data"]
    assert data == {"concurrency": 2, "ram_budget_gb": 8.5, "idle_unload_seconds": 45}

    # persisted to models/settings.json (git-ignored, machine-local)
    saved = json.loads((env.models_dir / "settings.json").read_text())
    assert saved == data

    # GET reflects the persisted values
    got = app_client.get("/api/models/settings").get_json()["data"]
    assert got == data


def test_partial_update_preserves_other_keys(env, app_client):
    app_client.put("/api/models/settings", json={"concurrency": 3})
    resp = app_client.put("/api/models/settings", json={"ram_budget_gb": 9})
    data = resp.get_json()["data"]
    assert data == {"concurrency": 3, "ram_budget_gb": 9.0, "idle_unload_seconds": 120}


@pytest.mark.parametrize("bad", [
    {"concurrency": 0}, {"concurrency": -1}, {"concurrency": 99},
    {"ram_budget_gb": -1}, {"ram_budget_gb": 0}, {"ram_budget_gb": 999},
    {"idle_unload_seconds": 0}, {"idle_unload_seconds": -5},
    {"unknown_key": 5},
])
def test_invalid_values_rejected_without_writing(env, app_client, bad):
    app_client.put("/api/models/settings", json={"concurrency": 4})  # known-good state
    resp = app_client.put("/api/models/settings", json=bad)
    assert resp.status_code == 400
    assert resp.get_json()["code"] == 400
    # the previous good values survive untouched
    assert app_client.get("/api/models/settings").get_json()["data"] == {
        "concurrency": 4, "ram_budget_gb": 11.0, "idle_unload_seconds": 120}


# ---------------------------------------------------------------------------
# manager wiring (no restart)
# ---------------------------------------------------------------------------
def test_manager_status_reports_effective_settings(env, app_client):
    app_client.put("/api/models/settings", json={"concurrency": 5, "ram_budget_gb": 7})
    status = app_client.get("/api/models/status").get_json()["data"]
    assert status["ram_budget_mb"] == int(7 * 1024)
    assert status["concurrency"] == 5
    assert status["idle_unload_seconds"] == 120


def test_manager_uses_settings_budget_at_acquire_time(env, app_client):
    from app.models.manager import get_model_manager
    app_client.put("/api/models/settings", json={"ram_budget_gb": 3})
    mgr = get_model_manager()
    s = getattr(mgr, "_settings")
    assert s.get()["ram_budget_gb"] == 3.0
    # the limit helper (pure) reflects the live setting
    violation = mgr._limit_violation(other_loaded=0, est_mb=int(3.5 * 1024), settings=s.get())
    assert violation and "budget" in violation.lower()


def test_limit_violation_budget_and_concurrency(env, app_client):
    from app.models.manager import get_model_manager
    mgr = get_model_manager()
    s = mgr._settings.get()

    # budget: fine on its own, over when it alone exceeds
    assert mgr._limit_violation(other_loaded=0, est_mb=100, settings=s) is None
    assert "budget" in mgr._limit_violation(other_loaded=0, est_mb=99_999, settings=s).lower()

    # concurrency: with 4 allowed, 3 others loaded leaves room; 4 others do not
    assert mgr._limit_violation(other_loaded=3, est_mb=100, settings=s) is None
    msg = mgr._limit_violation(other_loaded=4, est_mb=100, settings=s)
    assert msg and "concurrency" in msg.lower()


def test_idle_seconds_live_read(env, app_client):
    from app.models.manager import get_model_manager
    mgr = get_model_manager()
    assert mgr._idle_seconds_now() == 120
    app_client.put("/api/models/settings", json={"idle_unload_seconds": 30})
    assert mgr._idle_seconds_now() == 30  # no restart needed
