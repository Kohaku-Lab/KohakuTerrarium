"""Unit tests for :mod:`kohakuterrarium.api.routes.persistence.*`."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.persistence import saved as saved_mod


def _app(*routers) -> FastAPI:
    app = FastAPI()
    for r in routers:
        app.include_router(r, prefix="/saved")
    return app


# ── saved ────────────────────────────────────────────────────────


class TestPersistenceSaved:
    def test_disk_usage(self, monkeypatch):
        monkeypatch.setattr(saved_mod, "disk_usage", lambda: {"total_bytes": 1024})
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved/disk-usage")
        assert resp.status_code == 200
        assert resp.json() == {"total_bytes": 1024}

    def test_stats(self, monkeypatch):
        monkeypatch.setattr(saved_mod, "session_stats", lambda: {"count": 5})
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved/stats")
        assert resp.status_code == 200
        assert resp.json()["count"] == 5

    def test_list_sessions_basic(self, monkeypatch):
        monkeypatch.setattr(
            saved_mod,
            "get_session_index",
            lambda: [{"name": "a"}, {"name": "b"}, {"name": "c"}],
        )
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved")
        body = resp.json()
        assert body["total"] == 3
        assert body["limit"] == 20

    def test_list_sessions_pagination(self, monkeypatch):
        monkeypatch.setattr(
            saved_mod,
            "get_session_index",
            lambda: [{"name": f"s{i}"} for i in range(10)],
        )
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved?limit=3&offset=2")
        body = resp.json()
        assert len(body["sessions"]) == 3
        assert body["sessions"][0]["name"] == "s2"

    def test_list_sessions_search(self, monkeypatch):
        sessions = [
            {"name": "alice-session", "config_type": "agent"},
            {"name": "bob-session", "config_type": "terrarium"},
            {"name": "carol-session", "agents": ["dave"]},
        ]
        monkeypatch.setattr(saved_mod, "get_session_index", lambda: sessions)
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved?search=alice")
        names = [s["name"] for s in resp.json()["sessions"]]
        assert "alice-session" in names
        assert "bob-session" not in names

    def test_list_sessions_search_with_list_field(self, monkeypatch):
        sessions = [{"name": "x", "agents": ["alice", "bob"]}]
        monkeypatch.setattr(saved_mod, "get_session_index", lambda: sessions)
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved?search=alice")
        assert resp.json()["total"] == 1

    def test_search_coerces_none_dict_and_scalar_fields(self, monkeypatch):
        # The search haystack defensively coerces every metadata field:
        # None → "", dict → its space-joined values, scalars → str().
        # An entry where the match only lives in a dict-valued field
        # must still be found; a None field must not crash the coerce.
        sessions = [
            # match buried inside a dict-valued ``preview`` field; ``pwd``
            # is None (must coerce to "" without raising).
            {
                "name": None,
                "preview": {"block": "needle-in-dict"},
                "pwd": None,
            },
            # match in a scalar (int) field — coerced via str().
            {"name": "plain", "config_path": 12345},
        ]
        monkeypatch.setattr(saved_mod, "get_session_index", lambda: sessions)
        client = TestClient(_app(saved_mod.router))
        # The dict-valued preview is searchable.
        r1 = client.get("/saved?search=needle-in-dict")
        assert r1.json()["total"] == 1
        # The int config_path is coerced to a string and searchable.
        r2 = client.get("/saved?search=12345")
        assert r2.json()["total"] == 1

    def test_list_sessions_refresh(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            saved_mod, "build_session_index", lambda: called.append("built")
        )
        monkeypatch.setattr(saved_mod, "get_session_index", lambda: [])
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved?refresh=true")
        assert resp.status_code == 200
        assert called == ["built"]

    def test_delete_success(self, monkeypatch):
        from pathlib import Path

        monkeypatch.setattr(
            saved_mod,
            "delete_session_files",
            lambda n: [Path("/x/s.kohakutr"), Path("/x/s.kohakutr.v2")],
        )
        client = TestClient(_app(saved_mod.router))
        resp = client.delete("/saved/foo")
        assert resp.status_code == 200
        body = resp.json()
        assert "s.kohakutr" in body["files"]

    def test_delete_missing(self, monkeypatch):
        monkeypatch.setattr(saved_mod, "delete_session_files", lambda n: [])
        client = TestClient(_app(saved_mod.router))
        resp = client.delete("/saved/ghost")
        assert resp.status_code == 404

    def test_delete_http_exception_propagates(self, monkeypatch):
        def boom(n):
            raise HTTPException(404, "not allowed")

        monkeypatch.setattr(saved_mod, "delete_session_files", boom)
        client = TestClient(_app(saved_mod.router))
        resp = client.delete("/saved/foo")
        assert resp.status_code == 404

    def test_delete_internal_error_500(self, monkeypatch):
        def boom(n):
            raise RuntimeError("io error")

        monkeypatch.setattr(saved_mod, "delete_session_files", boom)
        client = TestClient(_app(saved_mod.router))
        resp = client.delete("/saved/foo")
        assert resp.status_code == 500
