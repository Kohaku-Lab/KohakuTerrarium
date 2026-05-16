"""Unit tests for :mod:`kohakuterrarium.terrarium.observer`."""

import asyncio
from datetime import datetime


from kohakuterrarium.core.channel import (
    ChannelMessage,
)
from kohakuterrarium.core.session import Session
from kohakuterrarium.terrarium.observer import (
    ChannelObserver,
    ObservedMessage,
    _to_observed,
)

# ── ObservedMessage / _to_observed ─────────────────────────────


def _make_msg(content="hi", sender="alice", channel="ch") -> ChannelMessage:
    return ChannelMessage(
        sender=sender,
        content=content,
        message_id="m1",
        timestamp=datetime.now(),
        channel=channel,
        metadata={"k": "v"},
    )


class TestToObserved:
    def test_basic(self):
        msg = _make_msg()
        out = _to_observed("ch", msg)
        assert out.channel == "ch"
        assert out.sender == "alice"
        assert out.content == "hi"
        assert out.metadata == {"k": "v"}

    def test_non_str_content_stringified(self):
        msg = _make_msg(content=[1, 2, 3])  # type: ignore
        out = _to_observed("ch", msg)
        assert "1" in out.content


class TestObservedMessage:
    def test_default_metadata(self):
        m = ObservedMessage(
            channel="ch",
            sender="alice",
            content="hi",
            message_id="m1",
            timestamp=datetime.now(),
        )
        assert m.metadata == {}


# ── ChannelObserver ────────────────────────────────────────────


def _session() -> Session:
    return Session(key="test-session")


class TestObserverConstruction:
    def test_init_defaults(self):
        s = _session()
        obs = ChannelObserver(s)
        assert obs._max_history == 1000
        assert obs._callbacks == []
        assert obs._messages == []


class TestObservePublicAPI:
    async def test_observe_unknown_channel(self):
        s = _session()
        obs = ChannelObserver(s)
        await obs.observe("nope")
        # No subscription registered.
        assert "nope" not in obs._subscriptions

    async def test_observe_idempotent(self):
        s = _session()
        s.channels.get_or_create("ch", channel_type="broadcast")
        obs = ChannelObserver(s)
        await obs.observe("ch")
        await obs.observe("ch")  # second call is no-op
        # Only one subscription tracked.
        assert len(obs._subscriptions) == 1
        await obs.stop()

    async def test_observe_subagent_skipped(self):
        s = _session()
        s.channels.get_or_create("sa", channel_type="queue")
        obs = ChannelObserver(s)
        await obs.observe("sa")
        # Queue channels are not subscribed (they're not AgentChannel).
        assert "sa" not in obs._subscriptions

    async def test_record_appends_message(self):
        s = _session()
        obs = ChannelObserver(s)
        msg = _make_msg()
        obs.record("ch", msg)
        msgs = obs.get_messages()
        assert len(msgs) == 1
        assert msgs[0].channel == "ch"

    async def test_on_message_callback(self):
        s = _session()
        obs = ChannelObserver(s)
        received: list[ObservedMessage] = []
        obs.on_message(received.append)
        obs.record("ch", _make_msg())
        assert len(received) == 1

    async def test_callback_error_swallowed(self):
        s = _session()
        obs = ChannelObserver(s)

        def boom(_):
            raise RuntimeError("cb boom")

        obs.on_message(boom)
        # Doesn't raise.
        obs.record("ch", _make_msg())

    async def test_get_messages_filter_by_channel(self):
        s = _session()
        obs = ChannelObserver(s)
        obs.record("ch1", _make_msg(channel="ch1"))
        obs.record("ch2", _make_msg(channel="ch2"))
        out = obs.get_messages(channel="ch1")
        assert len(out) == 1
        assert out[0].channel == "ch1"

    async def test_get_messages_last_n(self):
        s = _session()
        obs = ChannelObserver(s)
        for _ in range(5):
            obs.record("ch", _make_msg())
        out = obs.get_messages(last_n=2)
        assert len(out) == 2

    async def test_max_history_trims(self):
        s = _session()
        obs = ChannelObserver(s, max_history=3)
        for _ in range(5):
            obs.record("ch", _make_msg())
        assert len(obs._messages) == 3


class TestObserveBroadcast:
    async def test_receives_messages_from_subscribed_channel(self):
        s = _session()
        ch = s.channels.get_or_create("ch", channel_type="broadcast")
        obs = ChannelObserver(s)
        await obs.observe("ch")
        try:
            await ch.send(_make_msg(channel="ch", content="broadcast!"))
            # The background pump must surface the broadcast to the
            # observer — observing a channel means capturing its sends.
            for _ in range(200):
                await asyncio.sleep(0.01)
                if obs.get_messages():
                    break
            msgs = obs.get_messages()
            assert len(msgs) == 1
            assert msgs[0].channel == "ch"
            assert msgs[0].content == "broadcast!"
        finally:
            await obs.stop()


class TestObserverStop:
    async def test_stop_cleans_up(self):
        s = _session()
        s.channels.get_or_create("ch", channel_type="broadcast")
        obs = ChannelObserver(s)
        await obs.observe("ch")
        await obs.stop()
        assert obs._subscriptions == {}
        assert obs._observe_tasks == []

    async def test_stop_idempotent_no_subscriptions(self):
        s = _session()
        obs = ChannelObserver(s)
        await obs.stop()  # no-op
