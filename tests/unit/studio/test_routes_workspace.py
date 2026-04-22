"""Workspace route tests."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.studio import build_studio_router
from kohakuterrarium.api.studio.deps import set_workspace


def _fresh_client() -> TestClient:
    set_workspace(None)
    app = FastAPI()
    app.include_router(build_studio_router())
    return TestClient(app)


def test_get_without_workspace_returns_409():
    c = _fresh_client()
    resp = c.get("/api/studio/workspace")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "no_workspace"


def test_open_workspace(tmp_workspace: Path):
    c = _fresh_client()
    resp = c.post("/api/studio/workspace/open", json={"path": str(tmp_workspace)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["root"] == str(tmp_workspace.resolve())
    assert body["creatures"] == []
    set_workspace(None)


def test_open_nonexistent_returns_400(tmp_path: Path):
    c = _fresh_client()
    resp = c.post(
        "/api/studio/workspace/open", json={"path": str(tmp_path / "missing")}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "not_found"


def test_open_then_get(tmp_workspace: Path):
    c = _fresh_client()
    c.post("/api/studio/workspace/open", json={"path": str(tmp_workspace)})
    resp = c.get("/api/studio/workspace")
    assert resp.status_code == 200
    body = resp.json()
    assert "modules" in body
    assert "creatures" in body
    set_workspace(None)


def test_close_workspace(tmp_workspace: Path):
    c = _fresh_client()
    c.post("/api/studio/workspace/open", json={"path": str(tmp_workspace)})
    resp = c.post("/api/studio/workspace/close")
    assert resp.status_code == 204
    resp2 = c.get("/api/studio/workspace")
    assert resp2.status_code == 409
