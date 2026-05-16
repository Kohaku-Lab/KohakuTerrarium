"""Unit tests for :mod:`kohakuterrarium.serving.events`."""

from datetime import datetime

from kohakuterrarium.serving.events import ChannelEvent, OutputEvent


class TestChannelEvent:
    def test_required_fields(self):
        e = ChannelEvent(
            terrarium_id="t1",
            channel="c1",
            sender="s1",
            content="hello",
            message_id="m1",
        )
        assert e.terrarium_id == "t1"
        assert e.channel == "c1"
        assert e.sender == "s1"
        assert e.content == "hello"
        assert e.message_id == "m1"
        assert isinstance(e.timestamp, datetime)
        assert e.metadata == {}

    def test_metadata_independent_per_instance(self):
        a = ChannelEvent(
            terrarium_id="t",
            channel="c",
            sender="s",
            content="x",
            message_id="m",
        )
        b = ChannelEvent(
            terrarium_id="t",
            channel="c",
            sender="s",
            content="x",
            message_id="m",
        )
        a.metadata["k"] = 1
        assert b.metadata == {}


class TestOutputEvent:
    def test_required_fields(self):
        e = OutputEvent(agent_id="a1", event_type="text", content="hi")
        assert e.agent_id == "a1"
        assert e.event_type == "text"
        assert e.content == "hi"
        assert isinstance(e.timestamp, datetime)
        assert e.metadata == {}
