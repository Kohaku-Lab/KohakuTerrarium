"""Channel + wiring + subscribe tests for the engine."""

import asyncio

import pytest

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium.engine import (
    ConnectionResult,
    DisconnectionResult,
    Terrarium,
)
from kohakuterrarium.terrarium.events import EventFilter, EventKind

from tests.unit.terrarium._fakes import make_creature

# ---------------------------------------------------------------------------
# add_channel
# ---------------------------------------------------------------------------


class TestAddChannel:
    @pytest.mark.asyncio
    async def test_declares_in_graph_and_environment(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        info = await engine.add_channel(a.graph_id, "ch1")
        assert info.name == "ch1"
        # registered in topology
        assert "ch1" in engine.get_graph(a.graph_id).channels
        # registered in env's live channel registry
        env = engine._environments[a.graph_id]
        assert env.shared_channels.get("ch1") is not None

    @pytest.mark.asyncio
    async def test_duplicate_channel_rejected(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        await engine.add_channel(a.graph_id, "ch1")
        with pytest.raises(ValueError):
            await engine.add_channel(a.graph_id, "ch1")


# ---------------------------------------------------------------------------
# connect / disconnect (same-graph)
# ---------------------------------------------------------------------------


class TestConnectSameGraph:
    @pytest.mark.asyncio
    async def test_connect_injects_trigger_on_receiver(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        result = await engine.connect(a, b, channel="ab")
        assert isinstance(result, ConnectionResult)
        assert result.channel == "ab"
        assert result.delta_kind == "nothing"  # already same graph
        # bob (the receiver) got the trigger; alice did not.
        assert any(
            tid.startswith("channel_bob_ab")
            for tid in b.agent.trigger_manager._triggers
        )
        assert not any(
            tid.startswith("channel_alice_ab")
            for tid in a.agent.trigger_manager._triggers
        )

    @pytest.mark.asyncio
    async def test_connect_updates_creature_listen_send_lists(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        await engine.connect(a, b, channel="ab")
        assert "ab" in a.send_channels
        assert "ab" in b.listen_channels
        assert "ab" not in a.listen_channels
        assert "ab" not in b.send_channels

    @pytest.mark.asyncio
    async def test_auto_channel_name_when_none(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        result = await engine.connect(a, b)
        assert result.channel.startswith("alice__bob__")

    @pytest.mark.asyncio
    async def test_cross_graph_connect_merges_graphs(self):
        # Cross-graph connect merges the two graphs
        # and unions their environments.  Detailed merge invariants
        # live in test_hotplug.py; this just asserts the high-level
        # outcome from the channel surface.
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"))
        assert a.graph_id != b.graph_id
        result = await engine.connect(a, b)
        assert result.delta_kind == "merge"
        assert a.graph_id == b.graph_id


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_removes_trigger(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        await engine.connect(a, b, channel="ab")
        # baseline
        assert "channel_bob_ab" in b.agent.trigger_manager._triggers

        result = await engine.disconnect(a, b, channel="ab")
        assert isinstance(result, DisconnectionResult)
        assert result.channels == ["ab"]
        # trigger gone
        assert "channel_bob_ab" not in b.agent.trigger_manager._triggers
        # listen/send lists cleaned up
        assert "ab" not in a.send_channels
        assert "ab" not in b.listen_channels

    @pytest.mark.asyncio
    async def test_disconnect_split_graph(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        await engine.connect(a, b, channel="ab")
        # Single graph; only one channel; disconnecting splits it.
        result = await engine.disconnect(a, b, channel="ab")
        assert result.delta_kind == "split"
        # Each creature lands in its own graph and gets a fresh env.
        assert a.graph_id != b.graph_id
        assert a.graph_id in engine._environments
        assert b.graph_id in engine._environments


# ---------------------------------------------------------------------------
# wire_output / unwire_output
# ---------------------------------------------------------------------------


class _RecordingSink(OutputModule):
    def __init__(self) -> None:
        self.writes: list[str] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def write(self, text: str) -> None:
        self.writes.append(text)

    async def write_stream(self, chunk: str) -> None:
        self.writes.append(chunk)

    async def flush(self) -> None:
        pass

    async def on_processing_start(self) -> None:
        pass

    async def on_processing_end(self) -> None:
        pass

    def on_activity(self, activity_type: str, detail: str) -> None:
        pass


class TestWireOutput:
    @pytest.mark.asyncio
    async def test_wire_then_unwire_output_edge(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        edge_id = await engine.wire_output(a, "bob")
        assert edge_id.startswith("wire_bob_")
        assert [entry["to"] for entry in engine.list_output_wiring(a)] == ["bob"]
        assert a.agent.config.output_wiring[0].to == "bob"

        ok = await engine.unwire_output(a, edge_id)
        assert ok is True
        assert engine.list_output_wiring(a) == []
        assert a.agent.config.output_wiring == []

    @pytest.mark.asyncio
    async def test_output_edge_delivers_creature_output(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        await engine.wire_output(a, "bob")

        await a.agent._wiring_resolver.emit(
            source=a.creature_id,
            content="done",
            source_event_type="test",
            turn_index=1,
            entries=a.agent.config.output_wiring,
        )
        await asyncio.sleep(0)

        assert len(b.agent.received_events) == 1
        event = b.agent.received_events[0]
        assert event.type == "creature_output"
        assert event.content == "done"
        assert event.context["source"] == "alice"

    @pytest.mark.asyncio
    async def test_output_edge_resolves_target_by_creature_id(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        await engine.wire_output(a, b.creature_id)

        await a.agent._wiring_resolver.emit(
            source=a.creature_id,
            content="done",
            source_event_type="test",
            turn_index=1,
            entries=a.agent.config.output_wiring,
        )
        await asyncio.sleep(0)

        assert len(b.agent.received_events) == 1

    @pytest.mark.asyncio
    async def test_unwire_unknown_output_edge_returns_false(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        ok = await engine.unwire_output(a, "wire_missing")
        assert ok is False

    @pytest.mark.asyncio
    async def test_wire_then_unwire_output_sink(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        sink = _RecordingSink()
        sink_id = await engine.wire_output_sink(a, sink)
        assert sink in a.agent.output_router._secondary_outputs

        ok = await engine.unwire_output_sink(a, sink_id)
        assert ok is True
        assert sink not in a.agent.output_router._secondary_outputs

    @pytest.mark.asyncio
    async def test_unwire_unknown_sink_returns_false(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        ok = await engine.unwire_output_sink(a, "sink_deadbeef")
        assert ok is False


# ---------------------------------------------------------------------------
# subscribe
# ---------------------------------------------------------------------------


class TestSubscribe:
    @pytest.mark.asyncio
    async def test_creature_started_event_emitted(self):
        engine = Terrarium()
        events: list = []

        async def collect():
            async for ev in engine.subscribe():
                events.append(ev)
                if len(events) >= 1:
                    return

        # Race: spawn the consumer first, then add a creature.
        task = asyncio.create_task(collect())
        await asyncio.sleep(0)  # let the subscriber attach
        await engine.add_creature(make_creature("alice"))
        await asyncio.wait_for(task, timeout=1.0)

        assert len(events) == 1
        assert events[0].kind == EventKind.CREATURE_STARTED
        assert events[0].creature_id == "alice"

    @pytest.mark.asyncio
    async def test_filter_by_kind(self):
        engine = Terrarium()
        events: list = []

        async def collect():
            async for ev in engine.subscribe(
                EventFilter(kinds={EventKind.CREATURE_STOPPED})
            ):
                events.append(ev)
                if len(events) >= 1:
                    return

        task = asyncio.create_task(collect())
        await asyncio.sleep(0)
        await engine.add_creature(make_creature("alice"))
        # Should be filtered out — only CREATURE_STOPPED matches.
        await asyncio.sleep(0.05)
        assert len(events) == 0
        await engine.remove_creature("alice")
        await asyncio.wait_for(task, timeout=1.0)
        assert len(events) == 1
        assert events[0].kind == EventKind.CREATURE_STOPPED

    @pytest.mark.asyncio
    async def test_topology_changed_emitted_on_split(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        await engine.connect(a, b, channel="ab")

        topo_events: list = []

        async def collect():
            async for ev in engine.subscribe(
                EventFilter(kinds={EventKind.TOPOLOGY_CHANGED})
            ):
                topo_events.append(ev)
                if len(topo_events) >= 1:
                    return

        task = asyncio.create_task(collect())
        await asyncio.sleep(0)
        await engine.disconnect(a, b, channel="ab")
        await asyncio.wait_for(task, timeout=1.0)

        assert topo_events[0].kind == EventKind.TOPOLOGY_CHANGED
        assert topo_events[0].payload["kind"] == "split"
