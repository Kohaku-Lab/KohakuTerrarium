"""Unit tests for :mod:`kohakuterrarium.core.channel`."""

import asyncio
from datetime import datetime

import pytest

from kohakuterrarium.core.channel import (
    AgentChannel,
    Channel,
    ChannelMessage,
    ChannelRegistry,
    ChannelSubscription,
    SubAgentChannel,
    generate_message_id,
)

# ── ChannelMessage / IDs ──────────────────────────────────────────


class TestChannelMessage:
    def test_defaults(self):
        m = ChannelMessage(sender="alice", content="hi")
        assert m.sender == "alice"
        assert m.content == "hi"
        assert m.metadata == {}
        assert isinstance(m.timestamp, datetime)
        assert m.message_id.startswith("msg_")
        assert m.reply_to is None
        assert m.channel is None
        assert m.sender_id is None

    def test_unique_ids(self):
        a = generate_message_id()
        b = generate_message_id()
        assert a != b
        assert a.startswith("msg_")


# ── SubAgentChannel ───────────────────────────────────────────────


class TestSubAgentChannel:
    async def test_send_receive_roundtrip(self):
        ch = SubAgentChannel("worker")
        await ch.send(ChannelMessage(sender="a", content="hi"))
        msg = await ch.receive()
        assert msg.content == "hi"
        assert msg.channel == "worker"

    async def test_channel_type(self):
        assert SubAgentChannel("x").channel_type == "queue"

    async def test_history_recorded(self):
        ch = SubAgentChannel("x")
        for i in range(3):
            await ch.send(ChannelMessage(sender="a", content=f"m{i}"))
        # Drain queue.
        for _ in range(3):
            await ch.receive()
        assert [m.content for m in ch.history] == ["m0", "m1", "m2"]

    async def test_history_capped(self):
        ch = SubAgentChannel("x")
        ch._max_history = 3
        for i in range(10):
            await ch.send(ChannelMessage(sender="a", content=str(i)))
        assert [m.content for m in ch.history] == ["7", "8", "9"]

    async def test_empty_and_qsize(self):
        ch = SubAgentChannel("x")
        assert ch.empty is True
        assert ch.qsize == 0
        await ch.send(ChannelMessage(sender="s", content="m"))
        assert ch.empty is False
        assert ch.qsize == 1

    async def test_receive_timeout(self):
        ch = SubAgentChannel("x")
        with pytest.raises(asyncio.TimeoutError):
            await ch.receive(timeout=0.01)

    async def test_try_receive_empty(self):
        ch = SubAgentChannel("x")
        assert ch.try_receive() is None

    async def test_try_receive_with_message(self):
        ch = SubAgentChannel("x")
        await ch.send(ChannelMessage(sender="a", content="hi"))
        msg = ch.try_receive()
        assert msg is not None
        assert msg.content == "hi"

    async def test_on_send_callback(self):
        ch = SubAgentChannel("x")
        seen: list[ChannelMessage] = []

        def cb(name, message):
            seen.append((name, message))

        ch.on_send(cb)
        await ch.send(ChannelMessage(sender="a", content="hi"))
        assert seen[0][0] == "x"
        assert seen[0][1].content == "hi"

    async def test_remove_on_send(self):
        ch = SubAgentChannel("x")
        seen = []

        def cb(name, msg):
            seen.append(1)

        ch.on_send(cb)
        ch.remove_on_send(cb)
        await ch.send(ChannelMessage(sender="a", content="hi"))
        assert seen == []

    async def test_callback_exception_is_swallowed(self):
        ch = SubAgentChannel("x")

        def boom(name, message):
            raise RuntimeError("nope")

        ch.on_send(boom)
        # Send must NOT raise.
        await ch.send(ChannelMessage(sender="a", content="hi"))
        assert ch.qsize == 1

    async def test_bounded_queue_backpressure(self):
        ch = SubAgentChannel("x", maxsize=1)
        await ch.send(ChannelMessage(sender="a", content="first"))
        # Consumer task drains after delay to unblock the second send.

        async def consumer():
            await asyncio.sleep(0.02)
            await ch.receive()

        consumer_task = asyncio.create_task(consumer())
        # Second send must wait until consumer drains the slot.
        await ch.send(ChannelMessage(sender="a", content="second"))
        await consumer_task
        # Final message still queued.
        msg = await ch.receive()
        assert msg.content == "second"


# ── AgentChannel (broadcast) ──────────────────────────────────────


class TestAgentChannel:
    async def test_channel_type(self):
        assert AgentChannel("x").channel_type == "broadcast"

    async def test_subscribe_returns_subscription(self):
        ch = AgentChannel("x")
        sub = ch.subscribe("alice")
        assert isinstance(sub, ChannelSubscription)
        assert sub.subscriber_id == "alice"
        assert ch.subscriber_count == 1

    async def test_subscribe_twice_same_id_reuses(self):
        ch = AgentChannel("x")
        a = ch.subscribe("alice")
        b = ch.subscribe("alice")
        # Same queue → both subscriptions see the same messages.
        assert ch.subscriber_count == 1
        assert a._queue is b._queue

    async def test_broadcast_delivers_to_all(self):
        ch = AgentChannel("x")
        a = ch.subscribe("alice")
        b = ch.subscribe("bob")
        await ch.send(ChannelMessage(sender="ext", content="hi"))
        msg_a = await a.receive()
        msg_b = await b.receive()
        assert msg_a.content == msg_b.content == "hi"

    async def test_sender_does_not_echo(self):
        ch = AgentChannel("x")
        alice = ch.subscribe("alice")
        bob = ch.subscribe("bob")
        await ch.send(ChannelMessage(sender="alice", content="self"))
        # Bob receives, Alice does not.
        assert (await bob.receive()).content == "self"
        assert alice.empty is True

    async def test_unsubscribe(self):
        ch = AgentChannel("x")
        ch.subscribe("alice")
        ch.unsubscribe("alice")
        assert ch.subscriber_count == 0
        # Subsequent broadcast goes nowhere.
        await ch.send(ChannelMessage(sender="bob", content="x"))
        assert ch.qsize == 0

    async def test_unsubscribe_unknown_silent(self):
        ch = AgentChannel("x")
        ch.unsubscribe("nope")  # must not raise
        assert ch.subscriber_count == 0

    async def test_subscription_unsubscribe_helper(self):
        ch = AgentChannel("x")
        sub = ch.subscribe("alice")
        sub.unsubscribe()
        assert ch.subscriber_count == 0

    async def test_subscription_try_receive(self):
        ch = AgentChannel("x")
        sub = ch.subscribe("alice")
        assert sub.try_receive() is None
        await ch.send(ChannelMessage(sender="ext", content="hi"))
        msg = sub.try_receive()
        assert msg is not None and msg.content == "hi"

    async def test_subscription_receive_timeout(self):
        ch = AgentChannel("x")
        sub = ch.subscribe("alice")
        with pytest.raises(asyncio.TimeoutError):
            await sub.receive(timeout=0.01)

    async def test_subscription_empty_qsize(self):
        ch = AgentChannel("x")
        sub = ch.subscribe("alice")
        assert sub.empty is True
        assert sub.qsize == 0
        await ch.send(ChannelMessage(sender="ext", content="m"))
        assert sub.empty is False
        assert sub.qsize == 1

    async def test_broadcast_empty_and_qsize(self):
        ch = AgentChannel("x")
        a = ch.subscribe("alice")
        b = ch.subscribe("bob")
        await ch.send(ChannelMessage(sender="ext", content="m1"))
        await ch.send(ChannelMessage(sender="ext", content="m2"))
        # 2 messages × 2 subscribers = 4 queued.
        assert ch.qsize == 4
        assert ch.empty is False
        await a.receive()
        await a.receive()
        await b.receive()
        await b.receive()
        assert ch.empty is True

    async def test_broadcast_history_capped(self):
        ch = AgentChannel("x")
        ch._max_history = 2
        for i in range(5):
            await ch.send(ChannelMessage(sender="s", content=str(i)))
        assert [m.content for m in ch.history] == ["3", "4"]


# ── ChannelRegistry ───────────────────────────────────────────────


class TestChannelRegistry:
    def test_get_or_create_queue_default(self):
        reg = ChannelRegistry()
        ch = reg.get_or_create("a")
        assert isinstance(ch, SubAgentChannel)

    def test_get_or_create_broadcast(self):
        reg = ChannelRegistry()
        ch = reg.get_or_create("a", channel_type="broadcast")
        assert isinstance(ch, AgentChannel)

    def test_get_or_create_idempotent(self):
        reg = ChannelRegistry()
        a = reg.get_or_create("x", channel_type="queue")
        b = reg.get_or_create("x", channel_type="broadcast")  # ignored
        # Same object — type arg ignored when channel exists.
        assert a is b

    def test_get_by_name(self):
        reg = ChannelRegistry()
        ch = reg.get_or_create("a")
        assert reg.get("a") is ch
        assert reg.get("missing") is None

    def test_list_channels(self):
        reg = ChannelRegistry()
        reg.get_or_create("a")
        reg.get_or_create("b", channel_type="broadcast")
        assert set(reg.list_channels()) == {"a", "b"}

    def test_remove(self):
        reg = ChannelRegistry()
        reg.get_or_create("a")
        assert reg.remove("a") is True
        assert reg.get("a") is None
        # Idempotent.
        assert reg.remove("a") is False

    def test_get_channel_info(self):
        reg = ChannelRegistry()
        reg.get_or_create("a", description="alpha channel")
        reg.get_or_create("b", channel_type="broadcast", description="beta")
        info = reg.get_channel_info()
        names = {i["name"]: i for i in info}
        assert names["a"]["type"] == "queue"
        assert names["a"]["description"] == "alpha channel"
        assert names["b"]["type"] == "broadcast"


# ── backwards-compat alias ────────────────────────────────────────


class TestChannelAlias:
    def test_channel_alias_is_subagent(self):
        assert Channel is SubAgentChannel
