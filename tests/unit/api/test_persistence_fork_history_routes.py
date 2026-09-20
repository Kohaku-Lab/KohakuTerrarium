"""Unit tests for the persistence fork + history routes."""

import asyncio
import time
import types
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.persistence import fork as fork_mod
from kohakuterrarium.api.routes.persistence import history as history_mod
from kohakuterrarium.session.store import SessionStore


def _app(router) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


class _FakeAgent:
    def __init__(self, job_ids):
        self._direct_job_meta = {jid: {} for jid in job_ids}


class _FakeCreature:
    def __init__(self, agent):
        self.agent = agent


class _FakeGraph:
    def __init__(self, creature_ids):
        self.creature_ids = list(creature_ids)


class _FakeEngine:
    """Minimal host engine: not a ``TerrariumService`` Protocol instance,
    so ``host_engine_or_none`` treats it as the engine directly."""

    def __init__(self, graph, creatures):
        self._graph = graph
        self._creatures = creatures

    def get_graph(self, session_name):
        return self._graph

    def get_creature(self, creature_id):
        return self._creatures[creature_id]


# ── fork ────────────────────────────────────────────────────────


class TestForkRoute:
    def test_session_missing(self, monkeypatch):
        monkeypatch.setattr(fork_mod, "resolve_session_path_default", lambda n: None)
        client = TestClient(_app(fork_mod.router))
        resp = client.post(
            "/api/ghost/fork",
            json={"at_event_id": 5},
        )
        assert resp.status_code == 404

    def test_success(self, monkeypatch):
        monkeypatch.setattr(
            fork_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        async def fake_fork(path, **kwargs):
            return {
                "session_id": "s-fork-1",
                "fork_point": kwargs["at_event_id"],
                "path": "/x/s-fork-1.kohakutr.v2",
            }

        monkeypatch.setattr(fork_mod, "fork_session_handler", fake_fork)
        client = TestClient(_app(fork_mod.router))
        resp = client.post(
            "/api/sess/fork",
            json={"at_event_id": 5, "name": "branch-x"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["session_id"] == "s-fork-1"
        assert body["fork_point"] == 5

    def test_live_fork_reuses_the_attached_store(self, monkeypatch, tmp_path):
        # Forking a LIVE session (by graph_id or file stem) must go
        # through the engine's open store — a second open of the
        # actively-written source IOERRs on POSIX. Both source-open
        # entry points are bombed; the REAL fork runs.
        from kohakuterrarium.studio.persistence import fork as fork_handler_mod

        store_path = tmp_path / "alice_3f2a9c11.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        store.append_event("alice", "user_message", {"content": "hi"})
        store.checkpoint()
        engine = _FakeEngine(graph=_FakeGraph([]), creatures={})
        engine._session_stores = {"graph_live1": store}

        def _bomb(*a, **k):
            raise AssertionError("live fork must not open the source session file")

        monkeypatch.setattr(fork_mod, "resolve_session_path_default", _bomb)
        monkeypatch.setattr(fork_handler_mod, "SessionStore", _bomb)

        app = _app(fork_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        client = TestClient(app)
        try:
            resp = client.post(
                "/api/alice_3f2a9c11/fork",
                json={"at_event_id": 1, "name": "pin-fork"},
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["fork_point"] == 1
            # The child landed on disk as a REAL fork of the live source.
            assert Path(resp.json()["path"]).exists()
        finally:
            store.close()

    async def test_live_fork_does_not_block_event_loop(self, monkeypatch, tmp_path):
        from kohakuterrarium.studio.persistence import fork as fork_handler_mod

        store_path = tmp_path / "alice_slow_fork.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        store.append_event("alice", "user_message", {"content": "hi"})
        store.checkpoint()
        engine = _FakeEngine(graph=_FakeGraph([]), creatures={})
        engine._session_stores = {"graph_live1": store}

        real_find = fork_handler_mod.find_fork_point

        def slow_find(*a, **k):
            time.sleep(0.3)
            return real_find(*a, **k)

        def _bomb(*a, **k):
            raise AssertionError("live fork must not open the source session file")

        monkeypatch.setattr(fork_mod, "resolve_session_path_default", _bomb)
        monkeypatch.setattr(fork_handler_mod, "SessionStore", _bomb)
        monkeypatch.setattr(fork_handler_mod, "find_fork_point", slow_find)

        app = _app(fork_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        loop_alive: list[float] = []
        stop = asyncio.Event()

        async def _ping():
            while not stop.is_set():
                loop_alive.append(time.monotonic())
                await asyncio.sleep(0.02)
            loop_alive.append(time.monotonic())

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                ping = asyncio.create_task(_ping())
                await asyncio.sleep(0)
                resp = await client.post(
                    "/api/alice_slow_fork/fork",
                    json={"at_event_id": 1, "name": "slow-fork"},
                )
                stop.set()
                await ping
            assert resp.status_code == 201, resp.text
            gaps = [
                loop_alive[i + 1] - loop_alive[i] for i in range(len(loop_alive) - 1)
            ]
            assert (
                max(gaps) < 0.15
            ), f"live fork blocked the loop; max gap={max(gaps):.3f}s"
        finally:
            store.close()


# ── history ─────────────────────────────────────────────────────


class TestHistoryRoutes:
    def test_unpaged_saved_history_is_rejected_without_full_scan(self, monkeypatch):
        scanned = []

        def fake_payload(*_a, **_k):
            scanned.append("payload")
            return {"events": ["x"] * 10_000}

        monkeypatch.setattr(history_mod, "live_store_entry", lambda *_a, **_k: None)
        monkeypatch.setattr(
            history_mod,
            "resolve_session_path_default",
            lambda _n: Path("/x/s.kohakutr"),
        )
        monkeypatch.setattr(history_mod, "_saved_history_page", fake_payload)
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/sess/history/alice", params={"paged": False})
        assert resp.status_code == 400
        assert "paged" in resp.json()["detail"]
        assert scanned == []

    def test_unpaged_live_history_is_rejected_without_full_scan(self, monkeypatch):
        scanned = []

        def fake_from_store(*_a, **_k):
            scanned.append("from_store")
            return {"events": ["x"] * 10_000}

        fake_store = types.SimpleNamespace(_path="/x/live.kohakutr")
        monkeypatch.setattr(
            history_mod, "live_store_entry", lambda *_a, **_k: ("live_g", fake_store)
        )
        monkeypatch.setattr(history_mod, "history_page_from_store", fake_from_store)
        engine = _FakeEngine(graph=_FakeGraph(["root"]), creatures={})
        app = _app(history_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        resp = TestClient(app).get("/api/live_g/history/root", params={"paged": False})
        assert resp.status_code == 400
        assert "paged" in resp.json()["detail"]
        assert scanned == []

    def test_zero_limit_is_rejected_as_unbounded(self, monkeypatch):
        monkeypatch.setattr(history_mod, "live_store_entry", lambda *_a, **_k: None)
        monkeypatch.setattr(
            history_mod,
            "resolve_session_path_default",
            lambda _n: Path("/x/s.kohakutr"),
        )
        monkeypatch.setattr(
            history_mod,
            "_saved_history_page",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("full scan")),
        )
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/sess/history/alice", params={"limit": 0})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "paged" in detail or "limit" in detail

    async def test_unpaged_reject_does_not_block_event_loop(self, monkeypatch):
        def slow_full_scan(*_a, **_k):
            time.sleep(0.3)
            return {"events": []}

        fake_store = types.SimpleNamespace(_path="/x/live.kohakutr")
        monkeypatch.setattr(
            history_mod, "live_store_entry", lambda *_a, **_k: ("live_g", fake_store)
        )
        monkeypatch.setattr(history_mod, "history_page_from_store", slow_full_scan)
        engine = _FakeEngine(graph=_FakeGraph(["root"]), creatures={})
        app = _app(history_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        loop_alive: list[float] = []

        async def _ping():
            for _ in range(5):
                await asyncio.sleep(0.02)
                loop_alive.append(time.monotonic())

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            req = asyncio.create_task(
                client.get("/api/live_g/history/root", params={"paged": "false"})
            )
            ping = asyncio.create_task(_ping())
            resp, _ = await asyncio.gather(req, ping)
        assert resp.status_code == 400
        gaps = [loop_alive[i + 1] - loop_alive[i] for i in range(len(loop_alive) - 1)]
        assert (
            max(gaps) < 0.15
        ), f"unpaged reject stalled the loop; max gap={max(gaps):.3f}s"

    async def test_live_history_page_does_not_block_event_loop(
        self, monkeypatch, tmp_path
    ):
        def slow_page(*_a, **_k):
            time.sleep(0.3)
            return {"target": "alice", "events": []}

        store_path = tmp_path / "live-page.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        store.checkpoint()
        monkeypatch.setattr(history_mod, "history_page_from_store", slow_page)
        monkeypatch.setattr(
            history_mod, "live_store_entry", lambda *_a, **_k: ("live_g", store)
        )
        engine = _FakeEngine(graph=_FakeGraph(["alice"]), creatures={})
        app = _app(history_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        loop_alive: list[float] = []
        stop = asyncio.Event()

        async def _ping():
            while not stop.is_set():
                loop_alive.append(time.monotonic())
                await asyncio.sleep(0.02)
            loop_alive.append(time.monotonic())

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                ping = asyncio.create_task(_ping())
                await asyncio.sleep(0)
                resp = await client.get("/api/live_g/history/alice")
                stop.set()
                await ping
            assert resp.status_code == 200, resp.text
            gaps = [
                loop_alive[i + 1] - loop_alive[i] for i in range(len(loop_alive) - 1)
            ]
            assert (
                max(gaps) < 0.15
            ), f"live history page blocked the loop; max gap={max(gaps):.3f}s"
        finally:
            store.close()

    async def test_live_history_detail_does_not_block_event_loop(
        self, monkeypatch, tmp_path
    ):
        def slow_detail(*_a, **_k):
            time.sleep(0.3)
            return {"target": "alice", "value": None}

        store_path = tmp_path / "live-detail.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        store.checkpoint()
        monkeypatch.setattr(history_mod, "history_detail", slow_detail)
        monkeypatch.setattr(
            history_mod, "live_store_entry", lambda *_a, **_k: ("live_g", store)
        )
        engine = _FakeEngine(graph=_FakeGraph(["alice"]), creatures={})
        app = _app(history_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        loop_alive: list[float] = []
        stop = asyncio.Event()

        async def _ping():
            while not stop.is_set():
                loop_alive.append(time.monotonic())
                await asyncio.sleep(0.02)
            loop_alive.append(time.monotonic())

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                ping = asyncio.create_task(_ping())
                await asyncio.sleep(0)
                resp = await client.get(
                    "/api/live_g/history/alice/detail",
                    params={"stream": "events", "ref": "r", "history_id": "h1"},
                )
                stop.set()
                await ping
            assert resp.status_code == 200, resp.text
            gaps = [
                loop_alive[i + 1] - loop_alive[i] for i in range(len(loop_alive) - 1)
            ]
            assert (
                max(gaps) < 0.15
            ), f"live history detail blocked the loop; max gap={max(gaps):.3f}s"
        finally:
            store.close()

    async def test_live_history_index_does_not_block_event_loop(
        self, monkeypatch, tmp_path
    ):
        def slow_index(*_a, **_k):
            time.sleep(0.3)
            return {"session_name": "live", "targets": []}

        store_path = tmp_path / "live-index.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        store.checkpoint()
        monkeypatch.setattr(history_mod, "history_index_from_store", slow_index)
        monkeypatch.setattr(
            history_mod, "live_store_entry", lambda *_a, **_k: ("live_g", store)
        )
        engine = _FakeEngine(graph=_FakeGraph(["alice"]), creatures={})
        app = _app(history_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        loop_alive: list[float] = []
        stop = asyncio.Event()

        async def _ping():
            while not stop.is_set():
                loop_alive.append(time.monotonic())
                await asyncio.sleep(0.02)
            loop_alive.append(time.monotonic())

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                ping = asyncio.create_task(_ping())
                await asyncio.sleep(0)
                resp = await client.get("/api/live_g/history")
                stop.set()
                await ping
            assert resp.status_code == 200, resp.text
            gaps = [
                loop_alive[i + 1] - loop_alive[i] for i in range(len(loop_alive) - 1)
            ]
            assert (
                max(gaps) < 0.15
            ), f"live history index blocked the loop; max gap={max(gaps):.3f}s"
        finally:
            store.close()

    def test_saved_paging_invalid_limit_returns_400(self, monkeypatch, tmp_path):
        path = tmp_path / "paged-validation.kohakutr"
        store = SessionStore(path)
        store.append_event("alice", "user_input", {"content": "hello"})
        store.close(update_status=False)
        monkeypatch.setattr(history_mod, "resolve_session_path_default", lambda _: path)
        client = TestClient(_app(history_mod.router))
        response = client.get(
            "/api/paged-validation/history/alice",
            params={"paged": "true", "limit": 0},
        )
        assert response.status_code == 400
        assert "limit" in response.json()["detail"]

    def test_index_missing(self, monkeypatch):
        monkeypatch.setattr(history_mod, "resolve_session_path_default", lambda n: None)
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/ghost/history")
        assert resp.status_code == 404

    def test_index_success(self, monkeypatch):
        monkeypatch.setattr(
            history_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )
        monkeypatch.setattr(
            history_mod,
            "history_index_payload",
            lambda p: {"session_name": "s", "targets": ["a", "b"]},
        )
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/sess/history")
        assert resp.status_code == 200
        assert resp.json()["targets"] == ["a", "b"]

    def test_target_missing(self, monkeypatch):
        monkeypatch.setattr(history_mod, "resolve_session_path_default", lambda n: None)
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/ghost/history/alice")
        assert resp.status_code == 404

    def test_target_success(self, monkeypatch):
        monkeypatch.setattr(
            history_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        def fake_page(path, target, **_k):
            return {"target": target, "events": []}

        monkeypatch.setattr(history_mod, "_saved_history_page", fake_page)
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/sess/history/alice")
        assert resp.status_code == 200
        assert resp.json()["target"] == "alice"

    def test_target_unquoted(self, monkeypatch):
        monkeypatch.setattr(
            history_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        def fake_page(path, target, **_k):
            return {"target": target, "events": []}

        monkeypatch.setattr(history_mod, "_saved_history_page", fake_page)
        client = TestClient(_app(history_mod.router))
        # URL-encoded "a:b" → "a%3Ab"
        resp = client.get("/api/sess/history/a%3Ab")
        assert resp.status_code == 200
        assert resp.json()["target"] == "a:b"

    def test_saved_target_page_does_not_thread_live_job_ids(self, monkeypatch):
        monkeypatch.setattr(history_mod, "live_store_entry", lambda svc, n: None)
        monkeypatch.setattr(
            history_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )
        captured = {}

        def fake_page(path, target, **kwargs):
            captured["kwargs"] = kwargs
            return {"target": target, "events": []}

        monkeypatch.setattr(history_mod, "_saved_history_page", fake_page)
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/sess/history/root")
        assert resp.status_code == 200
        assert "live_job_ids" not in captured["kwargs"]

    def test_live_target_threads_running_job_ids(self, monkeypatch):
        # A live-resolved session gathers the still-running job ids from
        # the host engine's live agents and threads them into the paged
        # payload so an in-flight sub-agent isn't synthesised as interrupted.
        fake_store = types.SimpleNamespace(_path="/x/live.kohakutr", _closed=False)

        async def _run(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        fake_store.run = _run
        monkeypatch.setattr(
            history_mod, "live_store_entry", lambda svc, n: ("live_g", fake_store)
        )
        engine = _FakeEngine(
            graph=_FakeGraph(["root"]),
            creatures={"root": _FakeCreature(_FakeAgent(["job_abc"]))},
        )
        captured = {}

        def fake_page(store, **kwargs):
            captured["store"] = store
            captured["session_name"] = kwargs.get("session_name")
            captured["live"] = kwargs.get("live_job_ids")
            return {"target": kwargs.get("target"), "events": []}

        monkeypatch.setattr(history_mod, "history_page_from_store", fake_page)
        app = _app(history_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        resp = TestClient(app).get("/api/live_g/history/root")
        assert resp.status_code == 200
        assert captured["live"] == {"job_abc"}
        assert captured["store"] is fake_store
        assert captured["session_name"] == "live"

    def test_live_history_never_reopens_the_store_file(self, monkeypatch, tmp_path):
        # THE CI bug (POSIX): a second SessionStore open of the live,
        # actively-written file fails with SQLITE_IOERR. While the
        # session is live, history — addressed by graph_id OR by the
        # store's file stem — must reuse the engine's open store, so
        # every disk-open entry point is bombed.
        store_path = tmp_path / "alice_3f2a9c11.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        store.checkpoint()
        engine = _FakeEngine(graph=_FakeGraph([]), creatures={})
        engine._session_stores = {"graph_live1": store}

        def _bomb(*a, **k):
            raise AssertionError("live history must not open the session file")

        monkeypatch.setattr(history_mod, "resolve_session_path_default", _bomb)
        monkeypatch.setattr(history_mod, "history_index_payload", _bomb, raising=False)
        monkeypatch.setattr(history_mod, "_saved_history_page", _bomb)

        app = _app(history_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        client = TestClient(app)
        try:
            for name in ("graph_live1", "alice_3f2a9c11"):
                index = client.get(f"/api/{name}/history")
                assert index.status_code == 200, (name, index.text)
                assert index.json()["session_name"] == "alice_3f2a9c11"
                target = client.get(f"/api/{name}/history/alice")
                assert target.status_code == 200, (name, target.text)
        finally:
            store.close()
