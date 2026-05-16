"""Unit tests for :class:`TerrariumAttachAdapter`.

The full attach session needs a real engine + agent ``output_router``;
we drive it with the ``_FakeAgent`` test helper extended with an
``output_router`` that records secondary subscribers.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


from kohakuterrarium.laboratory.adapters.terrarium_attach import (
    TerrariumAttachAdapter,
    _SinkQueueAdapter,
    _normalize_input_content,
)
from kohakuterrarium.laboratory.ws_proxy import WSFrameSink
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _FakeLabNode:
    def __init__(self):
        self.app_extensions = {}
        self.notifications = []

    def register_app_extension(self, ns, handler):
        self.app_extensions[ns] = handler

    def unregister_app_extension(self, ns):
        return self.app_extensions.pop(ns, None) is not None

    async def notify(self, *, to_node, namespace, type, body):
        self.notifications.append(body)


# ── pure helpers ─────────────────────────────────────────────


class TestNormalizeInputContent:
    def test_string_content(self):
        assert _normalize_input_content({"content": "hi"}) == "hi"

    def test_list_content_normalised(self):
        out = _normalize_input_content({"content": [{"type": "text", "text": "hi"}]})
        # Structured content is passed through as a list of content parts,
        # preserving the text payload.
        assert out == [{"type": "text", "text": "hi"}]

    def test_message_fallback(self):
        assert _normalize_input_content({"message": "fallback"}) == "fallback"

    def test_no_content_returns_empty(self):
        assert _normalize_input_content({}) == ""

    def test_non_str_message_returns_empty(self):
        assert _normalize_input_content({"message": 123}) == ""


class TestSinkQueueAdapter:
    def test_put_nowait_forwards_to_sink(self):
        sink = MagicMock()
        adapter = _SinkQueueAdapter(sink)
        adapter.put_nowait({"x": 1})
        sink.send_json_nowait.assert_called_once_with({"x": 1})


# ── adapter setup / teardown ─────────────────────────────────


async def _build_adapter(t):
    node = _FakeLabNode()
    adapter = TerrariumAttachAdapter(t, node)
    return adapter, node


class TestAttachAdapterLifecycle:
    async def test_registers_extension(self):
        t = await TestTerrariumBuilder().build()
        try:
            adapter, node = await _build_adapter(t)
            assert "terrarium.attach" in node.app_extensions
            adapter.detach()
        finally:
            await t.shutdown()


# ── on_start happy path ──────────────────────────────────────


def _make_sink(node):
    sink = WSFrameSink(node, "ctrl", "stream-1")
    return sink


class TestOnStart:
    async def test_setup_frame_returned(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            # Replace the agent's output_router with a stub that
            # records subscribers.
            alice = t.get_creature("alice")
            secondaries = []
            alice.agent.output_router = SimpleNamespace(
                add_secondary=secondaries.append,
                remove_secondary=lambda x: (
                    secondaries.remove(x) if x in secondaries else None
                ),
                submit_reply_with_status=lambda r: (True, "ok"),
            )
            sink = _make_sink(node)
            resp = await adapter.on_start(
                {"creature_id": "alice", "session_id": "_"}, sink
            )
            assert resp is not None
            assert resp["setup"]["activity_type"] == "session_info"
            assert "stream-1" in adapter._sessions
            await adapter.on_close("stream-1")
            assert secondaries == []
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_with_siblings_subscribed(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_creature("bob").build()
        )
        try:
            adapter, node = await _build_adapter(t)
            for cid in ("alice", "bob"):
                ag = t.get_creature(cid).agent
                ag.output_router = SimpleNamespace(
                    add_secondary=lambda x: None,
                    remove_secondary=lambda x: None,
                    submit_reply_with_status=lambda r: (True, "ok"),
                )
            sink = _make_sink(node)
            await adapter.on_start({"creature_id": "alice", "session_id": "_"}, sink)
            session = adapter._sessions["stream-1"]
            # Sibling bob was wired.
            assert session.sibling_modules
            await adapter.on_close("stream-1")
        finally:
            adapter.detach()
            await t.shutdown()


# ── helpers: _register_channel_callbacks + _replay_channel_history ──


class TestChannelCallbacks:
    async def test_no_env_returns_empty(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            sink = _make_sink(node)
            cbs = adapter._register_channel_callbacks("ghost", sink)
            assert cbs == []
            # _replay_channel_history on unknown graph is a no-op.
            adapter._replay_channel_history("ghost", sink)
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_registers_per_channel(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            adapter, node = await _build_adapter(t)
            gid = t.get_creature("alice").graph_id
            sink = _make_sink(node)
            cbs = adapter._register_channel_callbacks(gid, sink)
            # One callback registered for the single "chat" channel.
            assert [ch.name for ch, _cb in cbs] == ["chat"]
            # Firing the callback pushes a structured channel_message frame
            # carrying the message fields onto the sink.
            _ch, cb = cbs[0]
            cb(
                "chat",
                SimpleNamespace(
                    timestamp=time.time(),
                    sender="alice",
                    content="x",
                    message_id="m1",
                ),
            )
            frames = _drain(sink)
            assert len(frames) == 1
            frame = frames[0]
            assert frame["type"] == "channel_message"
            assert frame["channel"] == "chat"
            assert frame["sender"] == "alice"
            assert frame["content"] == "x"
            assert frame["message_id"] == "m1"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_replay_with_history(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            adapter, node = await _build_adapter(t)
            gid = t.get_creature("alice").graph_id
            env = t._environments[gid]
            ch = env.shared_channels.get("chat")
            from kohakuterrarium.core.channel import ChannelMessage

            await ch.send(ChannelMessage(sender="alice", content="hi"))
            sink = _make_sink(node)
            adapter._replay_channel_history(gid, sink)
            # The single historical message is replayed as a channel_message
            # frame flagged ``history=True``.
            frames = _drain(sink)
            assert len(frames) == 1
            frame = frames[0]
            assert frame["type"] == "channel_message"
            assert frame["channel"] == "chat"
            assert frame["sender"] == "alice"
            assert frame["content"] == "hi"
            assert frame["history"] is True
        finally:
            adapter.detach()
            await t.shutdown()


# ── _find_sibling_by_name ────────────────────────────────────


class TestFindSibling:
    async def test_unknown_graph_returns_none(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            alice.graph_id = "ghost"
            assert adapter._find_sibling_by_name(alice, "bob") is None
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_finds_by_name(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_creature("bob").build()
        )
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            sibling = adapter._find_sibling_by_name(alice, "bob")
            assert sibling is not None
            assert sibling.name == "bob"
        finally:
            adapter.detach()
            await t.shutdown()


# ── _handle_ui_reply ─────────────────────────────────────────


class TestHandleUiReply:
    async def test_missing_event_id_silent(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            called = []
            alice.agent.output_router = SimpleNamespace(
                submit_reply_with_status=lambda r: called.append(r) or (True, "ok"),
            )
            sink = _make_sink(node)
            adapter._handle_ui_reply(sink, alice.agent, "alice", {})
            # No event_id → early return: the router is never invoked and
            # no ack frame is queued.
            assert called == []
            assert _drain(sink) == []
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_with_valid_event_id_acks(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            received = []
            alice.agent.output_router = SimpleNamespace(
                submit_reply_with_status=lambda r: received.append(r)
                or (True, "applied"),
            )
            sink = _make_sink(node)
            adapter._handle_ui_reply(
                sink,
                alice.agent,
                "alice",
                {
                    "event_id": "e1",
                    "action_id": "act",
                    "values": {"k": "v"},
                    "user": "u",
                    "ts": 1.0,
                },
            )
            # The frame was parsed into a UIReply and handed to the router.
            assert len(received) == 1
            reply = received[0]
            assert reply.event_id == "e1"
            assert reply.action_id == "act"
            assert reply.values == {"k": "v"}
            assert reply.user == "u"
            assert reply.timestamp == 1.0
            # The router's ack status is echoed back on a ui_reply_ack frame.
            frames = _drain(sink)
            assert [f["type"] for f in frames] == ["ui_reply_ack"]
            assert frames[0]["event_id"] == "e1"
            assert frames[0]["status"] == "applied"
            assert frames[0]["source"] == "alice"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_submit_reply_raises_swallowed(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")

            def _boom(r):
                raise RuntimeError("bad")

            alice.agent.output_router = SimpleNamespace(
                submit_reply_with_status=_boom,
            )
            sink = _make_sink(node)
            adapter._handle_ui_reply(sink, alice.agent, "alice", {"event_id": "e1"})
            # The router raised, but the failure is swallowed: an ack frame
            # is still emitted with status "unknown" so the UI isn't stuck.
            frames = _drain(sink)
            assert [f["type"] for f in frames] == ["ui_reply_ack"]
            assert frames[0]["event_id"] == "e1"
            assert frames[0]["status"] == "unknown"
        finally:
            adapter.detach()
            await t.shutdown()


# ── _process_input ──────────────────────────────────────────


def _drain(sink):
    """Collect every frame queued on the sink's outbox."""
    frames = []
    while not sink._outbox.empty():
        frames.append(sink._outbox.get_nowait())
    return frames


class TestProcessInput:
    async def test_inject_success_sends_idle(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            alice.agent.inject_input = AsyncMock()
            sink = _make_sink(node)
            await adapter._process_input(sink, alice.agent, "hi", "alice")
            # Input was forwarded to the agent verbatim with source="web".
            alice.agent.inject_input.assert_awaited_once_with("hi", source="web")
            # On success the sink emits a single ``idle`` frame for the
            # source creature — no error frame.
            frames = _drain(sink)
            assert [f["type"] for f in frames] == ["idle"]
            assert frames[0]["source"] == "alice"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_inject_failure_sends_error(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            alice.agent.inject_input = AsyncMock(side_effect=RuntimeError("bad"))
            sink = _make_sink(node)
            await adapter._process_input(sink, alice.agent, "hi", "alice")
            # On failure the sink emits an ``error`` frame carrying the
            # exception text — and no ``idle`` frame.
            frames = _drain(sink)
            assert [f["type"] for f in frames] == ["error"]
            assert frames[0]["source"] == "alice"
            assert frames[0]["content"] == "bad"
        finally:
            adapter.detach()
            await t.shutdown()
