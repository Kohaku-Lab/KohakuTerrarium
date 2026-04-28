"""V6 Session Viewer endpoints — export + diff.

Pins the response shape both surfaces depend on:

* ``GET /sessions/{name}/export?format=md|html|jsonl`` — the Overview
  "Export ▾" menu navigates to this URL; backend must stream a body
  with the right Content-Type and Content-Disposition.
* ``GET /sessions/{name}/diff?other=…`` — the Diff tab calls this with
  another session name and renders the structured payload.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import store as persistence_store

from tests.unit._persistence_test_helpers import mount_session_routes


@pytest.fixture()
def app_with_sessions(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)
    app = FastAPI()
    mount_session_routes(app)
    return app, tmp_path


def _seed(session_dir: Path, name: str, *, turns: int) -> Path:
    """Seed a tiny session with N text-only turns of (user, assistant)."""
    path = session_dir / f"{name}.kohakutr"
    store = SessionStore(path)
    store.init_meta(
        session_id=name,
        config_type="agent",
        config_path="",
        pwd=str(session_dir),
        agents=["root"],
    )
    for ti in range(1, turns + 1):
        store.append_event(
            "root",
            "user_input",
            {"content": f"q{ti}"},
            turn_index=ti,
        )
        store.append_event(
            "root",
            "user_message",
            {"content": f"q{ti}"},
            turn_index=ti,
        )
        store.append_event(
            "root",
            "text",
            {"content": f"a{ti}"},
            turn_index=ti,
        )
        store.append_event("root", "processing_end", {}, turn_index=ti)
    store.close(update_status=False)
    return path


# ─────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────


class TestExport:
    def test_markdown(self, app_with_sessions):
        app, tmp = app_with_sessions
        _seed(tmp, "alice", turns=3)
        client = TestClient(app)

        resp = client.get("/api/sessions/alice/export?format=md")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")
        assert 'attachment; filename="alice.md"' in resp.headers["content-disposition"]
        body = resp.text
        assert "# Session: alice" in body
        # All three turns surface.
        assert "q1" in body
        assert "q3" in body
        # System / Assistant labels render as markdown bold.
        assert "**Assistant:**" in body or "**User:**" in body

    def test_html(self, app_with_sessions):
        app, tmp = app_with_sessions
        _seed(tmp, "alice", turns=2)
        client = TestClient(app)

        resp = client.get("/api/sessions/alice/export?format=html")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "<!doctype html>" in resp.text
        # User content escaped through.
        assert "q1" in resp.text
        assert "q2" in resp.text

    def test_jsonl(self, app_with_sessions):
        app, tmp = app_with_sessions
        _seed(tmp, "alice", turns=2)
        client = TestClient(app)

        resp = client.get("/api/sessions/alice/export?format=jsonl")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/jsonl")
        # One JSON object per line — each parses cleanly.
        import json

        lines = [line for line in resp.text.split("\n") if line.strip()]
        assert lines, "expected at least one event line"
        for line in lines:
            parsed = json.loads(line)
            assert "type" in parsed
            assert parsed["agent"] == "root"

    def test_unsupported_format_400(self, app_with_sessions):
        app, tmp = app_with_sessions
        _seed(tmp, "alice", turns=1)
        client = TestClient(app)
        resp = client.get("/api/sessions/alice/export?format=xml")
        assert resp.status_code == 400

    def test_missing_session_404(self, app_with_sessions):
        app, _ = app_with_sessions
        client = TestClient(app)
        assert client.get("/api/sessions/nope/export?format=md").status_code == 404


# ─────────────────────────────────────────────────────────────────────
# Diff
# ─────────────────────────────────────────────────────────────────────


class TestDiff:
    def test_identical_sessions(self, app_with_sessions):
        app, tmp = app_with_sessions
        _seed(tmp, "alice", turns=3)
        _seed(tmp, "bob", turns=3)
        client = TestClient(app)

        data = client.get("/api/sessions/alice/diff?other=bob").json()
        # The seeded sessions share identical message text per turn so
        # the shared prefix should cover everything (system + 6 events).
        assert data["a"]["session_name"] == "alice"
        assert data["b"]["session_name"] == "bob"
        assert data["shared_prefix_length"] >= 1
        assert data["a_only"] == []
        assert data["b_only"] == []
        assert data["identical"] is True

    def test_divergent_sessions(self, app_with_sessions):
        app, tmp = app_with_sessions
        _seed(tmp, "alice", turns=3)
        _seed(tmp, "bob", turns=1)
        client = TestClient(app)

        data = client.get("/api/sessions/alice/diff?other=bob").json()
        # Alice has more turns — shared prefix is bob's length, alice
        # has trailing messages, bob has none.
        assert data["a_only"], "alice's extra turns must surface"
        assert data["b_only"] == []
        assert data["identical"] is False

    def test_other_session_missing_404(self, app_with_sessions):
        app, tmp = app_with_sessions
        _seed(tmp, "alice", turns=1)
        client = TestClient(app)
        resp = client.get("/api/sessions/alice/diff?other=ghost")
        assert resp.status_code == 404

    def test_self_session_missing_404(self, app_with_sessions):
        app, tmp = app_with_sessions
        _seed(tmp, "bob", turns=1)
        client = TestClient(app)
        resp = client.get("/api/sessions/ghost/diff?other=bob")
        assert resp.status_code == 404
