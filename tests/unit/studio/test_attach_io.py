"""Unit tests for :mod:`kohakuterrarium.studio.attach.io`."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kohakuterrarium.studio.attach import input_ops as input_ops_mod
from kohakuterrarium.studio.attach import io as io_mod
from kohakuterrarium.studio.attach import io_cluster as io_cluster_mod
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder
from kohakuterrarium.terrarium.service import LocalTerrariumService


class _FakeWebSocket:
    def __init__(self, frames=None):
        self._frames = list(frames or [])
        self.sent = []

    async def receive_json(self):
        if not self._frames:
            raise RuntimeError("disconnect")
        return self._frames.pop(0)

    async def send_json(self, data):
        self.sent.append(data)


# ── _normalize_input_content ────────────────────────────────


class TestNormalizeInputContent:
    def test_string_content(self):
        assert io_mod._normalize_input_content({"content": "hi"}) == "hi"

    def test_list_content(self):
        out = io_mod._normalize_input_content(
            {"content": [{"type": "text", "text": "x"}]}
        )
        assert isinstance(out, list)

    def test_message_fallback(self):
        assert io_mod._normalize_input_content({"message": "fallback"}) == "fallback"

    def test_no_content(self):
        assert io_mod._normalize_input_content({}) == ""

    def test_non_str_message(self):
        assert io_mod._normalize_input_content({"message": 123}) == ""


# ── _handle_ui_reply ────────────────────────────────────────


class _UiCreature:
    def __init__(self, name, *, status=(True, "ok"), graph_id="g"):
        self.name = name
        self.graph_id = graph_id
        agent = MagicMock()
        if isinstance(status, Exception):
            agent.output_router.submit_reply_with_status = MagicMock(side_effect=status)
        else:
            agent.output_router.submit_reply_with_status = MagicMock(
                return_value=status
            )
        self.agent = agent


class TestHandleUiReply:
    def test_missing_event_id_silent(self):
        creature = _UiCreature("alice")
        q = asyncio.Queue()
        io_mod._handle_ui_reply({}, MagicMock(), creature, "s", q)
        assert q.empty()
        creature.agent.output_router.submit_reply_with_status.assert_not_called()

    def test_full_reply_enqueues_ack_on_bound_creature(self):
        creature = _UiCreature("alice", status=(True, "applied"))
        q = asyncio.Queue()
        io_mod._handle_ui_reply(
            {
                "event_id": "e1",
                "action_id": "act",
                "values": {"k": "v"},
                "user": "u",
                "ts": 1.5,
            },
            MagicMock(),
            creature,
            "s",
            q,
        )
        (reply,), _ = creature.agent.output_router.submit_reply_with_status.call_args
        assert reply.event_id == "e1"
        assert reply.action_id == "act"
        assert reply.values == {"k": "v"}
        assert reply.user == "u"
        assert reply.timestamp == 1.5
        ack = q.get_nowait()
        assert ack["status"] == "applied"
        assert ack["event_id"] == "e1"
        assert ack["type"] == "ui_reply_ack"
        assert ack["source"] == "alice"

    def test_reply_routes_to_target_sibling(self, monkeypatch):
        # UXI-09: a reply for a prompt raised by a non-bound SIBLING must go
        # to the SIBLING's router, not the bound creature's — else no pending
        # Future resolves and the sibling's interactive tool hangs.
        bound = _UiCreature("alice")
        sibling = _UiCreature("bob", status=(True, "applied"))

        def _resolve(engine, creature, sid, data):
            if (data.get("target") or "") == "bob":
                return sibling
            return bound

        monkeypatch.setattr(io_mod, "_resolve_target", _resolve)
        q = asyncio.Queue()
        io_mod._handle_ui_reply(
            {"event_id": "e1", "target": "bob"}, MagicMock(), bound, "s", q
        )
        # The reply reached bob, not the bound alice.
        sibling.agent.output_router.submit_reply_with_status.assert_called_once()
        bound.agent.output_router.submit_reply_with_status.assert_not_called()
        # And the ack is tagged with the resolved creature so it lands in bob's tab.
        assert q.get_nowait()["source"] == "bob"

    def test_unknown_target_falls_back_to_bound(self, monkeypatch):
        bound = _UiCreature("alice", status=(True, "applied"))

        def _resolve(engine, creature, sid, data):
            raise KeyError("ghost")

        monkeypatch.setattr(io_mod, "_resolve_target", _resolve)
        q = asyncio.Queue()
        io_mod._handle_ui_reply(
            {"event_id": "e1", "target": "ghost"}, MagicMock(), bound, "s", q
        )
        bound.agent.output_router.submit_reply_with_status.assert_called_once()
        assert q.get_nowait()["source"] == "alice"

    def test_submit_reply_raises_swallowed(self):
        creature = _UiCreature("alice", status=RuntimeError("bad"))
        q = asyncio.Queue()
        io_mod._handle_ui_reply({"event_id": "e1"}, MagicMock(), creature, "s", q)
        ack = q.get_nowait()
        assert ack["status"] == "unknown"

    def test_queue_full_dropped(self):
        creature = _UiCreature("alice")
        q = asyncio.Queue(maxsize=1)
        q.put_nowait({"existing": True})
        # Should not raise on full queue.
        io_mod._handle_ui_reply({"event_id": "e1"}, MagicMock(), creature, "s", q)


# ── _process_input ──────────────────────────────────────────


class TestProcessInput:
    async def test_success_emits_idle(self):
        agent = MagicMock()
        agent.is_processing = False
        agent.inject_input = AsyncMock(return_value=True)
        q = asyncio.Queue()
        await io_mod._process_input(agent, "hi", q, "src", "pending_1")
        frame = q.get_nowait()
        assert frame["type"] == "idle"

    async def test_buffered_emits_input_queued_with_id(self):
        # inject_input returning False means the input was buffered for a
        # mid-turn drain — the ack carries the id so a shell can edit it.
        agent = MagicMock()
        agent.inject_input = AsyncMock(return_value=False)
        q = asyncio.Queue()
        await io_mod._process_input(agent, "hi", q, "src", "pending_1")
        frame = q.get_nowait()
        assert frame["type"] == "input_queued"
        assert frame["event_id"] == "pending_1"
        # The minted id is threaded into inject_input so the buffered
        # event carries the SAME id the ack advertises.
        assert agent.inject_input.await_args.kwargs["pending_id"] == "pending_1"

    async def test_inject_failure_emits_error(self):
        agent = MagicMock()
        agent.inject_input = AsyncMock(side_effect=RuntimeError("bad"))
        q = asyncio.Queue()
        await io_mod._process_input(agent, "hi", q, "src", "pending_1")
        frame = q.get_nowait()
        assert frame["type"] == "error"

    async def test_cancelled_propagates(self):
        agent = MagicMock()
        agent.inject_input = AsyncMock(side_effect=asyncio.CancelledError())
        q = asyncio.Queue()
        with pytest.raises(asyncio.CancelledError):
            await io_mod._process_input(agent, "hi", q, "src", "pending_1")

    async def test_queue_full_dropped_on_idle(self):
        agent = MagicMock()
        agent.is_processing = False
        agent.inject_input = AsyncMock(return_value=True)
        q = asyncio.Queue(maxsize=1)
        q.put_nowait({"x": 1})
        # Should not raise.
        await io_mod._process_input(agent, "hi", q, "src", "pending_1")

    async def test_queue_full_dropped_on_error(self):
        agent = MagicMock()
        agent.inject_input = AsyncMock(side_effect=RuntimeError("bad"))
        q = asyncio.Queue(maxsize=1)
        q.put_nowait({"x": 1})
        # Should not raise.
        await io_mod._process_input(agent, "hi", q, "src", "pending_1")


# ── _handle_pending_op (input_edit / input_cancel) ──────────────


class _PendingCreature:
    def __init__(self, name, *, edit_ok=True, cancel_ok=True):
        self.name = name
        self.graph_id = "g"
        agent = MagicMock()
        agent.edit_pending = MagicMock(return_value=edit_ok)
        agent.cancel_pending = MagicMock(return_value=cancel_ok)
        self.agent = agent


class TestHandlePendingOp:
    def test_edit_commits_and_acks(self):
        creature = _PendingCreature("alice", edit_ok=True)
        q = asyncio.Queue()
        io_mod._handle_pending_op(
            {"event_id": "p1", "content": "corrected"},
            "input_edit",
            engine=MagicMock(),
            creature=creature,
            session_id="s",
            queue=q,
        )
        creature.agent.edit_pending.assert_called_once_with("p1", "corrected")
        ack = q.get_nowait()
        assert ack["type"] == "input_edit_ack"
        assert ack["status"] == "edited"
        assert ack["event_id"] == "p1"

    def test_edit_after_claim_reports_already_sent(self):
        creature = _PendingCreature("alice", edit_ok=False)
        q = asyncio.Queue()
        io_mod._handle_pending_op(
            {"event_id": "p1", "content": "x"},
            "input_edit",
            engine=MagicMock(),
            creature=creature,
            session_id="s",
            queue=q,
        )
        assert q.get_nowait()["status"] == "already_sent"

    def test_cancel_commits_and_acks(self):
        creature = _PendingCreature("alice", cancel_ok=True)
        q = asyncio.Queue()
        io_mod._handle_pending_op(
            {"event_id": "p1"},
            "input_cancel",
            engine=MagicMock(),
            creature=creature,
            session_id="s",
            queue=q,
        )
        creature.agent.cancel_pending.assert_called_once_with("p1")
        ack = q.get_nowait()
        assert ack["type"] == "input_cancel_ack"
        assert ack["status"] == "cancelled"

    def test_missing_event_id_silent(self):
        creature = _PendingCreature("alice")
        q = asyncio.Queue()
        io_mod._handle_pending_op(
            {},
            "input_cancel",
            engine=MagicMock(),
            creature=creature,
            session_id="s",
            queue=q,
        )
        assert q.empty()
        creature.agent.cancel_pending.assert_not_called()

    def test_unknown_target_emits_error(self, monkeypatch):
        creature = _PendingCreature("alice")

        def _no_creature(engine, sid, name):
            raise KeyError(name)

        # ``_handle_pending_op`` resolves ``find_creature`` in input_ops.
        monkeypatch.setattr(input_ops_mod, "find_creature", _no_creature)
        q = asyncio.Queue()
        io_mod._handle_pending_op(
            {"event_id": "p1", "target": "ghost"},
            "input_cancel",
            engine=MagicMock(),
            creature=creature,
            session_id="s",
            queue=q,
        )
        assert q.get_nowait()["type"] == "error"


# ── clustered pending input routing ──────────────────────────


class TestClusterPendingInputRouting:
    @pytest.mark.parametrize("msg_type", ["input_edit", "input_cancel"])
    async def test_pending_op_routes_to_target_worker(self, msg_type):
        target = ("node-b", "sid-b", SimpleNamespace(stream_id="stream-b"))
        default = ("node-a", "sid-a", SimpleNamespace(stream_id="stream-a"))
        service = SimpleNamespace(host=SimpleNamespace(request=AsyncMock()))
        data = {
            "type": msg_type,
            "target": "bob",
            "event_id": "pending-1",
        }
        if msg_type == "input_edit":
            data["content"] = "updated"

        handled = await io_cluster_mod._forward_client_frame(
            service,
            data,
            {"bob": target},
            default,
        )

        assert handled is True
        service.host.request.assert_awaited_once_with(
            to_node="node-b",
            namespace="terrarium.attach",
            type="input",
            body={"stream_id": "stream-b", "frame": data},
            timeout=10.0,
        )


# ── _forward_queue ──────────────────────────────────────────


class TestForwardQueue:
    async def test_forwards_until_none(self):
        ws = _FakeWebSocket()
        q = asyncio.Queue()
        q.put_nowait({"a": 1})
        q.put_nowait({"b": 2})
        q.put_nowait(None)
        await io_mod._forward_queue(q, ws)
        assert ws.sent == [{"a": 1}, {"b": 2}]


# ── _register_channel_callbacks ─────────────────────────────


class TestRegisterChannelCallbacks:
    async def test_subscribes_to_each_channel(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            env = t._environments[t.get_creature("alice").graph_id]
            q = asyncio.Queue()
            cbs = io_mod._register_channel_callbacks(env, q)
            assert cbs
            # Fire one and assert callback enqueues a message.
            ch, cb = cbs[0]
            from kohakuterrarium.core.channel import ChannelMessage

            msg = ChannelMessage(sender="alice", content="hi", message_id="m1")
            cb("chat", msg)
            frame = q.get_nowait()
            assert frame["type"] == "channel_message"
            assert frame["sender"] == "alice"
        finally:
            await t.shutdown()

    async def test_no_channels_returns_empty(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            env = t._environments[t.get_creature("alice").graph_id]
            cbs = io_mod._register_channel_callbacks(env, asyncio.Queue())
            assert cbs == []
        finally:
            await t.shutdown()


# ── _send_channel_history ───────────────────────────────────


class TestSendChannelHistory:
    async def test_no_channels_silent(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            env = t._environments[t.get_creature("alice").graph_id]
            ws = _FakeWebSocket()
            await io_mod._send_channel_history(ws, env)
            assert ws.sent == []
        finally:
            await t.shutdown()

    async def test_sends_each_history_message(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        try:
            env = t._environments[t.get_creature("alice").graph_id]
            ch = env.shared_channels.get("chat")
            from kohakuterrarium.core.channel import ChannelMessage

            await ch.send(ChannelMessage(sender="alice", content="hi"))
            ws = _FakeWebSocket()
            await io_mod._send_channel_history(ws, env)
            # History replayed.
            assert ws.sent
            assert ws.sent[0]["history"] is True
        finally:
            await t.shutdown()


# ── _resolve_creature_home ──────────────────────────────────


class TestResolveCreatureHome:
    async def test_no_resolver(self):
        svc = SimpleNamespace()
        out = await io_mod._resolve_creature_home(svc, "cid")
        assert out == "_host"

    async def test_resolver_returns_worker(self):
        async def _r(cid):
            return "worker-1"

        svc = SimpleNamespace(_resolve_home=_r)
        out = await io_mod._resolve_creature_home(svc, "cid")
        assert out == "worker-1"

    async def test_resolver_raises_returns_none(self):
        async def _r(cid):
            raise RuntimeError("bad")

        svc = SimpleNamespace(_resolve_home=_r)
        out = await io_mod._resolve_creature_home(svc, "cid")
        assert out is None


# ── attach_io: standalone local path ────────────────────────


class TestAttachIoLocal:
    async def test_unknown_creature_raises(self):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        try:
            # No creature; service.get_creature_info returns None.
            ws = _FakeWebSocket()
            with pytest.raises(KeyError):
                await io_mod.attach_io(ws, svc, "_", "ghost")
        finally:
            await t.shutdown()

    async def test_invalid_input_type_continues(self, monkeypatch):
        """Input frames that aren't 'input'/'ui_reply'/'ui_dismiss' are skipped."""
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            agent = t.get_creature("alice").agent
            agent.inject_input = AsyncMock()
            agent.output_router = SimpleNamespace(
                add_secondary=lambda x: None,
                remove_secondary=lambda x: None,
                submit_reply_with_status=lambda r: (True, "ok"),
            )
            agent.config = SimpleNamespace(model="m")
            ws = _FakeWebSocket(frames=[{"type": "unknown_frame"}])
            with pytest.raises(RuntimeError):
                await io_mod.attach_io(ws, svc, "_", "alice")
            # session_info was sent.
            assert any(s.get("activity_type") == "session_info" for s in ws.sent)
        finally:
            await t.shutdown()

    async def test_ui_dismiss_continues(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            agent = t.get_creature("alice").agent
            agent.output_router = SimpleNamespace(
                add_secondary=lambda x: None,
                remove_secondary=lambda x: None,
                submit_reply_with_status=lambda r: (True, "ok"),
            )
            agent.config = SimpleNamespace(model="m")
            ws = _FakeWebSocket(frames=[{"type": "ui_dismiss"}])
            with pytest.raises(RuntimeError):
                await io_mod.attach_io(ws, svc, "_", "alice")
        finally:
            await t.shutdown()

    async def test_input_routed_to_target_creature(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_creature("bob").build()
        )
        svc = LocalTerrariumService(t)
        try:
            for cid in ("alice", "bob"):
                ag = t.get_creature(cid).agent
                ag.inject_input = AsyncMock()
                ag.output_router = SimpleNamespace(
                    add_secondary=lambda x: None,
                    remove_secondary=lambda x: None,
                    submit_reply_with_status=lambda r: (True, "ok"),
                )
                ag.config = SimpleNamespace(model="m")
            ws = _FakeWebSocket(
                frames=[
                    {"type": "input", "content": "hello", "target": "bob"},
                ]
            )
            with pytest.raises(RuntimeError):
                await io_mod.attach_io(ws, svc, "_", "alice")
            # Give the spawned input task a moment to dispatch.
            await asyncio.sleep(0.05)
            # Input targeted at "bob" reached bob, not the attached alice.
            bob_agent = t.get_creature("bob").agent
            alice_agent = t.get_creature("alice").agent
            bob_agent.inject_input.assert_awaited()
            assert bob_agent.inject_input.await_args.args[0] == "hello"
            alice_agent.inject_input.assert_not_awaited()
        finally:
            await t.shutdown()

    async def test_input_unknown_target_emits_error(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            agent = t.get_creature("alice").agent
            agent.inject_input = AsyncMock()
            agent.output_router = SimpleNamespace(
                add_secondary=lambda x: None,
                remove_secondary=lambda x: None,
                submit_reply_with_status=lambda r: (True, "ok"),
            )
            agent.config = SimpleNamespace(model="m")
            ws = _FakeWebSocket(
                frames=[
                    {"type": "input", "content": "x", "target": "ghost"},
                ]
            )
            with pytest.raises(RuntimeError):
                await io_mod.attach_io(ws, svc, "_", "alice")
            # Error frame eventually flushed via the queue.
        finally:
            await t.shutdown()

    async def test_empty_input_skipped(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            agent = t.get_creature("alice").agent
            agent.inject_input = AsyncMock()
            agent.output_router = SimpleNamespace(
                add_secondary=lambda x: None,
                remove_secondary=lambda x: None,
                submit_reply_with_status=lambda r: (True, "ok"),
            )
            agent.config = SimpleNamespace(model="m")
            ws = _FakeWebSocket(frames=[{"type": "input", "content": ""}])
            with pytest.raises(RuntimeError):
                await io_mod.attach_io(ws, svc, "_", "alice")
            # Empty input skipped, inject not called for it.
            assert not agent.inject_input.called
        finally:
            await t.shutdown()

    async def test_ui_reply_frame_routed_to_output_router(self):
        # A ``ui_reply`` frame on the WS must reach the agent's
        # output_router.submit_reply_with_status — the bridge between
        # the browser and an awaiting interactive tool.
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            agent = t.get_creature("alice").agent
            submitted = []
            agent.output_router = SimpleNamespace(
                add_secondary=lambda x: None,
                remove_secondary=lambda x: None,
                submit_reply_with_status=lambda r: submitted.append(r)
                or (True, "applied"),
            )
            agent.config = SimpleNamespace(model="m")
            ws = _FakeWebSocket(
                frames=[
                    {
                        "type": "ui_reply",
                        "event_id": "evt-1",
                        "action_id": "confirm",
                        "values": {"ok": True},
                    }
                ]
            )
            with pytest.raises(RuntimeError):
                await io_mod.attach_io(ws, svc, "_", "alice")
            # The reply reached the router with the frame's event id.
            assert len(submitted) == 1
            assert submitted[0].event_id == "evt-1"
            assert submitted[0].action_id == "confirm"
        finally:
            await t.shutdown()

    async def test_multi_creature_graph_registers_channels_and_replays_history(
        self,
    ):
        # When the bound creature lives in a multi-creature graph with a
        # shared channel, attach_io must subscribe to the channel AND
        # replay its pre-existing history to the new connection.
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .build()
        )
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id
            ch = t._environments[gid].shared_channels.get("chat")
            from kohakuterrarium.core.channel import ChannelMessage

            # A message that happened BEFORE the WS attached.
            await ch.send(ChannelMessage(sender="bob", content="earlier"))

            for cid in ("alice", "bob"):
                ag = t.get_creature(cid).agent
                ag.output_router = SimpleNamespace(
                    add_secondary=lambda x: None,
                    remove_secondary=lambda x: None,
                    submit_reply_with_status=lambda r: (True, "ok"),
                )
                ag.config = SimpleNamespace(model="m")
            ws = _FakeWebSocket()  # disconnects immediately
            with pytest.raises(RuntimeError):
                await io_mod.attach_io(ws, svc, gid, "alice")
            # The pre-attach channel message was replayed as history.
            history_frames = [s for s in ws.sent if s.get("type") == "channel_message"]
            assert any(
                f.get("history") and f["content"] == "earlier" for f in history_frames
            )
        finally:
            await t.shutdown()

    async def test_sibling_lookup_failure_is_skipped(self, monkeypatch):
        # If a sibling creature id is in the graph topology but
        # engine.get_creature raises for it (transient race), attach_io
        # must skip that sibling, not abort the whole attach.
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_creature("bob").build()
        )
        svc = LocalTerrariumService(t)
        try:
            for cid in ("alice", "bob"):
                ag = t.get_creature(cid).agent
                ag.output_router = SimpleNamespace(
                    add_secondary=lambda x: None,
                    remove_secondary=lambda x: None,
                    submit_reply_with_status=lambda r: (True, "ok"),
                )
                ag.config = SimpleNamespace(model="m")
            gid = t.get_creature("alice").graph_id
            real_get = t.get_creature

            def _flaky_get(cid):
                if cid == "bob":
                    raise KeyError("bob vanished mid-attach")
                return real_get(cid)

            monkeypatch.setattr(t, "get_creature", _flaky_get)
            ws = _FakeWebSocket()
            # The KeyError for the sibling is swallowed; the attach still
            # reaches its (disconnect-driven) end.
            with pytest.raises(RuntimeError):
                await io_mod.attach_io(ws, svc, gid, "alice")
            assert any(s.get("activity_type") == "session_info" for s in ws.sent)
        finally:
            await t.shutdown()

    async def test_attach_sends_session_info_per_creature(self):
        # The frontend keys its per-tab model map off ``source`` — every
        # graph member must get its own session_info frame at attach,
        # not just the bound creature. (The old single-frame behaviour
        # left sibling tabs showing the primary creature's model until
        # that sibling's next model switch.)
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_creature("bob").build()
        )
        svc = LocalTerrariumService(t)
        try:
            for cid in ("alice", "bob"):
                creature = t.get_creature(cid)
                creature.config_name = f"{cid}-config"
                creature.config_ref = f"@pack/creatures/{cid}"
                ag = creature.agent
                ag.output_router = SimpleNamespace(
                    add_secondary=lambda x: None,
                    remove_secondary=lambda x: None,
                    submit_reply_with_status=lambda r: (True, "ok"),
                )
            gid = t.get_creature("alice").graph_id
            ws = _FakeWebSocket()
            with pytest.raises(RuntimeError):
                await io_mod.attach_io(ws, svc, gid, "alice")
            infos = {
                s["source"]: s
                for s in ws.sent
                if s.get("activity_type") == "session_info"
            }
            assert set(infos) == {"alice", "bob"}
            # Frames mirror the switch_model metadata shape so the
            # frontend per-tab map gets identical fields either way.
            for source, frame in infos.items():
                assert "llm_name" in frame
                assert "model" in frame
                assert "max_context" in frame
                assert "compact_threshold" in frame
                assert frame["agent_name"] == source
                assert frame["config_name"] == f"{source}-config"
                assert frame["config_ref"] == f"@pack/creatures/{source}"
        finally:
            await t.shutdown()

    async def test_cleanup_swallows_secondary_removal_failures(self):
        # On disconnect, attach_io detaches its output sinks + channel
        # callbacks. If remove_secondary / remove_on_send raise, those
        # failures must be swallowed so cleanup of the rest still runs.
        t = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .build()
        )
        svc = LocalTerrariumService(t)
        try:
            gid = t.get_creature("alice").graph_id

            def _boom_remove(x):
                raise RuntimeError("remove_secondary exploded")

            for cid in ("alice", "bob"):
                ag = t.get_creature(cid).agent
                ag.output_router = SimpleNamespace(
                    add_secondary=lambda x: None,
                    remove_secondary=_boom_remove,
                    submit_reply_with_status=lambda r: (True, "ok"),
                )
                ag.config = SimpleNamespace(model="m")
            # Make channel callback removal raise too.
            ch = t._environments[gid].shared_channels.get("chat")
            orig_remove = ch.remove_on_send

            def _boom_ch_remove(cb):
                raise RuntimeError("remove_on_send exploded")

            ch.remove_on_send = _boom_ch_remove
            ws = _FakeWebSocket()
            try:
                # All three cleanup failures (main sink, sibling sink,
                # channel cb) are swallowed — only the WS disconnect
                # RuntimeError propagates.
                with pytest.raises(RuntimeError, match="disconnect"):
                    await io_mod.attach_io(ws, svc, gid, "alice")
            finally:
                ch.remove_on_send = orig_remove
        finally:
            await t.shutdown()


# ── _attach_io_remote ───────────────────────────────────────


class TestAttachIoRemote:
    async def test_unresolved_home_raises(self):
        async def _r(cid):
            return None

        svc = SimpleNamespace(_resolve_home=_r)
        info = SimpleNamespace(creature_id="cid")
        with pytest.raises(KeyError):
            await io_mod._attach_io_remote(MagicMock(), svc, info, "_")

    async def test_host_home_raises(self):
        async def _r(cid):
            return "_host"

        svc = SimpleNamespace(_resolve_home=_r)
        info = SimpleNamespace(creature_id="cid")
        with pytest.raises(KeyError):
            await io_mod._attach_io_remote(MagicMock(), svc, info, "_")

    async def test_routes_through_proxy(self, monkeypatch):
        called = {}

        async def _fake_proxy(**kw):
            called.update(kw)

        monkeypatch.setattr(io_mod, "proxy_ws_to_lab", _fake_proxy)

        async def _r(cid):
            return "worker-1"

        svc = SimpleNamespace(_resolve_home=_r, host="HOST", demux="DEMUX")
        info = SimpleNamespace(creature_id="cid-1")
        await io_mod._attach_io_remote(MagicMock(), svc, info, "sid")
        assert called["target_node"] == "worker-1"
        assert called["namespace"] == "terrarium.attach"


# ── attach_io: remote fallback ─────────────────────────────


class TestAttachIoRemoteFallback:
    async def test_remote_creature_dispatched(self, monkeypatch):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        # Override service to make get_creature_info return a fake info.
        from kohakuterrarium.terrarium.service import CreatureInfo

        info = CreatureInfo(
            creature_id="remote-cid",
            name="r",
            graph_id="g-remote",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )

        async def _info(cid):
            return info

        svc.get_creature_info = _info
        called = []

        async def _attach_remote(ws, svc, info, sid):
            called.append((ws, info, sid))

        monkeypatch.setattr(io_mod, "_attach_io_remote", _attach_remote)
        ws = _FakeWebSocket()
        try:
            await io_mod.attach_io(ws, svc, "_", "remote-cid")
            assert called
        finally:
            await t.shutdown()

    async def test_remote_creature_unknown_reraises(self):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)

        async def _info(cid):
            return None

        svc.get_creature_info = _info
        try:
            with pytest.raises(KeyError):
                await io_mod.attach_io(_FakeWebSocket(), svc, "_", "ghost")
        finally:
            await t.shutdown()


# ── attach_io: lab-host name resolution (regression) ───────────


class _FakeMultiNodeService:
    """A ``MultiNodeTerrariumService``-shaped fake.

    ``connected_nodes`` makes ``host_engine_or_none`` return ``None``
    (the lab-host path — no host agent engine).  ``get_creature_info``
    is id-only, exactly like the real service Protocol; the bug under
    test is that ``attach_io`` in lab-host mode passes the raw URL
    segment straight to ``get_creature_info`` without the name→id
    resolution the standalone ``find_creature`` path performs.
    """

    def __init__(self, infos):
        self._by_id = {i.creature_id: i for i in infos}
        self.host = "HOST"
        self.demux = "DEMUX"

    def connected_nodes(self):
        return ("worker-1",)

    async def list_creatures(self):
        return tuple(self._by_id.values())

    async def get_creature_info(self, cid):
        return self._by_id.get(cid)  # id-only — names do NOT resolve

    async def _resolve_home(self, cid):
        return "worker-1" if cid in self._by_id else None


class TestAttachIoLabHostNameResolution:
    """In lab-host mode the chat WS attaches by the creature's display
    name (the frontend keys its chat tab off the friendly name). The
    standalone path resolves names via ``find_creature``; the lab-host
    path must resolve them too — otherwise the WebSocket closes with
    ``creature '<name>' not found`` and the user can't attach."""

    async def test_attach_by_name_resolves_to_remote(self, monkeypatch):
        from kohakuterrarium.terrarium.service import CreatureInfo

        info = CreatureInfo(
            creature_id="alice_abc12345",
            name="alice",
            graph_id="g1",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        svc = _FakeMultiNodeService([info])
        dispatched = []

        async def _attach_remote(ws, service, creature_info, sid):
            dispatched.append(creature_info.creature_id)

        monkeypatch.setattr(io_mod, "_attach_io_remote", _attach_remote)
        # Attaching by the display NAME must resolve to the worker
        # creature — not raise KeyError.
        await io_mod.attach_io(_FakeWebSocket(), svc, "g1", "alice")
        assert dispatched == ["alice_abc12345"], (
            "attach_io did not resolve the creature by display name in "
            "lab-host mode — the chat WebSocket closes 'creature not found'"
        )

    async def test_attach_by_id_still_resolves(self, monkeypatch):
        # The id form must keep working alongside name resolution.
        from kohakuterrarium.terrarium.service import CreatureInfo

        info = CreatureInfo(
            creature_id="bob_def67890",
            name="bob",
            graph_id="g1",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        svc = _FakeMultiNodeService([info])
        dispatched = []

        async def _attach_remote(ws, service, creature_info, sid):
            dispatched.append(creature_info.creature_id)

        monkeypatch.setattr(io_mod, "_attach_io_remote", _attach_remote)
        await io_mod.attach_io(_FakeWebSocket(), svc, "g1", "bob_def67890")
        assert dispatched == ["bob_def67890"]

    async def test_attach_genuinely_unknown_still_raises(self):
        svc = _FakeMultiNodeService([])
        with pytest.raises(KeyError):
            await io_mod.attach_io(_FakeWebSocket(), svc, "g1", "nobody")
