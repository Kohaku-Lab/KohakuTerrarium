"""Unit tests for ``tools_group_send``, ``tools_group_wire`` and
``tools_group_channel``.

Patches resolve_* helpers to avoid full engine setup; focuses on
branch behaviour (privilege gates, error formatting, dispatch).
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock


import kohakuterrarium.terrarium.tools_group_channel as channel_mod
import kohakuterrarium.terrarium.tools_group_send as send_mod
import kohakuterrarium.terrarium.tools_group_wire as wire_mod


class _FakeCreature:
    def __init__(
        self,
        cid="cid",
        name="alice",
        graph_id="g1",
        is_privileged=False,
        is_running=True,
        listen_channels=None,
        send_channels=None,
    ):
        self.creature_id = cid
        self.name = name
        self.graph_id = graph_id
        self.is_privileged = is_privileged
        self.is_running = is_running
        self.listen_channels = listen_channels or []
        self.send_channels = send_channels or []
        self.agent = SimpleNamespace(
            _process_event=AsyncMock(),
            trigger_manager=SimpleNamespace(_triggers={}),
        )


class _FakeChannelInfo:
    def __init__(self, name="chat", description="d"):
        self.name = name
        self.description = description


class _FakeGraph:
    def __init__(self, gid="g1", channels=None, send_edges=None, listen_edges=None):
        self.graph_id = gid
        self.creature_ids = {"caller", "target"}
        self.channels = channels or {}
        self.send_edges = send_edges or {}
        self.listen_edges = listen_edges or {}


class _FakeEngine:
    def __init__(self):
        self.add_channel = AsyncMock(return_value=_FakeChannelInfo())
        self.remove_channel = AsyncMock(return_value=SimpleNamespace(kind="nothing"))
        self.connect = AsyncMock()
        self.wire_output = AsyncMock(return_value="edge-1")
        self.unwire_output = AsyncMock(return_value=True)
        self._environments = {}
        self._topology = SimpleNamespace()
        self.emitted = []

    def _emit(self, event):
        self.emitted.append(event)


def _gctx(
    caller=None,
    engine=None,
    graph=None,
):
    return SimpleNamespace(
        engine=engine or _FakeEngine(),
        caller=caller
        or _FakeCreature(cid="caller", name="root", is_privileged=True, graph_id="g1"),
        graph=graph or _FakeGraph(),
    )


def _parse(r):
    return json.loads(r.output)


# ─── group_send ──────────────────────────────────────────────


class TestGroupSend:
    async def test_resolve_error(self, monkeypatch):
        sentinel = send_mod.err("bad")
        monkeypatch.setattr(
            send_mod, "resolve_or_error", lambda c, **_: (None, sentinel)
        )
        r = await send_mod.GroupSendTool()._execute({"to": "x", "message": "m"})
        assert r.error == "bad"

    async def test_missing_fields(self, monkeypatch):
        monkeypatch.setattr(
            send_mod, "resolve_or_error", lambda c, **_: (_gctx(), None)
        )
        r = await send_mod.GroupSendTool()._execute({"to": "", "message": None})
        assert "are required" in r.error

    async def test_target_not_in_group(self, monkeypatch):
        monkeypatch.setattr(
            send_mod, "resolve_or_error", lambda c, **_: (_gctx(), None)
        )
        monkeypatch.setattr(send_mod, "resolve_group_target", lambda g, n: None)
        r = await send_mod.GroupSendTool()._execute({"to": "ghost", "message": "m"})
        assert "not in your group" in r.error

    async def test_target_not_running(self, monkeypatch):
        monkeypatch.setattr(
            send_mod, "resolve_or_error", lambda c, **_: (_gctx(), None)
        )
        target = _FakeCreature(is_running=False)
        monkeypatch.setattr(send_mod, "resolve_group_target", lambda g, n: target)
        r = await send_mod.GroupSendTool()._execute({"to": "t", "message": "m"})
        assert "is not running" in r.error

    async def test_non_privileged_to_non_privileged_rejected(self, monkeypatch):
        gctx = _gctx(
            caller=_FakeCreature(cid="caller", name="worker", is_privileged=False)
        )
        monkeypatch.setattr(send_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        target = _FakeCreature(is_privileged=False)
        monkeypatch.setattr(send_mod, "resolve_group_target", lambda g, n: target)
        r = await send_mod.GroupSendTool()._execute({"to": "t", "message": "m"})
        assert "non-privileged" in r.error

    async def test_delivery_success(self, monkeypatch):
        gctx = _gctx()
        monkeypatch.setattr(send_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        target = _FakeCreature(name="bob")
        monkeypatch.setattr(send_mod, "resolve_group_target", lambda g, n: target)
        r = await send_mod.GroupSendTool()._execute({"to": "bob", "message": "hi"})
        body = _parse(r)
        assert body["delivered"] is True
        assert body["to"] == "cid"


# ─── log_send_error helper ───────────────────────────────────


class TestLogSendError:
    def test_cancelled_returns(self):
        class _T:
            def cancelled(self):
                return True

        send_mod._log_send_error(_T(), "a", "b")

    def test_no_exception_returns(self):
        class _T:
            def cancelled(self):
                return False

            def exception(self):
                return None

        send_mod._log_send_error(_T(), "a", "b")

    def test_logs_exception(self):
        class _T:
            def cancelled(self):
                return False

            def exception(self):
                return RuntimeError("oops")

        # Just exercises the warning branch.
        send_mod._log_send_error(_T(), "a", "b")


# ─── send_channel ────────────────────────────────────────────


class TestSendChannel:
    async def test_missing_fields(self, monkeypatch):
        gctx = _gctx()
        monkeypatch.setattr(send_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        r = await send_mod.SendChannelTool()._execute({"channel": "", "message": None})
        assert "are required" in r.error

    async def test_channel_missing(self, monkeypatch):
        gctx = _gctx(graph=_FakeGraph(channels={}))
        monkeypatch.setattr(send_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        r = await send_mod.SendChannelTool()._execute(
            {"channel": "ghost", "message": "m"}
        )
        assert "does not exist" in r.error

    async def test_not_wired_privileged_self_wire_hint(self, monkeypatch):
        gctx = _gctx(
            graph=_FakeGraph(
                channels={"chat": _FakeChannelInfo()}, send_edges={"caller": set()}
            )
        )
        monkeypatch.setattr(send_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        r = await send_mod.SendChannelTool()._execute(
            {"channel": "chat", "message": "m"}
        )
        assert "Self-wire via group_channel" in r.error

    async def test_not_wired_non_privileged_hint(self, monkeypatch):
        gctx = _gctx(
            caller=_FakeCreature(cid="caller", name="worker", is_privileged=False),
            graph=_FakeGraph(
                channels={"chat": _FakeChannelInfo()}, send_edges={"caller": set()}
            ),
        )
        monkeypatch.setattr(send_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        r = await send_mod.SendChannelTool()._execute(
            {"channel": "chat", "message": "m"}
        )
        assert "Ask the privileged creature" in r.error

    async def test_no_registry(self, monkeypatch):
        gctx = _gctx(
            graph=_FakeGraph(
                channels={"chat": _FakeChannelInfo()},
                send_edges={"caller": {"chat"}},
            )
        )
        monkeypatch.setattr(send_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        r = await send_mod.SendChannelTool()._execute(
            {"channel": "chat", "message": "m"}
        )
        assert "no live channel" in r.error

    async def test_channel_not_registered(self, monkeypatch):
        gctx = _gctx(
            graph=_FakeGraph(
                channels={"chat": _FakeChannelInfo()},
                send_edges={"caller": {"chat"}},
            )
        )
        gctx.engine._environments["g1"] = SimpleNamespace(
            shared_channels=SimpleNamespace(get=lambda n: None)
        )
        monkeypatch.setattr(send_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        r = await send_mod.SendChannelTool()._execute(
            {"channel": "chat", "message": "m"}
        )
        assert "not registered live" in r.error

    async def test_send_success(self, monkeypatch):
        gctx = _gctx(
            graph=_FakeGraph(
                channels={"chat": _FakeChannelInfo()},
                send_edges={"caller": {"chat"}},
            )
        )
        fake_ch = SimpleNamespace(send=AsyncMock())
        gctx.engine._environments["g1"] = SimpleNamespace(
            shared_channels=SimpleNamespace(get=lambda n: fake_ch)
        )
        monkeypatch.setattr(send_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        r = await send_mod.SendChannelTool()._execute(
            {"channel": "chat", "message": "m", "metadata": {"k": 1}}
        )
        body = _parse(r)
        assert body["channel"] == "chat"
        fake_ch.send.assert_awaited_once()


# ─── group_wire ──────────────────────────────────────────────


class TestGroupWire:
    async def test_from_unknown(self, monkeypatch):
        monkeypatch.setattr(
            wire_mod, "resolve_or_error", lambda c, **_: (_gctx(), None)
        )
        monkeypatch.setattr(wire_mod, "resolve_group_target", lambda g, n: None)
        r = await wire_mod.GroupWireTool()._execute({"action": "add"})
        assert "not in your group" in r.error

    async def test_add_missing_to(self, monkeypatch):
        gctx = _gctx()
        monkeypatch.setattr(wire_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        monkeypatch.setattr(
            wire_mod,
            "resolve_group_target",
            lambda g, n: gctx.caller,
        )
        r = await wire_mod.GroupWireTool()._execute({"action": "add"})
        assert "'to' is required" in r.error

    async def test_add_to_unknown(self, monkeypatch):
        gctx = _gctx()
        monkeypatch.setattr(wire_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        # First call returns from_creature (caller), second call returns None
        seq = iter([gctx.caller, None])
        monkeypatch.setattr(wire_mod, "resolve_group_target", lambda g, n: next(seq))
        r = await wire_mod.GroupWireTool()._execute({"action": "add", "to": "ghost"})
        assert "not in your group" in r.error

    async def test_add_same_graph_success(self, monkeypatch):
        gctx = _gctx()
        target = _FakeCreature(cid="t", name="bob", graph_id="g1")
        seq = iter([gctx.caller, target])
        monkeypatch.setattr(wire_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        monkeypatch.setattr(wire_mod, "resolve_group_target", lambda g, n: next(seq))
        r = await wire_mod.GroupWireTool()._execute(
            {
                "action": "add",
                "to": "bob",
                "with_content": True,
                "prompt": "say hi",
                "prompt_format": "jinja",
                "allow_self_trigger": True,
            }
        )
        body = _parse(r)
        assert body["edge_id"] == "edge-1"
        assert body["to"] == "t"

    async def test_add_cross_graph_merges(self, monkeypatch):
        gctx = _gctx()
        target = _FakeCreature(cid="t", name="bob", graph_id="g-other")
        seq = iter([gctx.caller, target])
        monkeypatch.setattr(wire_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        monkeypatch.setattr(wire_mod, "resolve_group_target", lambda g, n: next(seq))
        ensure_called = {}

        async def ensure_same_graph(e, f, t):
            ensure_called["called"] = True

        monkeypatch.setattr(wire_mod._channels, "ensure_same_graph", ensure_same_graph)
        await wire_mod.GroupWireTool()._execute({"action": "add", "to": "bob"})
        assert ensure_called["called"]

    async def test_add_wire_output_failure(self, monkeypatch):
        gctx = _gctx()
        gctx.engine.wire_output.side_effect = RuntimeError("fail")
        target = _FakeCreature(cid="t", graph_id="g1")
        seq = iter([gctx.caller, target])
        monkeypatch.setattr(wire_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        monkeypatch.setattr(wire_mod, "resolve_group_target", lambda g, n: next(seq))
        r = await wire_mod.GroupWireTool()._execute({"action": "add", "to": "bob"})
        assert "wire_output failed" in r.error

    async def test_remove_missing_edge_id(self, monkeypatch):
        gctx = _gctx()
        monkeypatch.setattr(wire_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        monkeypatch.setattr(wire_mod, "resolve_group_target", lambda g, n: gctx.caller)
        r = await wire_mod.GroupWireTool()._execute({"action": "remove"})
        assert "'edge_id' is required" in r.error

    async def test_remove_failure(self, monkeypatch):
        gctx = _gctx()
        gctx.engine.unwire_output.side_effect = RuntimeError("nope")
        monkeypatch.setattr(wire_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        monkeypatch.setattr(wire_mod, "resolve_group_target", lambda g, n: gctx.caller)
        r = await wire_mod.GroupWireTool()._execute(
            {"action": "remove", "edge_id": "e-1"}
        )
        assert "unwire_output failed" in r.error

    async def test_remove_success(self, monkeypatch):
        gctx = _gctx()
        monkeypatch.setattr(wire_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        monkeypatch.setattr(wire_mod, "resolve_group_target", lambda g, n: gctx.caller)
        r = await wire_mod.GroupWireTool()._execute(
            {"action": "remove", "edge_id": "e-1"}
        )
        body = _parse(r)
        assert body["edge_id"] == "e-1"

    async def test_unknown_action(self, monkeypatch):
        gctx = _gctx()
        monkeypatch.setattr(wire_mod, "resolve_or_error", lambda c, **_: (gctx, None))
        monkeypatch.setattr(wire_mod, "resolve_group_target", lambda g, n: gctx.caller)
        r = await wire_mod.GroupWireTool()._execute({"action": "garbage"})
        assert "unknown action" in r.error


# ─── group_channel ───────────────────────────────────────────


class TestGroupChannel:
    async def test_missing_args(self, monkeypatch):
        gctx = _gctx()
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        r = await channel_mod.GroupChannelTool()._execute({"action": "", "channel": ""})
        assert "are required" in r.error

    async def test_create_already_exists(self, monkeypatch):
        gctx = _gctx(graph=_FakeGraph(channels={"chat": _FakeChannelInfo()}))
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        r = await channel_mod.GroupChannelTool()._execute(
            {"action": "create", "channel": "chat"}
        )
        assert "already exists" in r.error

    async def test_create_failure(self, monkeypatch):
        gctx = _gctx(graph=_FakeGraph(channels={}))
        gctx.engine.add_channel.side_effect = RuntimeError("no")
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        r = await channel_mod.GroupChannelTool()._execute(
            {"action": "create", "channel": "chat"}
        )
        assert "add_channel failed" in r.error

    async def test_create_success(self, monkeypatch):
        gctx = _gctx(graph=_FakeGraph(channels={}))
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        r = await channel_mod.GroupChannelTool()._execute(
            {"action": "create", "channel": "chat", "description": "d"}
        )
        body = _parse(r)
        assert body["created"] == "chat"

    async def test_delete_unknown(self, monkeypatch):
        gctx = _gctx(graph=_FakeGraph(channels={}))
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        r = await channel_mod.GroupChannelTool()._execute(
            {"action": "delete", "channel": "ghost"}
        )
        assert "not in your graph" in r.error

    async def test_delete_failure(self, monkeypatch):
        gctx = _gctx(graph=_FakeGraph(channels={"chat": _FakeChannelInfo()}))
        gctx.engine.remove_channel.side_effect = RuntimeError("no")
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        r = await channel_mod.GroupChannelTool()._execute(
            {"action": "delete", "channel": "chat"}
        )
        assert "remove_channel failed" in r.error

    async def test_delete_success(self, monkeypatch):
        gctx = _gctx(graph=_FakeGraph(channels={"chat": _FakeChannelInfo()}))
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        r = await channel_mod.GroupChannelTool()._execute(
            {"action": "delete", "channel": "chat"}
        )
        body = _parse(r)
        assert body["deleted"] == "chat"

    async def test_wire_invalid_direction(self, monkeypatch):
        gctx = _gctx()
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        monkeypatch.setattr(
            channel_mod, "resolve_group_target", lambda g, n: gctx.caller
        )
        r = await channel_mod.GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "chat",
                "creature_id": "t",
                "direction": "garbage",
            }
        )
        assert "must be" in r.error

    async def test_wire_target_unknown(self, monkeypatch):
        gctx = _gctx()
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        monkeypatch.setattr(channel_mod, "resolve_group_target", lambda g, n: None)
        r = await channel_mod.GroupChannelTool()._execute(
            {
                "action": "wire",
                "channel": "chat",
                "creature_id": "ghost",
                "direction": "listen",
            }
        )
        assert "not in your group" in r.error

    async def test_unknown_action(self, monkeypatch):
        gctx = _gctx()
        target = _FakeCreature(graph_id="g1")
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        monkeypatch.setattr(channel_mod, "resolve_group_target", lambda g, n: target)
        r = await channel_mod.GroupChannelTool()._execute(
            {
                "action": "bogus",
                "channel": "chat",
                "creature_id": "t",
                "direction": "listen",
            }
        )
        assert "unknown action" in r.error

    async def test_unwire_cross_graph_rejected(self, monkeypatch):
        gctx = _gctx()
        target = _FakeCreature(graph_id="g-other")
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        monkeypatch.setattr(channel_mod, "resolve_group_target", lambda g, n: target)
        r = await channel_mod.GroupChannelTool()._execute(
            {
                "action": "unwire",
                "channel": "chat",
                "creature_id": "t",
                "direction": "listen",
            }
        )
        assert "not in your graph" in r.error

    async def test_unwire_unknown_channel(self, monkeypatch):
        gctx = _gctx(graph=_FakeGraph(channels={}))
        target = _FakeCreature(graph_id="g1")
        monkeypatch.setattr(
            channel_mod, "resolve_or_error", lambda c, **_: (gctx, None)
        )
        monkeypatch.setattr(channel_mod, "resolve_group_target", lambda g, n: target)
        r = await channel_mod.GroupChannelTool()._execute(
            {
                "action": "unwire",
                "channel": "ghost",
                "creature_id": "t",
                "direction": "listen",
            }
        )
        assert "not in your graph" in r.error
