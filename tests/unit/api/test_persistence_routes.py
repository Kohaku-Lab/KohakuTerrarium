"""Unit tests for :mod:`kohakuterrarium.api.routes.persistence.*`."""

import threading
import time

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


class TestReconcileSingleFlight:
    """``refresh=true`` bursts collapse onto one directory scan."""

    class _FakePage:
        def to_dict(self):
            return {"sessions": [], "total": 0}

    class _FakeIndex:
        def list(self, **kw):
            return TestReconcileSingleFlight._FakePage()

    @staticmethod
    def _install(monkeypatch, tmp_path, calls, block=None):
        """Point the module at a fake index/dir and a recording reconcile."""
        monkeypatch.setattr(saved_mod, "_session_dir", lambda: tmp_path)
        monkeypatch.setattr(
            saved_mod,
            "get_session_index_default",
            lambda d: TestReconcileSingleFlight._FakeIndex(),
        )

        def fake_reconcile(index, session_dir, full=False):
            calls.append({"full": full})
            if block is not None:
                block.wait(timeout=5)

        monkeypatch.setattr(saved_mod, "reconcile", fake_reconcile)
        # Fresh per-directory state (tmp_path is unique per test anyway).
        saved_mod._RECONCILE_STATE.clear()

    def _call(self, **params):
        kw = dict(
            search="",
            sort="last_active",
            order="desc",
            status=None,
            config_type=None,
            node_id=None,
            limit=20,
            offset=0,
            refresh=False,
            full_rescan=False,
        )
        kw.update(params)
        return saved_mod._list_via_index(**kw)

    def test_concurrent_refreshes_share_one_scan(self, monkeypatch, tmp_path):
        calls = []
        release = threading.Event()
        self._install(monkeypatch, tmp_path, calls, block=release)

        def refresh_once():
            self._call(refresh=True)

        first = threading.Thread(target=refresh_once)
        first.start()
        # Wait until the first caller is inside the (blocked) reconcile.
        deadline = 5.0
        while not calls and deadline > 0:
            time.sleep(0.01)
            deadline -= 0.01
        assert calls, "first refresh never reached reconcile"

        # Both later callers queue on the per-directory lock while the
        # first scan is still running, then fall inside the dedupe window.
        others = [threading.Thread(target=refresh_once) for _ in range(2)]
        for t in others:
            t.start()
        # Best-effort only — lets the queued callers reach the lock while
        # the first scan runs. Not load-bearing: an arrival that lands
        # after the scan still falls inside the dedupe window and skips.
        time.sleep(0.2)
        release.set()
        first.join(timeout=5)
        for t in others:
            t.join(timeout=5)

        assert len(calls) == 1, "concurrent refreshes must share one scan"

    def test_sequential_refresh_outside_window_rescans(self, monkeypatch, tmp_path):
        calls = []
        self._install(monkeypatch, tmp_path, calls)
        fake_now = [1000.0]
        monkeypatch.setattr(saved_mod, "_monotonic", lambda: fake_now[0])

        self._call(refresh=True)
        assert len(calls) == 1

        # Inside the dedupe window: reuse the fresh scan.
        fake_now[0] += 0.5
        self._call(refresh=True)
        assert len(calls) == 1

        # Outside the window: a real rescan runs.
        fake_now[0] += 5.0
        self._call(refresh=True)
        assert len(calls) == 2

    def test_full_rescan_bypasses_dedupe_window(self, monkeypatch, tmp_path):
        calls = []
        self._install(monkeypatch, tmp_path, calls)
        fake_now = [1000.0]
        monkeypatch.setattr(saved_mod, "_monotonic", lambda: fake_now[0])

        self._call(refresh=True)
        fake_now[0] += 0.1
        # Explicit reread-everything intent is never short-circuited by
        # a recent incremental scan.
        self._call(refresh=True, full_rescan=True)
        assert len(calls) == 2
        assert calls[1]["full"] is True

    def test_no_refresh_never_reconciles(self, monkeypatch, tmp_path):
        calls = []
        self._install(monkeypatch, tmp_path, calls)
        self._call()
        self._call(search="alice")
        assert calls == []
