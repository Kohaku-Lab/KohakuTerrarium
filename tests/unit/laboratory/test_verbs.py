"""Unit tests for :mod:`kohakuterrarium.laboratory.verbs`."""

import asyncio

import pytest

from kohakuterrarium.laboratory._internal.envelope import Envelope, EnvelopeKind
from kohakuterrarium.laboratory._internal.streams import build_ack_envelope
from kohakuterrarium.laboratory.verbs import AckTimeoutError, Channel, Topic


class _FakeNode:
    """Minimal LabNode for testing."""

    def __init__(self, client_id="node-A"):
        self.client_id = client_id
        self.sent: list[Envelope] = []
        self._handlers = []

    async def send(self, env):
        self.sent.append(env)

    def on_envelope(self, handler):
        self._handlers.append(handler)

    async def deliver(self, env):
        """Helper to simulate the node receiving an envelope."""
        for h in self._handlers:
            await h(env)


# ── _BaseEndpoint shared behaviour ───────────────────────────────


class TestBaseEndpoint:
    async def test_name_property_exposes_endpoint_name(self):
        # Both Channel and Topic carry the name they were constructed
        # with; it's the routing key the host directory matches on.
        node = _FakeNode()
        ch = Channel("orders", node)
        topic = Topic("events", node)
        assert ch.name == "orders"
        assert topic.name == "events"

    async def test_base_handle_is_abstract(self):
        # ``_BaseEndpoint._handle`` is a contract stub: every concrete
        # endpoint MUST override it. Reaching the base implementation
        # (e.g. a subclass that forgot to) raises NotImplementedError
        # rather than silently dropping the envelope.
        from kohakuterrarium.laboratory.verbs import _BaseEndpoint

        node = _FakeNode()
        endpoint = _BaseEndpoint("x", node)
        env = Envelope(
            from_node="peer",
            to_node="node-A",
            kind=EnvelopeKind.SEND,
            stream_id=0,
            seq=0,
        )
        with pytest.raises(NotImplementedError):
            await endpoint._handle(env)


# ── Channel ──────────────────────────────────────────────────────


class TestChannel:
    async def test_subscribe_sends_control(self):
        node = _FakeNode()
        ch = Channel("team", node)
        await ch.subscribe()
        assert len(node.sent) == 1
        assert node.sent[0].kind == EnvelopeKind.CONTROL

    async def test_subscribe_idempotent(self):
        node = _FakeNode()
        ch = Channel("team", node)
        await ch.subscribe()
        await ch.subscribe()
        # Only one control envelope.
        assert len(node.sent) == 1

    async def test_unsubscribe(self):
        node = _FakeNode()
        ch = Channel("team", node)
        await ch.subscribe()
        await ch.unsubscribe()
        # subscribe + unsubscribe = 2 envelopes.
        assert len(node.sent) == 2

    async def test_unsubscribe_idempotent(self):
        node = _FakeNode()
        ch = Channel("team", node)
        await ch.unsubscribe()  # not subscribed → no-op
        assert node.sent == []

    async def test_send_fire_and_forget(self):
        node = _FakeNode()
        ch = Channel("team", node)
        await ch.send(b"hello")
        assert len(node.sent) == 1
        sent = node.sent[0]
        assert sent.kind == EnvelopeKind.SEND
        assert sent.payload == b"hello"
        assert sent.to_node == "channel://team"

    async def test_send_with_ack_times_out(self):
        node = _FakeNode()
        ch = Channel("team", node)
        with pytest.raises(AckTimeoutError):
            await ch.send(b"hello", ack=True, timeout=0.05)

    async def test_send_with_ack_succeeds(self):
        node = _FakeNode()
        ch = Channel("team", node)

        async def auto_ack():
            # Wait for the send envelope to be queued.
            while not node.sent:
                await asyncio.sleep(0.001)
            env = node.sent[-1]
            ack = build_ack_envelope(
                from_node="recv",
                to_node=env.from_node,
                stream_id=env.stream_id,
                seq=env.seq,
            )
            await node.deliver(ack)

        # Run auto_ack concurrently with send-with-ack.
        await asyncio.gather(
            ch.send(b"hi", ack=True, timeout=1.0),
            auto_ack(),
        )

    async def test_receive_send_to_inbox(self):
        node = _FakeNode()
        ch = Channel("team", node)
        # Simulate inbound SEND envelope addressed to this channel.
        env = Envelope(
            from_node="peer",
            to_node="channel://team",
            kind=EnvelopeKind.SEND,
            stream_id=99,
            seq=0,
            payload=b"inbound",
        )
        await node.deliver(env)
        out = await ch.recv()
        assert out == b"inbound"

    async def test_receive_acks_back(self):
        node = _FakeNode()
        # Constructing the Channel registers its envelope handler on the
        # node — that side effect is what this test exercises.
        Channel("team", node)
        env = Envelope(
            from_node="peer",
            to_node="channel://team",
            kind=EnvelopeKind.SEND,
            stream_id=99,
            seq=5,
            payload=b"x",
            flags={"ack_required": True},
        )
        await node.deliver(env)
        # An ACK envelope was sent back.
        acks = [e for e in node.sent if e.kind == EnvelopeKind.ACK]
        assert len(acks) == 1
        assert acks[0].seq == 5

    async def test_unrelated_envelope_ignored(self):
        node = _FakeNode()
        ch = Channel("team", node)
        env = Envelope(
            from_node="peer",
            to_node="channel://other",
            kind=EnvelopeKind.SEND,
            stream_id=99,
            seq=0,
            payload=b"not for us",
        )
        await node.deliver(env)
        # Nothing landed in inbox.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ch.recv(), timeout=0.05)


# ── Topic ────────────────────────────────────────────────────────


class TestTopic:
    async def test_publish(self):
        node = _FakeNode()
        t = Topic("announce", node)
        await t.publish(b"news")
        assert len(node.sent) == 1
        env = node.sent[0]
        assert env.kind == EnvelopeKind.BROADCAST
        assert env.to_node == "announce"

    async def test_receive_broadcast(self):
        node = _FakeNode()
        t = Topic("announce", node)
        env = Envelope(
            from_node="peer",
            to_node="announce",
            kind=EnvelopeKind.BROADCAST,
            stream_id=1,
            seq=0,
            payload=b"hi",
        )
        await node.deliver(env)
        out = await t.recv()
        assert out == b"hi"

    async def test_ignores_unrelated_broadcast(self):
        node = _FakeNode()
        t = Topic("announce", node)
        env = Envelope(
            from_node="peer",
            to_node="other-topic",
            kind=EnvelopeKind.BROADCAST,
            stream_id=1,
            seq=0,
            payload=b"x",
        )
        await node.deliver(env)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(t.recv(), timeout=0.05)

    async def test_messages_iterator(self):
        node = _FakeNode()
        t = Topic("announce", node)
        msgs = []

        async def consume():
            async for m in t.messages():
                msgs.append(m)
                if len(msgs) >= 2:
                    return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)
        # Deliver two broadcasts.
        for i in (1, 2):
            await node.deliver(
                Envelope(
                    from_node="p",
                    to_node="announce",
                    kind=EnvelopeKind.BROADCAST,
                    stream_id=1,
                    seq=i,
                    payload=str(i).encode(),
                )
            )
        await asyncio.wait_for(consumer, timeout=1.0)
        assert msgs == [b"1", b"2"]
