"""HTTP serving for session-local artifacts."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.studio.persistence import store as persistence_store

from tests.unit._persistence_test_helpers import mount_session_routes


@pytest.fixture()
def app_with_sessions(tmp_path: Path, monkeypatch):
    """Spin up a FastAPI app mounted at /api/sessions with a fresh
    sessions directory so the test doesn't touch ~/.kohakuterrarium."""
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)
    app = FastAPI()
    mount_session_routes(app)
    return app, tmp_path


def _seed_artifact(session_dir: Path, session_name: str, rel: str, data: bytes) -> Path:
    target = session_dir / f"{session_name}.artifacts" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def test_get_existing_artifact(app_with_sessions):
    app, tmp = app_with_sessions
    _seed_artifact(
        tmp, "agent_abc", "generated_images/cat.png", b"\x89PNG\r\n\x1a\n..."
    )
    client = TestClient(app)
    resp = client.get("/api/sessions/agent_abc/artifacts/generated_images/cat.png")
    assert resp.status_code == 200
    assert resp.content.startswith(b"\x89PNG")
    assert resp.headers["content-type"].startswith("image/png")


def test_missing_artifact_404(app_with_sessions):
    app, tmp = app_with_sessions
    # Create the artifacts dir but not the file.
    (tmp / "agent_abc.artifacts").mkdir()
    client = TestClient(app)
    resp = client.get("/api/sessions/agent_abc/artifacts/nope.png")
    assert resp.status_code == 404


def test_missing_session_404(app_with_sessions):
    app, tmp = app_with_sessions
    client = TestClient(app)
    resp = client.get("/api/sessions/does_not_exist/artifacts/any.png")
    assert resp.status_code == 404


def test_rejects_traversal(app_with_sessions):
    app, tmp = app_with_sessions
    _seed_artifact(tmp, "agent_abc", "ok.png", b"x")
    # Also seed a file outside the artifacts dir to catch traversal.
    outside = tmp / "secret.txt"
    outside.write_text("shhh", encoding="utf-8")
    client = TestClient(app)
    # Url-encoded traversal:
    resp = client.get("/api/sessions/agent_abc/artifacts/..%2Fsecret.txt")
    assert resp.status_code in (400, 404)
    # Literal .. inside filepath:
    resp = client.get("/api/sessions/agent_abc/artifacts/../secret.txt")
    # FastAPI normalizes some forms; we care that we don't return the secret:
    assert resp.status_code != 200 or b"shhh" not in resp.content


def test_rejects_absolute(app_with_sessions):
    app, tmp = app_with_sessions
    (tmp / "agent_abc.artifacts").mkdir()
    client = TestClient(app)
    # Windows absolute path — httpx / Starlette will URL-encode, but
    # our handler's check still applies.
    resp = client.get("/api/sessions/agent_abc/artifacts//etc/passwd")
    assert resp.status_code in (400, 404)
