"""Phase 0 smoke — the studio router is reachable and meta works.

These tests don't need a workspace; the meta endpoints are
pure-introspection.
"""

from fastapi.testclient import TestClient

from kohakuterrarium.api.studio import build_studio_router


def test_build_studio_router_is_importable():
    r = build_studio_router()
    paths = {route.path for route in r.routes}
    assert "/api/studio/meta/health" in paths
    assert "/api/studio/meta/version" in paths


def test_health_returns_ok(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/meta/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_version_has_studio_and_core_keys(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/meta/version")
    assert resp.status_code == 200
    body = resp.json()
    assert "studio" in body
    assert "core" in body
    assert isinstance(body["studio"], str)


def test_mounted_on_core_app():
    """The full core app (create_app) must expose studio routes.

    Guards touch point T1 — if api/app.py stops including the
    studio router this test goes red.
    """
    from kohakuterrarium.api.app import create_app

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/studio/meta/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
