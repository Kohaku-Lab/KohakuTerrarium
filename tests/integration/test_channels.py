"""Integration tests for channel communication."""

import asyncio

import pytest

from kohakuterrarium.core.channel import (
    AgentChannel,
    ChannelMessage,
    ChannelRegistry,
    SubAgentChannel,
)
from kohakuterrarium.core.events import EventType
from kohakuterrarium.modules.trigger.channel import ChannelTrigger


class TestSubAgentChannel:
    """Tests for queue-based channel."""

    async def test_send_receive_basic(self):
        """Basic send and receive."""
        ch = SubAgentChannel("test")
        msg = ChannelMessage(sender="agent_a", content="hello")
        await ch.send(msg)
        received = await ch.receive(timeout=1.0)
        assert received.content == "hello"
        assert received.sender == "agent_a"
        assert received.message_id  # auto-generated
        assert received.channel == "test"  # set by send()

    async def test_queue_single_consumer(self):
        """Only one consumer gets the message."""
        ch = SubAgentChannel("tasks")
        await ch.send(ChannelMessage(sender="a", content="task1"))

        received = await ch.receive(timeout=1.0)
        assert received.content == "task1"
        assert ch.empty  # message consumed

    async def test_fifo_ordering(self):
        """Messages received in FIFO order."""
        ch = SubAgentChannel("ordered")
        for i in range(5):
            await ch.send(ChannelMessage(sender="a", content=f"msg_{i}"))

        for i in range(5):
            msg = await ch.receive(timeout=1.0)
            assert msg.content == f"msg_{i}"

    async def test_timeout_on_empty(self):
        """Receive times out on empty channel."""
        ch = SubAgentChannel("empty")
        with pytest.raises(asyncio.TimeoutError):
            await ch.receive(timeout=0.1)

    async def test_try_receive_empty(self):
        """Non-blocking receive returns None on empty."""
        ch = SubAgentChannel("empty")
        assert ch.try_receive() is None

    async def test_message_id_unique(self):
        """Each message gets a unique ID."""
        msg1 = ChannelMessage(sender="a", content="1")
        msg2 = ChannelMessage(sender="a", content="2")
        assert msg1.message_id != msg2.message_id

    async def test_reply_to_preserved(self):
        """reply_to field survives send/receive."""
        ch = SubAgentChannel("thread")
        original = ChannelMessage(sender="a", content="question")
        await ch.send(original)

        reply = ChannelMessage(
            sender="b",
            content="answer",
            reply_to=original.message_id,
        )
        await ch.send(reply)

        await ch.receive(timeout=1.0)  # consume original
        received_reply = await ch.receive(timeout=1.0)
        assert received_reply.reply_to == original.message_id

    async def test_metadata_preserved(self):
        """Metadata survives send/receive."""
        ch = SubAgentChannel("meta")
        msg = ChannelMessage(
            sender="a",
            content="x",
            metadata={"priority": "high", "task_id": 42},
        )
        await ch.send(msg)
        received = await ch.receive(timeout=1.0)
        assert received.metadata["priority"] == "high"
        assert received.metadata["task_id"] == 42


class TestAgentChannel:
    """Tests for broadcast channel."""

    async def test_broadcast_all_subscribers_receive(self):
        """All non-sender subscribers get every message."""
        ch = AgentChannel("discussion")
        sub_a = ch.subscribe("agent_a")
        sub_b = ch.subscribe("agent_b")
        sub_c = ch.subscribe("agent_c")

        await ch.send(ChannelMessage(sender="agent_a", content="hello everyone"))

        # B and C should receive (not A, sender is skipped)
        msg_b = await sub_b.receive(timeout=1.0)
        msg_c = await sub_c.receive(timeout=1.0)

        assert msg_b.content == "hello everyone"
        assert msg_c.content == "hello everyone"
        assert msg_b.message_id == msg_c.message_id

        # Sender's queue should be empty
        assert sub_a.try_receive() is None

    async def test_broadcast_sender_not_echoed(self):
        """Sender does NOT receive their own message (no self-echo)."""
        ch = AgentChannel("chat")
        sub = ch.subscribe("agent_a")
        await ch.send(ChannelMessage(sender="agent_a", content="hello"))
        assert sub.try_receive() is None

    async def test_late_subscriber_misses_old_messages(self):
        """Subscriber added after send doesn't get old messages."""
        ch = AgentChannel("events")
        await ch.send(ChannelMessage(sender="a", content="old"))

        sub = ch.subscribe("late_joiner")
        assert sub.try_receive() is None  # missed it

        await ch.send(ChannelMessage(sender="a", content="new"))
        msg = await sub.receive(timeout=1.0)
        assert msg.content == "new"

    async def test_unsubscribe_stops_delivery(self):
        """After unsubscribe, no more messages delivered."""
        ch = AgentChannel("events")
        sub = ch.subscribe("agent_a")
        sub.unsubscribe()

        await ch.send(ChannelMessage(sender="b", content="after_unsub"))
        assert ch.subscriber_count == 0

    async def test_subscriber_count(self):
        """Track subscriber count."""
        ch = AgentChannel("count")
        assert ch.subscriber_count == 0

        ch.subscribe("a")
        assert ch.subscriber_count == 1

        ch.subscribe("b")
        assert ch.subscriber_count == 2

        ch.unsubscribe("a")
        assert ch.subscriber_count == 1

    async def test_resubscribe_returns_same(self):
        """Subscribing twice returns same subscription."""
        ch = AgentChannel("resub")
        sub1 = ch.subscribe("agent_a")
        sub2 = ch.subscribe("agent_a")
        assert sub1.subscriber_id == sub2.subscriber_id


class TestChannelRegistry:
    """Tests for channel registry."""

    def test_create_queue_default(self):
        """Default channel type is queue."""
        reg = ChannelRegistry()
        ch = reg.get_or_create("tasks")
        assert isinstance(ch, SubAgentChannel)
        assert ch.channel_type == "queue"

    def test_create_broadcast(self):
        """Explicit broadcast channel creation."""
        reg = ChannelRegistry()
        ch = reg.get_or_create("discussion", channel_type="broadcast")
        assert isinstance(ch, AgentChannel)
        assert ch.channel_type == "broadcast"

    def test_get_existing_ignores_type(self):
        """Getting existing channel ignores channel_type param."""
        reg = ChannelRegistry()
        ch1 = reg.get_or_create("ch", channel_type="queue")
        ch2 = reg.get_or_create("ch", channel_type="broadcast")  # ignored
        assert ch1 is ch2
        assert isinstance(ch2, SubAgentChannel)

    def test_list_and_remove(self):
        """List and remove channels."""
        reg = ChannelRegistry()
        reg.get_or_create("a")
        reg.get_or_create("b", channel_type="broadcast")
        assert sorted(reg.list_channels()) == ["a", "b"]

        reg.remove("a")
        assert reg.list_channels() == ["b"]


class TestChannelTrigger:
    """Tests for channel trigger with both channel types."""

    async def test_trigger_fires_on_queue_message(self):
        """Trigger fires when message arrives on queue channel."""
        reg = ChannelRegistry()
        ch = reg.get_or_create("inbox")

        trigger = ChannelTrigger("inbox", registry=reg)
        await trigger.start()

        # Send message in background
        async def send_delayed():
            await asyncio.sleep(0.05)
            await ch.send(ChannelMessage(sender="agent_a", content="hello"))

        asyncio.create_task(send_delayed())
        event = await trigger.wait_for_trigger()
        await trigger.stop()

        assert event is not None
        assert event.type == EventType.CHANNEL_MESSAGE
        assert event.context["sender"] == "agent_a"
        assert event.context["channel"] == "inbox"
        assert "message_id" in event.context

    async def test_trigger_fires_on_broadcast_message(self):
        """Trigger fires when message arrives on broadcast channel."""
        reg = ChannelRegistry()
        reg.get_or_create("events", channel_type="broadcast")

        trigger = ChannelTrigger("events", subscriber_id="listener_1", registry=reg)
        await trigger.start()

        async def send_delayed():
            await asyncio.sleep(0.05)
            ch = reg.get_or_create("events")
            await ch.send(ChannelMessage(sender="broadcaster", content="event!"))

        asyncio.create_task(send_delayed())
        event = await trigger.wait_for_trigger()
        await trigger.stop()

        assert event is not None
        assert event.context["sender"] == "broadcaster"

    async def test_trigger_filter_sender(self):
        """Filter only fires for matching sender."""
        reg = ChannelRegistry()
        ch = reg.get_or_create("filtered")

        trigger = ChannelTrigger("filtered", filter_sender="agent_b", registry=reg)
        await trigger.start()

        async def send_messages():
            await asyncio.sleep(0.05)
            # This one should be filtered out
            await ch.send(ChannelMessage(sender="agent_a", content="skip"))
            # This one should trigger
            await ch.send(ChannelMessage(sender="agent_b", content="match"))

        asyncio.create_task(send_messages())
        event = await trigger.wait_for_trigger()
        await trigger.stop()

        assert event is not None
        assert event.context["sender"] == "agent_b"

    async def test_trigger_prompt_template(self):
        """Prompt template gets channel metadata substitution."""
        reg = ChannelRegistry()
        ch = reg.get_or_create("templated")

        trigger = ChannelTrigger(
            "templated",
            prompt="[Channel '{channel}' from {sender}]: {content} ({priority})",
            registry=reg,
        )
        await trigger.start()

        async def send_delayed():
            await asyncio.sleep(0.05)
            await ch.send(
                ChannelMessage(
                    sender="a",
                    content="fix the bug",
                    metadata={"priority": "high"},
                )
            )

        asyncio.create_task(send_delayed())
        event = await trigger.wait_for_trigger()
        await trigger.stop()

        assert (
            "[Channel 'templated' from a]: fix the bug (high)"
            in event.get_text_content()
        )

    async def test_trigger_cleanup_on_stop(self):
        """Broadcast subscription cleaned up on stop."""
        reg = ChannelRegistry()
        ch = reg.get_or_create("cleanup", channel_type="broadcast")

        trigger = ChannelTrigger("cleanup", subscriber_id="test_sub", registry=reg)
        await trigger.start()

        # Force the trigger to create subscription by briefly attempting wait
        wait_task = asyncio.create_task(trigger.wait_for_trigger())
        await asyncio.sleep(0.05)  # Let it subscribe

        await trigger.stop()
        wait_task.cancel()
        try:
            await wait_task
        except asyncio.CancelledError:
            pass

        # Subscription should be cleaned up
        assert ch.subscriber_count == 0
