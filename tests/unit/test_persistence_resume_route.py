"""Pin: ``POST /api/sessions/{name}/resume`` returns the legacy shape.

The frontend (``pages/sessions/index.vue:196``) reads
``result.instance_id`` from the resume response and uses it to redirect
to ``/instances/{instance_id}``.  api.js:399 documents the contract as
``{instance_id, type, session_name}``.

Before this fix the route returned ``asdict(Session)`` which exposes
``session_id`` / ``kind`` / ``name`` — different field names — so
every resume from the saved-sessions list redirected to
``/instances/undefined``.  This test pins the legacy shape so future
cleanups don't regress it again.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.routes.persistence import resume as resume_route
from kohakuterrarium.studio.persistence import resume as studio_resume_mod
from kohakuterrarium.studio.persistence import store as persistence_store
from kohakuterrarium.studio.sessions.handles import Session


@pytest.fixture
def fake_session_path(tmp_path, monkeypatch) -> Path:
    """Drop a sentinel ``foo.kohakutr`` so the route's path resolver hits it."""
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)
    target = tmp_path / "foo.kohakutr"
    target.write_bytes(b"sentinel")
    return target


def _make_client(monkeypatch, *, returned_session: Session) -> TestClient:
    """Wire the resume route on a test app with ``studio_resume`` stubbed."""

    async def _fake_resume(_engine, _path, **_kwargs):
        return returned_session

    monkeypatch.setattr(resume_route, "studio_resume", _fake_resume)

    app = FastAPI()
    app.include_router(resume_route.router, prefix="/api/sessions")
    app.dependency_overrides[get_engine] = lambda: SimpleNamespace()
    return TestClient(app)


def test_resume_returns_legacy_shape_for_creature(monkeypatch, fake_session_path):
    """A resumed 1-creature session returns ``type == "agent"``."""
    session = Session(
        session_id="alice_abc123",
        kind="creature",
        name="alice",
        creatures=[{"agent_id": "alice_abc123", "name": "alice"}],
        created_at="2025-01-01T00:00:00Z",
        config_path="alice.yaml",
        pwd="/tmp/work",
    )
    client = _make_client(monkeypatch, returned_session=session)

    response = client.post("/api/sessions/foo/resume")
    assert response.status_code == 200
    body = response.json()
    # Legacy contract — what api.js + the resume page consume.
    assert body["instance_id"] == "alice_abc123"
    assert body["type"] == "agent"
    assert body["session_name"] == "alice"
    # And the full handle is still reachable for new callers.
    assert body["session"]["session_id"] == "alice_abc123"
    assert body["session"]["kind"] == "creature"


def test_resume_returns_legacy_shape_for_terrarium(monkeypatch, fake_session_path):
    """A resumed terrarium session returns ``type == "terrarium"``."""
    session = Session(
        session_id="graph_xyz",
        kind="terrarium",
        name="my-tank",
        creatures=[],
        channels=[],
        has_root=True,
    )
    client = _make_client(monkeypatch, returned_session=session)

    response = client.post("/api/sessions/foo/resume")
    assert response.status_code == 200
    body = response.json()
    assert body["instance_id"] == "graph_xyz"
    assert body["type"] == "terrarium"
    assert body["session_name"] == "my-tank"
    assert body["session"]["has_root"] is True


def test_resume_404_when_path_unknown(monkeypatch, tmp_path):
    """The route 404s when the session path can't be resolved."""
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)

    app = FastAPI()
    app.include_router(resume_route.router, prefix="/api/sessions")
    app.dependency_overrides[get_engine] = lambda: SimpleNamespace()
    client = TestClient(app)

    response = client.post("/api/sessions/missing/resume")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_resume_400_on_value_error(monkeypatch, fake_session_path):
    """Studio resume's ``ValueError`` becomes 400."""

    async def _fake_resume(_engine, _path, **_kwargs):
        raise ValueError("session corrupted")

    monkeypatch.setattr(resume_route, "studio_resume", _fake_resume)

    app = FastAPI()
    app.include_router(resume_route.router, prefix="/api/sessions")
    app.dependency_overrides[get_engine] = lambda: SimpleNamespace()
    client = TestClient(app)

    response = client.post("/api/sessions/foo/resume")
    assert response.status_code == 400
    assert "session corrupted" in response.json()["detail"]


def test_studio_resume_signature_is_callable():
    """Sanity: ``studio.persistence.resume.resume_session`` is the
    coroutine the route imports under the alias ``studio_resume``."""
    assert resume_route.studio_resume is studio_resume_mod.resume_session
