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
        # Route now delegates to ``_stats_via_index`` which reads the
        # sidecar — stub it so the route plumbing is exercised in
        # isolation from KohakuVault.
        monkeypatch.setattr(saved_mod, "_stats_via_index", lambda: {"count": 5})
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved/stats")
        assert resp.status_code == 200
        assert resp.json()["count"] == 5

    def test_list_sessions_basic(self, monkeypatch):
        # Route delegates to ``_list_via_index`` which hits the
        # SessionIndex sidecar — stub it directly so we exercise the
        # route plumbing (executor offload + param marshalling)
        # without spinning up KohakuVault for every test.
        captured = {}

        def fake(**kw):
            captured.update(kw)
            return {
                "sessions": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
                "total": 3,
                "offset": kw["offset"],
                "limit": kw["limit"],
            }

        monkeypatch.setattr(saved_mod, "_list_via_index", fake)
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved")
        body = resp.json()
        assert body["total"] == 3
        assert body["limit"] == 20
        # Defaults — sort key, ordering, no refresh.
        assert captured["sort"] == "last_active"
        assert captured["order"] == "desc"
        assert captured["refresh"] is False

    def test_list_sessions_pagination(self, monkeypatch):
        def fake(**kw):
            offset, limit = kw["offset"], kw["limit"]
            assert offset == 2 and limit == 3
            return {
                "sessions": [{"name": f"s{i}"} for i in range(2, 5)],
                "total": 10,
                "offset": offset,
                "limit": limit,
            }

        monkeypatch.setattr(saved_mod, "_list_via_index", fake)
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved?limit=3&offset=2")
        body = resp.json()
        assert len(body["sessions"]) == 3
        assert body["sessions"][0]["name"] == "s2"

    def test_list_sessions_search_forwards_query(self, monkeypatch):
        # The route should forward ``search`` verbatim; semantic
        # matching is the SessionIndex's job (covered by its own
        # unit tests).  This test pins the wire forwarding only.
        captured = {}

        def fake(**kw):
            captured.update(kw)
            return {"sessions": [], "total": 0, "offset": 0, "limit": 20}

        monkeypatch.setattr(saved_mod, "_list_via_index", fake)
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved?search=alice")
        assert resp.status_code == 200
        assert captured["search"] == "alice"

    def test_list_sessions_facets_forwarded(self, monkeypatch):
        # New query params (``status``, ``config_type``, ``node_id``,
        # ``sort``, ``order``, ``full_rescan``) must reach the index
        # layer untouched.
        captured = {}

        def fake(**kw):
            captured.update(kw)
            return {"sessions": [], "total": 0, "offset": 0, "limit": 20}

        monkeypatch.setattr(saved_mod, "_list_via_index", fake)
        client = TestClient(_app(saved_mod.router))
        resp = client.get(
            "/saved?status=running&config_type=terrarium&node_id=worker-1"
            "&sort=name&order=asc&full_rescan=true"
        )
        assert resp.status_code == 200
        assert captured["status"] == "running"
        assert captured["config_type"] == "terrarium"
        assert captured["node_id"] == "worker-1"
        assert captured["sort"] == "name"
        assert captured["order"] == "asc"
        assert captured["full_rescan"] is True

    def test_list_sessions_refresh(self, monkeypatch):
        # ``refresh=true`` must propagate to the index layer; the
        # SessionIndex unit tests verify it triggers reconcile.
        captured = {}

        def fake(**kw):
            captured.update(kw)
            return {"sessions": [], "total": 0, "offset": 0, "limit": 20}

        monkeypatch.setattr(saved_mod, "_list_via_index", fake)
        client = TestClient(_app(saved_mod.router))
        resp = client.get("/saved?refresh=true")
        assert resp.status_code == 200
        assert captured["refresh"] is True
        assert captured["full_rescan"] is False

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
