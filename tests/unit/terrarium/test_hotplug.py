"""Hot-plug tests for the Terrarium engine.

Covers cross-graph ``connect`` (which forces a graph merge plus
environment union) and the live add/remove of channel triggers.

Same-graph connect/disconnect and the split-on-disconnect path are
tested in ``test_channels.py``.
"""

import pytest

from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import EventFilter, EventKind

from tests.unit.terrarium._fakes import make_creature

# ---------------------------------------------------------------------------
# cross-graph connect → merge
# ---------------------------------------------------------------------------


class TestCrossGraphConnect:
    @pytest.mark.asyncio
    async def test_two_singletons_merge_on_connect(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"))
        assert a.graph_id != b.graph_id
        # Two graphs, two envs.
        assert len(engine._environments) == 2

        result = await engine.connect(a, b, channel="ab")
        assert result.delta_kind == "merge"
        # graph_id updated on both creatures.
        assert a.graph_id == b.graph_id
        # One graph, one env.
        assert len(engine._environments) == 1
        # The channel was registered in the surviving env.
        env = engine._environments[a.graph_id]
        assert env.shared_channels.get("ab") is not None

    @pytest.mark.asyncio
    async def test_merge_carries_existing_channels(self):
        engine = Terrarium()
        # graph A: alice + an "intra" channel listened on by alice
        a = await engine.add_creature(make_creature("alice"))
        await engine.add_channel(a.graph_id, "intra")
        # alice listens on intra (set via topology directly)
        from kohakuterrarium.terrarium import topology as topo

        topo.set_listen(engine._topology, a.creature_id, "intra", listening=True)
        # ... and inject the live trigger for parity with connect()
        from kohakuterrarium.terrarium import channels as channels_mod

        channels_mod.inject_channel_trigger(
            a.agent,
            subscriber_id=a.name,
            channel_name="intra",
            registry=engine._environments[a.graph_id].shared_channels,
            ignore_sender=a.name,
        )

        # graph B: bob alone
        b = await engine.add_creature(make_creature("bob"))

        # Connect them — graphs merge.
        await engine.connect(a, b, channel="ab")
        merged_env = engine._environments[a.graph_id]
        # Both channels live in the surviving env.
        assert merged_env.shared_channels.get("intra") is not None
        assert merged_env.shared_channels.get("ab") is not None
        # alice's "intra" trigger has been re-pointed at the merged env.
        assert "channel_alice_intra" in a.agent.trigger_manager._triggers
        # bob has the new "ab" trigger.
        assert "channel_bob_ab" in b.agent.trigger_manager._triggers

    @pytest.mark.asyncio
    async def test_topology_event_emitted_on_merge(self):
        import asyncio

        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"))

        topo_events: list = []

        async def collect():
            async for ev in engine.subscribe(
                EventFilter(kinds={EventKind.TOPOLOGY_CHANGED})
            ):
                topo_events.append(ev)
                if topo_events:
                    return

        task = asyncio.create_task(collect())
        await asyncio.sleep(0)
        await engine.connect(a, b, channel="ab")
        await asyncio.wait_for(task, timeout=1.0)
        assert topo_events[0].payload["kind"] == "merge"
        assert {"alice", "bob"}.issubset(set(topo_events[0].payload["affected"]))


# ---------------------------------------------------------------------------
# add_creature into existing graph
# ---------------------------------------------------------------------------


class TestAddCreatureToExistingGraph:
    @pytest.mark.asyncio
    async def test_third_creature_joins_existing_pair(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        await engine.add_channel(a.graph_id, "ab")
        await engine.connect(a, b, channel="ab")

        # Add carol to the same graph after the channel exists.
        carol = await engine.add_creature(make_creature("carol"), graph=a.graph_id)
        assert carol.graph_id == a.graph_id
        assert engine.get_graph(a.graph_id).creature_ids == {
            "alice",
            "bob",
            "carol",
        }
        # Carol does NOT auto-listen to the existing channel; explicit
        # connect is required.  Confirm she has no triggers yet.
        assert "channel_carol_ab" not in (carol.agent.trigger_manager._triggers)

    @pytest.mark.asyncio
    async def test_runtime_disconnect_after_carol_joins(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        carol = await engine.add_creature(make_creature("carol"), graph=a.graph_id)
        await engine.connect(a, b, channel="ab")
        await engine.connect(b, carol, channel="bc")

        # Removing carol should not fragment the {alice,bob} component.
        await engine.remove_creature(carol)
        assert a.graph_id == b.graph_id
        # Single graph remains.
        assert len(engine.list_graphs()) == 1
