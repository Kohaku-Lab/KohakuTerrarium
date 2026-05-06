"""Tests for ``Terrarium.assign_root`` — the channel/wiring helper that
mirrors the legacy "root agent" pattern at the engine layer.
"""

import pytest

from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import RootAssignment

from tests.unit.terrarium._fakes import make_creature


class TestAssignRootBasics:
    @pytest.mark.asyncio
    async def test_marks_creature_as_root(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        result = await engine.assign_root(a)
        assert isinstance(result, RootAssignment)
        assert result.root_id == "alice"
        assert a.is_privileged is True

    @pytest.mark.asyncio
    async def test_creates_report_channel(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        result = await engine.assign_root(a)
        graph = engine.get_graph(a.graph_id)
        assert "report_to_root" in graph.channels
        assert "report_to_root" in result.channels_created

    @pytest.mark.asyncio
    async def test_root_listens_on_report_channel(self):
        engine = Terrarium()
        a = await engine.add_creature(make_creature("alice"))
        await engine.assign_root(a)
        assert "report_to_root" in a.listen_channels
        # trigger injected on the root agent
        assert "channel_alice_report_to_root" in (a.agent.trigger_manager._triggers)


class TestAssignRootWithPeers:
    @pytest.mark.asyncio
    async def test_other_creatures_can_send_to_root(self):
        engine = Terrarium()
        root = await engine.add_creature(make_creature("root"))
        worker = await engine.add_creature(make_creature("worker"), graph=root.graph_id)
        scout = await engine.add_creature(make_creature("scout"), graph=root.graph_id)
        result = await engine.assign_root(root)
        assert "report_to_root" in worker.send_channels
        assert "report_to_root" in scout.send_channels
        assert sorted(result.senders_added) == ["scout", "worker"]
        # The root itself is NOT a sender on report_to_root.
        assert "report_to_root" not in root.send_channels

    @pytest.mark.asyncio
    async def test_root_listens_on_existing_channels(self):
        engine = Terrarium()
        root = await engine.add_creature(make_creature("root"))
        await engine.add_creature(make_creature("worker"), graph=root.graph_id)
        await engine.add_channel(root.graph_id, "tasks")
        await engine.add_channel(root.graph_id, "results")
        await engine.assign_root(root)
        # Root now hears every pre-existing channel + report_to_root.
        assert {"tasks", "results", "report_to_root"}.issubset(
            set(root.listen_channels)
        )
        # And triggers were injected for each.
        for ch in ("tasks", "results", "report_to_root"):
            assert f"channel_root_{ch}" in root.agent.trigger_manager._triggers

    @pytest.mark.asyncio
    async def test_root_assignment_is_idempotent(self):
        engine = Terrarium()
        root = await engine.add_creature(make_creature("root"))
        worker = await engine.add_creature(make_creature("worker"), graph=root.graph_id)
        first = await engine.assign_root(root)
        assert first.channels_created == ["report_to_root"]

        second = await engine.assign_root(root)
        # Channel already existed; second call doesn't recreate it.
        assert second.channels_created == []
        # But the contract still holds.
        assert "report_to_root" in worker.send_channels
        assert "report_to_root" in root.listen_channels


class TestAssignRootCustomName:
    @pytest.mark.asyncio
    async def test_custom_report_channel(self):
        engine = Terrarium()
        root = await engine.add_creature(make_creature("captain"))
        crew = await engine.add_creature(make_creature("crew"), graph=root.graph_id)
        result = await engine.assign_root(root, report_channel="bridge")
        assert "bridge" in engine.get_graph(root.graph_id).channels
        assert "bridge" in root.listen_channels
        assert "bridge" in crew.send_channels
        assert result.report_channel == "bridge"
