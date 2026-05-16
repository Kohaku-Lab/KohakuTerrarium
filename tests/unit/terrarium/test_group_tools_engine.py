"""Behaviour tests for the privileged ``group_*`` tool surface, driven
against a *real* :class:`Terrarium` engine via ``TestTerrariumBuilder``.

The existing ``test_tools_group_*`` files patch ``resolve_or_error`` and
``resolve_group_target`` to skip engine wiring — useful for branch
coverage but they never assert the actual invariants. These tests run
the tools through a live engine and assert the contract from CLAUDE.md:

- privileged-only ``group_*`` tools are registered only on privileged
  creatures (and ``send_channel`` / ``group_send`` on every creature);
- ``group_channel`` mutations keep the graph a connected component;
- ``group_status`` reports the caller's true group membership;
- ``group_channel(action='wire')`` cross-graph merges the two graphs;
- ``group_channel(action='unwire')`` of the only bridge auto-splits;
- exact ``EngineEvent`` kinds are emitted per mutation.
"""

import json
from pathlib import Path

from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium import tools_group as tg
from kohakuterrarium.terrarium import tools_group_channel as channel_mod
from kohakuterrarium.terrarium import tools_group_send as send_mod
from kohakuterrarium.terrarium import tools_group_status as status_mod
from kohakuterrarium.terrarium.events import EventKind
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _RegistryAgent:
    """Minimal agent-like with a real :class:`Registry` so the
    ``force_register_*`` helpers actually register tools."""

    def __init__(self):
        self.registry = Registry()
        self.executor = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ctx_for(engine, creature):
    """Build a ToolContext that resolves to ``creature`` in ``engine``."""
    env = engine._environments[creature.graph_id]
    return ToolContext(
        agent_name=creature.name,
        session=None,
        working_dir=Path("."),
        environment=env,
    )


def _parse(result):
    return json.loads(result.output)


async def _privileged_pair():
    """Engine with a privileged ``root`` + worker ``bob`` in one graph."""
    t = await TestTerrariumBuilder().with_creature("root").with_creature("bob").build()
    t.get_creature("root").is_privileged = True
    return t


# ---------------------------------------------------------------------------
# registration tiers
# ---------------------------------------------------------------------------


class TestToolRegistrationTiers:
    def test_basic_tools_on_every_creature(self):
        """``send_channel`` + ``group_send`` are force-registered on
        every engine creature regardless of privilege; the graph-mutating
        surface is withheld from a non-privileged creature."""
        agent = _RegistryAgent()
        tg.force_register_basic_tools(agent)
        names = set(agent.registry.list_tools())
        assert "send_channel" in names
        assert "group_send" in names
        # A non-privileged creature must NOT receive the
        # graph-mutating surface.
        assert "group_add_node" not in names
        assert "group_channel" not in names

    def test_privileged_tools_only_after_elevation(self):
        """``force_register_privileged_tools`` adds the full
        graph-mutating surface."""
        agent = _RegistryAgent()
        tg.force_register_privileged_tools(agent)
        names = set(agent.registry.list_tools())
        for priv in tg.PRIVILEGED_TOOL_NAMES:
            assert priv in names

    def test_register_is_idempotent(self):
        """Re-registering does not duplicate tools in the registry."""
        agent = _RegistryAgent()
        tg.force_register_group_tools(agent)
        first = sorted(agent.registry.list_tools())
        tg.force_register_group_tools(agent)
        assert sorted(agent.registry.list_tools()) == first

    def test_register_skips_already_present_tool(self):
        """A tool already in the registry is left untouched."""
        agent = _RegistryAgent()
        pre = get_builtin_tool("send_channel")
        agent.registry.register_tool(pre)
        tg.force_register_basic_tools(agent)
        # Still the same instance — not replaced.
        assert agent.registry.get_tool("send_channel") is pre

    def test_register_named_noop_without_registry(self):
        """A bare object with no ``registry`` is silently skipped."""

        class _Bare:
            registry = None
            executor = None

        # Must not raise.
        tg._register_named(_Bare(), tg.ENGINE_BASIC_TOOL_NAMES)

    def test_register_named_noop_when_register_not_callable(self):
        class _Reg:
            register_tool = "not callable"

        class _Agent:
            registry = _Reg()
            executor = None

        tg._register_named(_Agent(), tg.ENGINE_BASIC_TOOL_NAMES)


# ---------------------------------------------------------------------------
# group_status — real group snapshot
# ---------------------------------------------------------------------------


class TestGroupStatusBehaviour:
    async def test_status_lists_group_members_and_self(self):
        t = await _privileged_pair()
        try:
            root = t.get_creature("root")
            tool = status_mod.GroupStatusTool()
            result = await tool._execute({}, context=_ctx_for(t, root))
            body = _parse(result)
            assert body["self"]["creature_id"] == "root"
            assert body["self"]["is_privileged"] is True
            ids = {c["creature_id"] for c in body["creatures"]}
            # Both creatures share the graph → both in the snapshot.
            assert ids == {"root", "bob"}
            assert body["graph_id"] == root.graph_id
        finally:
            await t.shutdown()

    async def test_status_reports_channels_with_listeners_and_senders(self):
        t = await (
            TestTerrariumBuilder()
            .with_creature("root")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("root", "bob", channel="chat")
            .build()
        )
        t.get_creature("root").is_privileged = True
        try:
            root = t.get_creature("root")
            tool = status_mod.GroupStatusTool()
            result = await tool._execute({}, context=_ctx_for(t, root))
            body = _parse(result)
            chat = next(c for c in body["channels"] if c["name"] == "chat")
            # connect("root", "bob") → root sends, bob listens.
            assert "root" in chat["senders"]
            assert "bob" in chat["listeners"]
        finally:
            await t.shutdown()

    async def test_status_rejects_non_privileged_caller(self):
        """``group_status`` requires a privileged caller — a worker
        gets a clean error, not a snapshot."""
        t = await _privileged_pair()
        try:
            bob = t.get_creature("bob")  # not privileged
            tool = status_mod.GroupStatusTool()
            result = await tool._execute({}, context=_ctx_for(t, bob))
            assert result.error
            assert "privileged" in result.error
        finally:
            await t.shutdown()

    async def test_status_reports_output_wires(self):
        """An output-wire edge added via ``engine.wire_output`` shows up
        in the ``output_edges`` of the caller's group snapshot."""
        t = await _privileged_pair()
        try:
            root = t.get_creature("root")
            await t.wire_output("root", {"to": "bob", "with_content": True})
            tool = status_mod.GroupStatusTool()
            result = await tool._execute({}, context=_ctx_for(t, root))
            body = _parse(result)
            assert body["output_edges"]
            edge = body["output_edges"][0]
            assert edge["from"] == "root"
            assert edge["to"] == "bob"
        finally:
            await t.shutdown()

    async def test_status_include_history_serializes_messages(self):
        t = await (
            TestTerrariumBuilder()
            .with_creature("root")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("root", "bob", channel="chat")
            .build()
        )
        t.get_creature("root").is_privileged = True
        try:
            root = t.get_creature("root")
            # Push a message through the live channel.
            from kohakuterrarium.core.channel import ChannelMessage

            env = t._environments[root.graph_id]
            ch = env.shared_channels.get("chat")
            await ch.send(ChannelMessage(sender="root", content="hi"))
            tool = status_mod.GroupStatusTool()
            result = await tool._execute(
                {"include_history": True, "history_limit": 5},
                context=_ctx_for(t, root),
            )
            body = _parse(result)
            chat = next(c for c in body["channels"] if c["name"] == "chat")
            assert chat["history"]
            assert chat["history"][0]["content"] == "hi"
        finally:
            await t.shutdown()


# ---------------------------------------------------------------------------
# group_channel — create / delete / wire / unwire on a live engine
# ---------------------------------------------------------------------------


class TestGroupChannelCreateDelete:
    async def test_create_channel_lands_in_topology(self):
        t = await _privileged_pair()
        try:
            root = t.get_creature("root")
            tool = channel_mod.GroupChannelTool()
            result = await tool._execute(
                {"action": "create", "channel": "ops", "description": "ops chan"},
                context=_ctx_for(t, root),
            )
            body = _parse(result)
            assert body["created"] == "ops"
            # The channel is now a real topology channel in the graph.
            graph = t.get_graph(root.graph_id)
            assert "ops" in graph.channels
            assert graph.channels["ops"].description == "ops chan"
        finally:
            await t.shutdown()

    async def test_delete_channel_removes_from_topology(self):
        t = await (
            TestTerrariumBuilder()
            .with_creature("root")
            .with_creature("bob")
            .with_channel("chat")
            .with_channel("ops")
            .with_connection("root", "bob", channel="chat")
            .build()
        )
        t.get_creature("root").is_privileged = True
        try:
            root = t.get_creature("root")
            tool = channel_mod.GroupChannelTool()
            result = await tool._execute(
                {"action": "delete", "channel": "ops"},
                context=_ctx_for(t, root),
            )
            body = _parse(result)
            assert body["deleted"] == "ops"
            assert "ops" not in t.get_graph(root.graph_id).channels
            # The graph stays a single connected component (chat still wires).
            assert len(t.list_graphs()) == 1
        finally:
            await t.shutdown()


class TestGroupChannelWire:
    async def test_intra_graph_wire_listen_adds_edge_and_emits(self):
        """``group_channel(action='wire', direction='listen')`` toggles
        the *target*'s listen edge and emits TOPOLOGY_CHANGED."""
        t = await (
            TestTerrariumBuilder()
            .with_creature("root")
            .with_creature("bob")
            .with_channel("chat")
            .build()
        )
        t.get_creature("root").is_privileged = True
        try:
            root = t.get_creature("root")
            bob = t.get_creature("bob")
            events = []
            t._subscribers.clear()

            # Subscribe before the mutation to capture the emit.
            import asyncio

            async def _collect():
                async for ev in t.subscribe():
                    events.append(ev)

            collector = asyncio.create_task(_collect())
            await asyncio.sleep(0)

            tool = channel_mod.GroupChannelTool()
            result = await tool._execute(
                {
                    "action": "wire",
                    "channel": "chat",
                    "creature_id": "bob",
                    "direction": "listen",
                },
                context=_ctx_for(t, root),
            )
            await asyncio.sleep(0.05)
            collector.cancel()

            body = _parse(result)
            assert body["wired"] == "chat"
            assert body["direction"] == "listen"
            # The target's listen edge is now set.
            assert "chat" in bob.listen_channels
            graph = t.get_graph(root.graph_id)
            assert "chat" in graph.listen_edges.get("bob", set())
            kinds = {ev.kind for ev in events}
            assert EventKind.TOPOLOGY_CHANGED in kinds
        finally:
            await t.shutdown()

    async def test_intra_graph_wire_send_adds_send_edge(self):
        t = await (
            TestTerrariumBuilder()
            .with_creature("root")
            .with_creature("bob")
            .with_channel("chat")
            .build()
        )
        t.get_creature("root").is_privileged = True
        try:
            root = t.get_creature("root")
            bob = t.get_creature("bob")
            tool = channel_mod.GroupChannelTool()
            result = await tool._execute(
                {
                    "action": "wire",
                    "channel": "chat",
                    "creature_id": "bob",
                    "direction": "send",
                },
                context=_ctx_for(t, root),
            )
            body = _parse(result)
            assert body["direction"] == "send"
            assert "chat" in bob.send_channels
            graph = t.get_graph(root.graph_id)
            assert "chat" in graph.send_edges.get("bob", set())
        finally:
            await t.shutdown()

    async def test_wire_auto_creates_missing_channel(self):
        """Wiring a not-yet-declared channel auto-creates it."""
        t = await _privileged_pair()
        try:
            root = t.get_creature("root")
            tool = channel_mod.GroupChannelTool()
            result = await tool._execute(
                {
                    "action": "wire",
                    "channel": "fresh",
                    "creature_id": "bob",
                    "direction": "listen",
                },
                context=_ctx_for(t, root),
            )
            assert _parse(result)["wired"] == "fresh"
            assert "fresh" in t.get_graph(root.graph_id).channels
        finally:
            await t.shutdown()

    async def test_cross_graph_wire_merges_graphs(self):
        """When the target is a spawned child still in its own
        singleton graph, ``wire`` routes through ``engine.connect`` and
        the two graphs merge — graph stays a connected component."""
        t = await (
            TestTerrariumBuilder()
            .with_creature("root")
            .with_creature("bob")
            .with_separate_graphs()
            .build()
        )
        t.get_creature("root").is_privileged = True
        # ``bob`` is a child of root → in root's *group* even though it
        # lives in its own graph (the freshly-spawned-worker case).
        t.get_creature("bob").parent_creature_id = "root"
        try:
            root = t.get_creature("root")
            bob = t.get_creature("bob")
            assert root.graph_id != bob.graph_id
            tool = channel_mod.GroupChannelTool()
            result = await tool._execute(
                {
                    "action": "wire",
                    "channel": "bridge",
                    "creature_id": "bob",
                    "direction": "listen",
                },
                context=_ctx_for(t, root),
            )
            body = _parse(result)
            assert body["merged"] is True
            # Invariant: graph = connected component → now one graph.
            assert root.graph_id == bob.graph_id
            assert len(t.list_graphs()) == 1
        finally:
            await t.shutdown()


class TestGroupChannelUnwire:
    async def test_unwire_listen_removes_edge(self):
        t = await (
            TestTerrariumBuilder()
            .with_creature("root")
            .with_creature("bob")
            .with_channel("chat")
            .with_channel("ops")
            .with_connection("root", "bob", channel="chat")
            .with_connection("root", "bob", channel="ops")
            .build()
        )
        t.get_creature("root").is_privileged = True
        try:
            root = t.get_creature("root")
            bob = t.get_creature("bob")
            assert "chat" in bob.listen_channels
            tool = channel_mod.GroupChannelTool()
            result = await tool._execute(
                {
                    "action": "unwire",
                    "channel": "chat",
                    "creature_id": "bob",
                    "direction": "listen",
                },
                context=_ctx_for(t, root),
            )
            body = _parse(result)
            assert body["unwired"] == "chat"
            # Edge gone; second channel keeps the graph connected.
            assert "chat" not in bob.listen_channels
            assert body["delta_kind"] == "nothing"
            assert len(t.list_graphs()) == 1
        finally:
            await t.shutdown()

    async def test_unwire_only_bridge_splits_graph(self):
        """Unwiring the sole bridge channel auto-splits the graph into
        two connected components."""
        t = await (
            TestTerrariumBuilder()
            .with_creature("root")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("root", "bob", channel="chat")
            .build()
        )
        t.get_creature("root").is_privileged = True
        try:
            root = t.get_creature("root")
            assert len(t.list_graphs()) == 1
            tool = channel_mod.GroupChannelTool()
            result = await tool._execute(
                {
                    "action": "unwire",
                    "channel": "chat",
                    "creature_id": "bob",
                    "direction": "listen",
                },
                context=_ctx_for(t, root),
            )
            body = _parse(result)
            assert body["delta_kind"] == "split"
            # Graph fragmented: root and bob are now in separate graphs.
            assert t.get_creature("root").graph_id != t.get_creature("bob").graph_id
            assert len(t.list_graphs()) == 2
        finally:
            await t.shutdown()

    async def test_unwire_send_direction_removes_send_edge(self):
        t = await (
            TestTerrariumBuilder()
            .with_creature("root")
            .with_creature("bob")
            .with_channel("chat")
            .with_channel("ops")
            .with_connection("root", "bob", channel="chat")
            .with_connection("root", "bob", channel="ops")
            .build()
        )
        t.get_creature("root").is_privileged = True
        try:
            root = t.get_creature("root")
            assert "chat" in root.send_channels
            tool = channel_mod.GroupChannelTool()
            result = await tool._execute(
                {
                    "action": "unwire",
                    "channel": "chat",
                    "creature_id": "root",
                    "direction": "send",
                },
                context=_ctx_for(t, root),
            )
            body = _parse(result)
            assert body["unwired"] == "chat"
            assert "chat" not in root.send_channels
        finally:
            await t.shutdown()


# ---------------------------------------------------------------------------
# tool metadata + error propagation (covers the property bodies)
# ---------------------------------------------------------------------------


class TestSendToolMetadata:
    def test_group_send_metadata(self):
        tool = send_mod.GroupSendTool()
        assert tool.tool_name == "group_send"
        assert tool.description
        assert tool.execution_mode.name == "DIRECT"
        assert "to" in tool.get_parameters_schema()["properties"]

    def test_send_channel_metadata(self):
        tool = send_mod.SendChannelTool()
        assert tool.tool_name == "send_channel"
        assert tool.description
        assert tool.execution_mode.name == "DIRECT"
        assert "channel" in tool.get_parameters_schema()["properties"]

    async def test_send_channel_propagates_resolution_error(self):
        """A ToolContext with no environment yields a clean error, not a
        crash — exercises the ``resolve_or_error`` error arm."""
        tool = send_mod.SendChannelTool()
        result = await tool._execute({"channel": "chat", "message": "m"}, context=None)
        assert result.error
        assert "environment" in result.error

    async def test_group_send_propagates_resolution_error(self):
        tool = send_mod.GroupSendTool()
        result = await tool._execute({"to": "x", "message": "m"}, context=None)
        assert result.error


class TestSendChannelBehaviour:
    async def test_send_channel_delivers_to_live_registry(self):
        """A privileged sender wired on a channel writes to the live
        registry — the message lands in channel history."""
        t = await (
            TestTerrariumBuilder()
            .with_creature("root")
            .with_creature("bob")
            .with_channel("chat")
            .with_connection("root", "bob", channel="chat")
            .build()
        )
        t.get_creature("root").is_privileged = True
        try:
            root = t.get_creature("root")
            tool = send_mod.SendChannelTool()
            result = await tool._execute(
                {"channel": "chat", "message": "broadcast me"},
                context=_ctx_for(t, root),
            )
            body = json.loads(result.output)
            assert body["channel"] == "chat"
            env = t._environments[root.graph_id]
            ch = env.shared_channels.get("chat")
            assert any(m.content == "broadcast me" for m in ch.history)
        finally:
            await t.shutdown()

    async def test_send_channel_rejects_unwired_sender(self):
        """A creature not wired as sender cannot broadcast — gets a
        hint instead of silently dropping the message."""
        t = await (
            TestTerrariumBuilder().with_creature("root").with_channel("chat").build()
        )
        t.get_creature("root").is_privileged = True
        try:
            root = t.get_creature("root")
            tool = send_mod.SendChannelTool()
            result = await tool._execute(
                {"channel": "chat", "message": "m"},
                context=_ctx_for(t, root),
            )
            assert result.error
            assert "not wired as sender" in result.error
        finally:
            await t.shutdown()
