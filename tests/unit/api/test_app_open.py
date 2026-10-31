"""``/api/app/open-session``: open a session file the desktop shell was given."""

import sqlite3

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from kohakuterrarium.api.routes import app_open


@pytest.fixture
def sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(app_open, "_session_dir", lambda: tmp_path / "sessions")
    return tmp_path / "sessions"


def _client(address="127.0.0.1"):
    app = FastAPI()
    app.include_router(app_open.router, prefix="/api/app")
    return TestClient(app, client=(address, 5000))


def _session_file(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE events (body TEXT)")
    connection.commit()
    connection.close()


def test_a_local_session_file_is_imported_and_named(sessions, tmp_path):
    source = tmp_path / "shared.kohakutr"
    _session_file(source)

    response = _client().post("/api/app/open-session", json={"path": str(source)})

    assert response.status_code == 200
    assert response.json() == {"session_name": "shared"}
    assert (sessions / "shared.kohakutr").is_file()


def test_other_machines_cannot_ask_the_server_to_read_its_files(sessions, tmp_path):
    source = tmp_path / "shared.kohakutr"
    _session_file(source)

    response = _client("203.0.113.9").post(
        "/api/app/open-session", json={"path": str(source)}
    )

    assert response.status_code == 403
    assert not sessions.exists()


def test_a_bad_file_is_a_client_error(sessions, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("x")

    response = _client().post("/api/app/open-session", json={"path": str(source)})

    assert response.status_code == 400
    assert "not a .kohakutr file" in response.json()["detail"]
