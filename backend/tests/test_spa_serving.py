# SPDX-License-Identifier: AGPL-3.0-or-later
"""§10 (#17): the backend serves the built SPA (frontend/dist) at :5001 with a
history-mode fallback, while /api, /v1 and /health stay on the API surface and
are never shadowed by the SPA catch-all.
"""

from types import SimpleNamespace

import pytest


@pytest.fixture()
def spa_env(tmp_path, monkeypatch):
    from app.config import Config
    from app.models import manager as manager_mod

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><title>Omnidroids</title></html>"
    )
    (dist / "assets" / "app-abc123.js").write_text("console.log('spa')")
    monkeypatch.setattr(Config, "FRONTEND_DIST_DIR", str(dist))
    monkeypatch.setattr(manager_mod, "_MANAGER", None)
    return SimpleNamespace(dist=dist)


@pytest.fixture()
def app_client(spa_env):
    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_served_at_root(app_client):
    resp = app_client.get("/")
    assert resp.status_code == 200
    assert b"Omnidroids" in resp.data
    assert resp.headers["Content-Type"].startswith("text/html")


def test_static_asset_served_from_dist(app_client):
    resp = app_client.get("/assets/app-abc123.js")
    assert resp.status_code == 200
    assert b"spa" in resp.data


def test_history_fallback_returns_index(app_client):
    resp = app_client.get("/monitor")
    assert resp.status_code == 200
    assert b"Omnidroids" in resp.data


def test_missing_spa_file_falls_back_to_index(app_client):
    resp = app_client.get("/assets/missing-hash.js")
    assert resp.status_code == 200
    assert b"Omnidroids" in resp.data


def test_api_route_is_not_shadowed_by_spa(app_client):
    resp = app_client.get("/api/graph/does-not-exist")
    assert b"Omnidroids" not in resp.data  # never the SPA shell
    assert resp.status_code in (400, 404, 405)


def test_health_endpoint_is_not_shadowed(app_client):
    resp = app_client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_v1_proxy_surface_is_not_shadowed(app_client):
    # GET /v1/models reaches the local-runtime proxy, not the SPA
    resp = app_client.get("/v1/models")
    assert b"Omnidroids" not in resp.data
