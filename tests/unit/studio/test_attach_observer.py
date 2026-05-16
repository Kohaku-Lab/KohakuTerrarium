"""Additional tests for studio.attach.observer."""

import asyncio
from datetime import datetime

import pytest

from kohakuterrarium.studio.attach import observer as obs_mod
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder
from kohakuterrarium.terrarium.service import LocalTerrariumService

# ── stream_creature_channels happy path with fast cancel ────


class TestStreamCreatureChannels:
    async def test_unknown_creature_raises(self):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        try:
            with pytest.raises(KeyError):
                async for _ in obs_mod.stream_creature_channels(svc, "ghost"):
                    break
        finally:
            await t.shutdown()


# ── _stream_from_registry pump dynamics ─────────────────────


class TestStreamFromRegistry:
    async def test_filter_channels_skips_unknown(self):
        # Build a registry with no channels matching the filter.
        from kohakuterrarium.core.channel import ChannelRegistry

        registry = ChannelRegistry()
        running = [True]

        async def collect():
            async for _ in obs_mod._stream_from_registry(
                registry,
                source_id="sid",
                source_type="session",
                filter_channels=["ghost"],
                running_check=lambda: running[0],
            ):
                pass

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        running[0] = False
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()

    async def test_non_agent_channel_skipped(self):
        from kohakuterrarium.core.channel import ChannelRegistry

        registry = ChannelRegistry()
        # Create a broadcast channel (not AgentChannel) — should be skipped.
        registry.get_or_create("chat", channel_type="broadcast")
        running = [True]

        async def collect():
            async for _ in obs_mod._stream_from_registry(
                registry,
                source_id="sid",
                source_type="session",
                running_check=lambda: running[0],
            ):
                pass

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        running[0] = False
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()

    async def test_running_check_stops_loop(self):
        from kohakuterrarium.core.channel import ChannelRegistry

        registry = ChannelRegistry()
        running = [True]

        events = []

        async def collect():
            async for ev in obs_mod._stream_from_registry(
                registry,
                source_id="sid",
                source_type="session",
                running_check=lambda: running[0],
            ):
                events.append(ev)

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        running[0] = False
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()

    async def test_real_channel_message_flows_through_as_event(self):
        # A message sent on a subscribed AgentChannel must surface as a
        # ChannelEvent through the registry pump — the on_message hook +
        # the yield path are exercised end to end.
        from kohakuterrarium.core.channel import AgentChannel, ChannelRegistry

        registry = ChannelRegistry()
        ch = AgentChannel("chat")
        registry._channels["chat"] = ch
        running = [True]
        events = []

        async def collect():
            async for ev in obs_mod._stream_from_registry(
                registry,
                source_id="sid",
                source_type="session",
                running_check=lambda: running[0],
            ):
                events.append(ev)

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)  # let the subscribe happen
        from kohakuterrarium.core.channel import ChannelMessage

        await ch.send(ChannelMessage(sender="alice", content="ping", message_id="m1"))
        await asyncio.sleep(0.05)
        running[0] = False
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()
        # The sent message arrived as a ChannelEvent with its fields intact.
        assert any(
            e.sender == "alice" and e.content == "ping" and e.channel == "chat"
            for e in events
        )


class TestStreamSessionChannels:
    async def test_unknown_session_raises(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            with pytest.raises(KeyError, match="not found"):
                async for _ in obs_mod.stream_session_channels(svc, "ghost-graph"):
                    break
        finally:
            await t.shutdown()

    async def test_real_session_channel_message_streamed(self):
        # A message broadcast on a session's shared channel must reach a
        # stream_session_channels subscriber.
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        svc = LocalTerrariumService(t)
        gid = t.get_creature("alice").graph_id
        events = []

        async def collect():
            async for ev in obs_mod.stream_session_channels(svc, gid):
                events.append(ev)

        task = asyncio.create_task(collect())
        try:
            await asyncio.sleep(0.05)
            from kohakuterrarium.core.channel import ChannelMessage

            ch = t._environments[gid].shared_channels.get("chat")
            await ch.send(ChannelMessage(sender="alice", content="hi-session"))
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await t.shutdown()
        assert any(e.content == "hi-session" for e in events)


class TestStreamCreatureChannelsHappy:
    async def test_creature_private_channel_message_streamed(self):
        # stream_creature_channels reads the creature's own session.channels
        # registry — a message there must surface as a ChannelEvent.
        from types import SimpleNamespace

        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        creature = t.get_creature("alice")
        from kohakuterrarium.core.channel import (
            AgentChannel,
            ChannelMessage,
            ChannelRegistry,
        )

        ch = AgentChannel("private")
        registry = ChannelRegistry()
        registry._channels["private"] = ch
        # The fake agent has no real ``session`` — give it one whose
        # ``channels`` registry holds the private channel.
        creature.agent.session = SimpleNamespace(channels=registry)
        events = []

        async def collect():
            async for ev in obs_mod.stream_creature_channels(svc, "alice"):
                events.append(ev)

        task = asyncio.create_task(collect())
        try:
            await asyncio.sleep(0.05)
            await ch.send(ChannelMessage(sender="alice", content="private-msg"))
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await t.shutdown()
        assert any(e.content == "private-msg" for e in events)

    async def test_channel_event_carries_fields_and_defaults_timestamp(self):
        before = datetime.now()
        ev = obs_mod.ChannelEvent(
            terrarium_id="t",
            channel="c",
            sender="s",
            content="x",
            message_id="m",
        )
        after = datetime.now()
        assert ev.terrarium_id == "t"
        assert ev.channel == "c"
        assert ev.sender == "s"
        assert ev.content == "x"
        assert ev.message_id == "m"
        # timestamp defaults to construction time.
        assert before <= ev.timestamp <= after
