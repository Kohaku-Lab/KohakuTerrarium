"""Unit tests for the legacy ``send_message`` builtin tool.

Pins behaviour of the engine-context send-edge gate: when a creature
inside a Terrarium graph carries a private session-channel that
shadows a graph-topology channel of the same name, the gate must
still block the broadcast if the creature is not wired as sender on
the topology channel.  Otherwise an unwired creature could call
``send_message(channel="ops")`` and silently succeed against its own
private queue while pretending to address the topology channel.
"""

from pathlib import Path

from kohakuterrarium.builtins.tools.send_message import SendMessageTool
from kohakuterrarium.core.session import Session
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


def _ctx_with_private_channel(
    engine, creature, private_channel_name: str
) -> ToolContext:
    """Build a ToolContext whose Session carries a private channel of
    ``private_channel_name`` while pointing at ``creature``'s engine
    environment.  Models a malformed resume snapshot where a private
    session-channel collides with a graph-topology channel name.
    """
    env = engine._environments[creature.graph_id]
    session = Session(key=f"{creature.creature_id}-private")
    session.channels.get_or_create(private_channel_name, channel_type="broadcast")
    return ToolContext(
        agent_name=creature.name,
        session=session,
        working_dir=Path("."),
        environment=env,
    )


class TestSendMessageEngineGate:
    async def test_send_message_blocks_when_only_private_channel_shadows_topology_channel(
        self,
    ):
        """A creature with a private session-channel "ops" that shadows
        a graph-topology channel "ops" — where the creature is NOT a
        wired sender — must be blocked by the send-edge gate.

        Bug shape: ``send_message`` resolved private channels first
        and only ran the gate when the *shared* channel resolved, so
        the private channel hid the topology channel from the gate
        and the broadcast silently succeeded on the wrong queue.

        Asserted behaviour: gate fires, returns the canonical "not
        wired as sender" error.  The private channel must not have
        received the message either.
        """
        engine = await (
            TestTerrariumBuilder().with_creature("alpha").with_channel("ops").build()
        )
        try:
            alpha = engine.get_creature("alpha")
            ctx = _ctx_with_private_channel(engine, alpha, "ops")
            tool = SendMessageTool()

            result = await tool._execute(
                {"channel": "ops", "message": "should be blocked"},
                context=ctx,
            )

            assert result.error, "expected gate to reject the unwired sender"
            assert "not wired as sender" in result.error
            # Behaviour: nothing was delivered to the private channel
            # that shadowed the topology name.
            private_ch = ctx.session.channels.get("ops")
            assert private_ch is not None
            assert not list(private_ch.history)
            # Behaviour: nothing was delivered to the topology channel
            # either (caller wasn't a wired sender).
            env = engine._environments[alpha.graph_id]
            topo_ch = env.shared_channels.get("ops")
            assert topo_ch is not None
            assert not list(topo_ch.history)
        finally:
            await engine.shutdown()

    async def test_send_message_allows_wired_sender_even_with_private_shadow(
        self,
    ):
        """Edge case: when a creature IS wired as sender on the
        topology channel and also has a private channel of the same
        name, the send must succeed and land on the cluster-visible
        topology channel (the canonical recipient)."""
        engine = await (
            TestTerrariumBuilder()
            .with_creature("alpha")
            .with_creature("bravo")
            .with_channel("ops")
            .with_connection("alpha", "bravo", channel="ops")
            .build()
        )
        try:
            alpha = engine.get_creature("alpha")
            ctx = _ctx_with_private_channel(engine, alpha, "ops")
            tool = SendMessageTool()

            result = await tool._execute(
                {"channel": "ops", "message": "delivered"},
                context=ctx,
            )

            assert result.error is None, result.error
            env = engine._environments[alpha.graph_id]
            topo_ch = env.shared_channels.get("ops")
            assert topo_ch is not None
            assert any(m.content == "delivered" for m in topo_ch.history)
        finally:
            await engine.shutdown()
