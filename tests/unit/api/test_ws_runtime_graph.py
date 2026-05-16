"""Unit tests for :mod:`kohakuterrarium.api.ws.runtime_graph`.

The endpoint runs a complex stream of snapshots + engine events +
channel observers.  We:

- Cover the pure helpers (_version, _jsonable, _preview,
  _timestamp_to_string, _make_channel_callback) directly.
- Drive the websocket endpoint via fastapi TestClient so the
  accept/snapshot path lands and we can disconnect cleanly.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.ws import runtime_graph as rg_mod
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder
from kohakuterrarium.terrarium.service import LocalTerrariumService

# ── Pure helpers ──────────────────────────────────────────────


class TestVersion:
    def test_returns_monotonic_int(self):
        a = rg_mod._version()
        b = rg_mod._version()
        assert isinstance(a, int)
        assert b >= a


class TestJsonable:
    def test_basic_dict_passes_through(self):
        assert rg_mod._jsonable({"k": "v"}) == {"k": "v"}

    def test_unjsonable_coerced_to_str(self):
        class _Weird:
            def __str__(self):
                return "weird"

        out = rg_mod._jsonable(_Weird())
        assert isinstance(out, str)
        assert "weird" in out


class TestPreview:
    def test_string_short(self):
        assert rg_mod._preview("hello") == "hello"

    def test_string_truncates(self):
        long = "x" * 300
        out = rg_mod._preview(long, limit=160)
        assert out.endswith("…")
        assert len(out) == 160

    def test_dict_serialised(self):
        out = rg_mod._preview({"k": "v"})
        assert "k" in out and "v" in out

    def test_unjsonable_str_fallback(self):
        class _Weird:
            def __str__(self):
                return "weird"

        out = rg_mod._preview(_Weird())
        assert "weird" in out

    def test_strips_newlines(self):
        out = rg_mod._preview("a\nb\nc")
        assert "\n" not in out


class TestTimestampToString:
    def test_none(self):
        assert rg_mod._timestamp_to_string(None) == ""

    def test_datetime_isoformat(self):
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        out = rg_mod._timestamp_to_string(ts)
        assert "2026-01-01" in out

    def test_str_fallback(self):
        assert rg_mod._timestamp_to_string(123) == "123"


class TestMakeChannelCallback:
    def test_callback_enqueues_payload(self):
        captured = []
        cb = rg_mod._make_channel_callback(
            "g1", lambda payload: captured.append(payload)
        )
        msg = SimpleNamespace(
            sender="alice",
            content="hi",
            message_id="m1",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        cb("chat", msg)
        assert captured
        p = captured[0]
        assert p["type"] == "channel_message"
        assert p["graph_id"] == "g1"
        assert p["channel"] == "chat"
        assert p["sender"] == "alice"


# ── Endpoint integration via TestClient ──────────────────────


def _build_app(service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(rg_mod.router)
    return app


class TestWsEndpoint:
    async def test_subscribe_and_snapshot_then_disconnect(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        app = _build_app(svc)
        try:
            with TestClient(app) as client:
                with client.websocket_connect("/ws/runtime/graph") as ws:
                    sub_msg = ws.receive_json()
                    assert sub_msg["type"] == "subscribed"
                    snap_msg = ws.receive_json()
                    assert snap_msg["type"] == "snapshot"
                    assert "graphs" in snap_msg["snapshot"]
        finally:
            await t.shutdown()

    async def test_with_channel_observed(self):
        # NOTE: TestClient runs the endpoint in a separate portal thread
        # with its own event loop, so the snapshot built there does not
        # see this loop's engine state — the *content* assertion lives
        # in test_ws_runtime_graph_pump.py, which drives the coroutine
        # in-loop. Here we pin the endpoint's dispatch contract: it
        # accepts, then emits exactly subscribed then snapshot, and the
        # subscribed/snapshot versions agree.
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("alice", "bob", channel="chat")
            .build()
        )
        svc = LocalTerrariumService(t)
        app = _build_app(svc)
        try:
            with TestClient(app) as client:
                with client.websocket_connect("/ws/runtime/graph") as ws:
                    sub = ws.receive_json()
                    snap = ws.receive_json()
            assert sub["type"] == "subscribed"
            assert snap["type"] == "snapshot"
            # subscribed.version == snapshot.snapshot.version (the
            # endpoint sends the snapshot's own version on the
            # subscribed frame so the client can order patches).
            assert sub["version"] == snap["snapshot"]["version"]
            assert "graphs" in snap["snapshot"]
        finally:
            await t.shutdown()
