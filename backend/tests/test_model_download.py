# SPDX-License-Identifier: AGPL-3.0-or-later
"""B1 (#10): download API + job registry — behaviors, offline (mocked HF).

Covers the issue's done-criteria split into unit-level assertions:
  - preflight size check vs RAM budget rejects over-budget repos BEFORE any download
  - download confinement (repo_id shape + resolved local_dir under models/)
  - job lifecycle: queued -> running -> done, error state surfaced, cancel
  - progress plumbing: snapshot_download's tqdm-class bars feed job progress
  - completed downloads register in the ModelStore (+role when requested)
"""

import time
from types import SimpleNamespace

import pytest

from app import create_app


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated models dir + registry + singleton reset (offline: no network)."""
    from app.config import Config
    from app.models import downloader as dl
    from app.models import manager as manager_mod

    models_dir = tmp_path / "models"
    monkeypatch.setattr(Config, "MODELS_DIR", str(models_dir))
    monkeypatch.setattr(Config, "MODELS_REGISTRY_PATH", str(models_dir / "registry.json"))
    monkeypatch.setattr(Config, "HF_CACHE_DIR", str(tmp_path / "hf-cache"))
    monkeypatch.setattr(Config, "MODEL_RAM_BUDGET_GB", 11.0)
    # singletons must not leak state across tests
    monkeypatch.setattr(manager_mod, "_MANAGER", None)
    monkeypatch.setattr(dl, "_REGISTRY", None)
    return SimpleNamespace(models_dir=models_dir, downloader=dl)


@pytest.fixture()
def app_client(env):
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


# ---------------------------------------------------------------------------
# HF mocks
# ---------------------------------------------------------------------------
def _siblings(*specs):
    """specs: (filename, size_bytes)"""
    return [SimpleNamespace(rfilename=n, size=s) for n, s in specs]


def _fake_hfapi(monkeypatch, env, siblings=None, exc=None):
    calls = {}

    class FakeHfApi:
        def model_info(self, repo_id, **kwargs):
            calls.setdefault("model_info", []).append((repo_id, kwargs))
            if exc:
                raise exc
            info = SimpleNamespace(siblings=siblings or [])
            return info

    monkeypatch.setattr(env.downloader, "HfApi", FakeHfApi)
    return calls


def _fake_snapshot(monkeypatch, env, *, exc=None, delay=0.0, exercise_progress=False):
    calls = {}

    def fake_snapshot_download(repo_id, local_dir, tqdm_class=None, **kwargs):
        calls["args"] = {"repo_id": repo_id, "local_dir": local_dir, "tqdm_class": tqdm_class}
        import os
        os.makedirs(local_dir, exist_ok=True)
        if exercise_progress and tqdm_class is not None:
            # mimic huggingface_hub: byte bars (unit="B") get total/initial +
            # update(n); the "Fetching N files" bar is unit="it".
            with tqdm_class(total=1000, initial=0, desc="Downloading bytes",
                            unit="B", unit_scale=True, bar_format=None) as transfer:
                transfer.update(400)
            with tqdm_class(total=1000, initial=0, desc="Reconstructing (incomplete total...)",
                            unit="B", unit_scale=True, bar_format=None) as reconstruct:
                reconstruct.update(400)
            with tqdm_class(total=2, initial=0, desc="Fetching 2 files",
                            unit="it") as fetch:
                fetch.update(1)
        if delay:
            time.sleep(delay)
        if exc:
            raise exc
        # a real snapshot_download writes the checkpoint layout into local_dir
        import os
        with open(os.path.join(local_dir, "config.json"), "w") as f:
            f.write('{"model_type": "lfm3"}')
        with open(os.path.join(local_dir, "model.safetensors"), "wb") as f:
            f.write(b"\x00" * 16)
        return local_dir

    monkeypatch.setattr(env.downloader, "snapshot_download", fake_snapshot_download)
    return calls


# ---------------------------------------------------------------------------
# preflight: size gate + confinement (before any download starts)
# ---------------------------------------------------------------------------
def test_over_budget_repo_is_rejected_before_download(env, app_client, monkeypatch):
    calls = _fake_hfapi(monkeypatch, env,
                        siblings=_siblings(("model.safetensors", 20 * 1024**3), ("config.json", 1024)))
    snap = _fake_snapshot(monkeypatch, env)

    resp = app_client.post("/api/models/download", json={"repo_id": "some/org-huge"})

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == 400
    assert "budget" in body["message"].lower()
    assert "snapshot_download" not in snap  # never started
    assert env.downloader.get_download_registry().list() == []


def test_within_budget_repo_passes_preflight(env, app_client, monkeypatch):
    _fake_hfapi(monkeypatch, env, siblings=_siblings(("model.safetensors", 1024**3)))
    _fake_snapshot(monkeypatch, env)

    resp = app_client.post("/api/models/download", json={"repo_id": "org/model-small"})

    assert resp.status_code == 202
    job_id = resp.get_json()["data"]["job_id"]
    job = _wait_for_terminal(env.downloader.get_download_registry(), job_id)
    assert job["status"] == "done"


@pytest.mark.parametrize("bad", ["/abs/path", "../../escape", "a/b/c", "..", "org//model", ""])
def test_repo_id_confinement_rejections(env, app_client, monkeypatch, bad):
    _fake_hfapi(monkeypatch, env, siblings=_siblings(("model.safetensors", 1024)))
    snap = _fake_snapshot(monkeypatch, env)

    resp = app_client.post("/api/models/download", json={"repo_id": bad})

    assert resp.status_code == 400
    assert "snapshot_download" not in snap


def test_unknown_repo_preflight_error_surfaced(env, app_client, monkeypatch):
    _fake_hfapi(monkeypatch, env, exc=RuntimeError("Repository Not Found"))
    snap = _fake_snapshot(monkeypatch, env)

    resp = app_client.post("/api/models/download", json={"repo_id": "org/nope"})

    assert resp.status_code == 400
    assert "Not Found" in resp.get_json()["message"]
    assert "snapshot_download" not in snap


# ---------------------------------------------------------------------------
# job lifecycle
# ---------------------------------------------------------------------------
def test_completed_download_registers_model_with_role(env, app_client, monkeypatch):
    _fake_hfapi(monkeypatch, env, siblings=_siblings(("model.safetensors", 1024**3)))
    _fake_snapshot(monkeypatch, env)

    resp = app_client.post("/api/models/download",
                           json={"repo_id": "org/model-x", "role": "big"})
    assert resp.status_code == 202
    job_id = resp.get_json()["data"]["job_id"]
    job = _wait_for_terminal(env.downloader.get_download_registry(), job_id)

    assert job["status"] == "done"
    store = env.downloader.get_download_registry()._store
    entry = store.get_model("model-x")
    assert entry is not None
    assert entry["repo_id"] == "org/model-x"
    assert entry["path"].startswith(str(env.models_dir))
    assert entry["backend"] == "mlx"  # config+safetensors auto-detected
    assert store.get_roles()["big"] == "model-x"


def test_progress_plumbing_from_tqdm_bars(env, app_client, monkeypatch):
    _fake_hfapi(monkeypatch, env, siblings=_siblings(("model.safetensors", 1000)))
    _fake_snapshot(monkeypatch, env, delay=1.0, exercise_progress=True)

    resp = app_client.post("/api/models/download", json={"repo_id": "org/progress"})
    job_id = resp.get_json()["data"]["job_id"]

    # poll while running: progress fields exist and advance
    saw_progress = False
    deadline = time.time() + 10
    while time.time() < deadline:
        body = app_client.get(f"/api/models/downloads/{job_id}").get_json()["data"]
        assert {"status", "bytes_done", "bytes_total", "percent", "speed_mb_s", "eta_s"} <= set(body)
        if body["bytes_done"] > 0:
            saw_progress = True
            assert body["bytes_total"] >= body["bytes_done"]
            assert 0 <= body["percent"] <= 100
        if body["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.1)
    assert saw_progress, "progress callbacks never reached the job"
    assert body["status"] == "done"


def test_download_error_is_surfaced(env, app_client, monkeypatch):
    _fake_hfapi(monkeypatch, env, siblings=_siblings(("model.safetensors", 1024)))
    _fake_snapshot(monkeypatch, env, exc=RuntimeError("connection reset"))

    resp = app_client.post("/api/models/download", json={"repo_id": "org/flaky"})
    job_id = resp.get_json()["data"]["job_id"]
    job = _wait_for_terminal(env.downloader.get_download_registry(), job_id)

    assert job["status"] == "error"
    assert "connection reset" in job["error"]
    assert env.downloader.get_download_registry()._store.get_model("flaky") is None  # not registered


def test_cancel_running_download(env, app_client, monkeypatch):
    _fake_hfapi(monkeypatch, env, siblings=_siblings(("model.safetensors", 1024)))
    _fake_snapshot(monkeypatch, env, delay=2.0)

    resp = app_client.post("/api/models/download", json={"repo_id": "org/slow", "role": "small"})
    job_id = resp.get_json()["data"]["job_id"]

    cancel = app_client.post(f"/api/models/downloads/{job_id}/cancel")
    assert cancel.status_code == 200

    job = _wait_for_terminal(env.downloader.get_download_registry(), job_id)
    assert job["status"] == "cancelled"
    assert env.downloader.get_download_registry()._store.get_model("slow") is None


def test_duplicate_download_rejected(env, app_client, monkeypatch):
    _fake_hfapi(monkeypatch, env, siblings=_siblings(("model.safetensors", 1024)))
    _fake_snapshot(monkeypatch, env, delay=1.5)

    first = app_client.post("/api/models/download", json={"repo_id": "org/dup"})
    assert first.status_code == 202
    second = app_client.post("/api/models/download", json={"repo_id": "org/dup"})

    assert second.status_code == 409
    assert "already" in second.get_json()["message"].lower()


def test_job_listing(env, app_client, monkeypatch):
    _fake_hfapi(monkeypatch, env, siblings=_siblings(("model.safetensors", 1024)))
    _fake_snapshot(monkeypatch, env)
    app_client.post("/api/models/download", json={"repo_id": "org/one"})
    app_client.post("/api/models/download", json={"repo_id": "org/two"})

    listing = app_client.get("/api/models/downloads").get_json()["data"]["jobs"]
    assert {j["repo_id"] for j in listing} == {"org/one", "org/two"}


def test_unknown_job_is_404(env, app_client):
    resp = app_client.get("/api/models/downloads/nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
def _wait_for_terminal(registry, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = registry.get(job_id)
        if job and job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} never reached a terminal state: {registry.get(job_id)}")
