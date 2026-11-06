# SPDX-License-Identifier: AGPL-3.0-or-later
"""B3 (#12): DELETE /api/models/<name> — remove weights from disk + registry.

The Models page's per-model delete action. Deletes the local model folder
(confined under models/) and the registry entry (clearing any role that pointed
at it). Refuses while the model is loaded in an active phase (unload first).
"""

from types import SimpleNamespace

import pytest

from app import create_app


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from app.config import Config
    from app.models import manager as manager_mod

    models_dir = tmp_path / "models"
    monkeypatch.setattr(Config, "MODELS_DIR", str(models_dir))
    monkeypatch.setattr(Config, "MODELS_REGISTRY_PATH", str(models_dir / "registry.json"))
    monkeypatch.setattr(manager_mod, "_MANAGER", None)
    return SimpleNamespace(models_dir=models_dir)


@pytest.fixture()
def app_client(env):
    from app.models.store import ModelStore
    from app.models.manager import get_model_manager
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _add_model(env, name="org-model-x", *, dir_name="model-x", role=None):
    from app.models.store import ModelStore
    from app.models.manager import get_model_manager
    path = env.models_dir / dir_name
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text('{"model_type": "lfm3"}')
    (path / "model.safetensors").write_bytes(b"\x00" * 32)
    store = get_model_manager().store
    store.add_model(name=name, repo_id="org/" + dir_name, path=str(path), backend="mlx")
    if role:
        store.set_role(role, name)
    return path


def test_delete_removes_folder_and_registry_entry(env, app_client):
    _add_model(env, "org-model-x", dir_name="model-x", role="big")

    resp = app_client.delete("/api/models/org-model-x")

    assert resp.status_code == 200
    assert resp.get_json()["code"] == 0
    assert not (env.models_dir / "model-x").exists()          # weights gone
    from app.models.manager import get_model_manager
    store = get_model_manager().store
    assert store.get_model("org-model-x") is None            # registry entry gone
    assert store.get_roles()["big"] is None                  # role cleared


def test_delete_unknown_model_is_404(env, app_client):
    resp = app_client.delete("/api/models/never-installed")
    assert resp.status_code == 404
    assert resp.get_json()["code"] == 404


def test_delete_refuses_while_loaded(env, app_client, monkeypatch):
    path = _add_model(env, "org-model-x", dir_name="model-x")
    from app.models import manager as manager_mod
    from app.models.runtime import GenResult

    mgr = manager_mod.get_model_manager()

    class StubRunner:
        backend = "mlx"
        def __init__(self): self._loaded = False
        async def load(self): self._loaded = True
        async def unload(self): self._loaded = False
        async def generate(self, **kw): return GenResult(content="ok")
        def memory_footprint_mb(self): return 1

    monkeypatch.setattr(manager_mod, "make_runner", lambda *a, **k: StubRunner())
    with mgr.phase(model="org-model-x"):
        resp = app_client.delete("/api/models/org-model-x")
        assert resp.status_code == 409
        assert "loaded" in resp.get_json()["message"].lower()

    # after the phase unloaded it, delete succeeds
    resp = app_client.delete("/api/models/org-model-x")
    assert resp.status_code == 200
    assert not (env.models_dir / "model-x").exists()
