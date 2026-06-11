"""Unit tests for :mod:`kohakuterrarium.terrarium.events`."""

from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
    EngineEvent,
    EventFilter,
    EventKind,
    RootAssignment,
)

# ── EventKind ─────────────────────────────────────────────────────


class TestEventKind:
    def test_string_values(self):
        assert EventKind.CHANNEL_MESSAGE.value == "channel_message"
        assert EventKind.TOPOLOGY_CHANGED.value == "topology_changed"

    def test_str_enum(self):
        # EventKind is a str-Enum so comparison with str works.
        assert EventKind.CHANNEL_MESSAGE == "channel_message"


# ── EngineEvent ───────────────────────────────────────────────────


class TestEngineEvent:
    def test_defaults(self):
        e = EngineEvent(kind=EventKind.CHANNEL_MESSAGE)
        assert e.creature_id is None
        assert e.payload == {}
        assert isinstance(e.ts, float)

    def test_explicit_fields(self):
        e = EngineEvent(
            kind=EventKind.CREATURE_STOPPED,
            creature_id="c1",
            graph_id="g",
            channel="ch",
            payload={"e": "boom"},
            ts=10.0,
        )
        assert e.creature_id == "c1"
        assert e.payload["e"] == "boom"


# ── EventFilter ───────────────────────────────────────────────────


class TestEventFilterMatches:
    def test_empty_matches_all(self):
        f = EventFilter()
        assert f.matches(EngineEvent(kind=EventKind.CHANNEL_MESSAGE))
        assert f.matches(
            EngineEvent(
                kind=EventKind.CREATURE_STOPPED,
                creature_id="c",
                graph_id="g",
                channel="ch",
            )
        )

    def test_kind_filter(self):
        f = EventFilter(kinds={EventKind.CHANNEL_MESSAGE})
        assert f.matches(EngineEvent(kind=EventKind.CHANNEL_MESSAGE))
        assert not f.matches(EngineEvent(kind=EventKind.CREATURE_STOPPED))

    def test_creature_filter(self):
        f = EventFilter(creature_ids={"c1"})
        assert f.matches(EngineEvent(kind=EventKind.CHANNEL_MESSAGE, creature_id="c1"))
        assert not f.matches(
            EngineEvent(kind=EventKind.CHANNEL_MESSAGE, creature_id="c2")
        )

    def test_graph_filter(self):
        f = EventFilter(graph_ids={"g1"})
        assert f.matches(EngineEvent(kind=EventKind.CHANNEL_MESSAGE, graph_id="g1"))
        assert not f.matches(EngineEvent(kind=EventKind.CHANNEL_MESSAGE, graph_id="g2"))

    def test_channel_filter(self):
        f = EventFilter(channels={"ch1"})
        assert f.matches(EngineEvent(kind=EventKind.CHANNEL_MESSAGE, channel="ch1"))
        assert not f.matches(EngineEvent(kind=EventKind.CHANNEL_MESSAGE, channel="ch2"))

    def test_all_filters_and_combined(self):
        f = EventFilter(
            kinds={EventKind.CHANNEL_MESSAGE},
            creature_ids={"c"},
            graph_ids={"g"},
            channels={"ch"},
        )
        good = EngineEvent(
            kind=EventKind.CHANNEL_MESSAGE, creature_id="c", graph_id="g", channel="ch"
        )
        bad = EngineEvent(
            kind=EventKind.CHANNEL_MESSAGE,
            creature_id="c",
            graph_id="g",
            channel="other",
        )
        assert f.matches(good)
        assert not f.matches(bad)


# ── Result dataclasses ────────────────────────────────────────────


class TestConnectionResult:
    def test_defaults(self):
        r = ConnectionResult(channel="ch")
        assert r.trigger_id == ""
        assert r.delta_kind == "nothing"
        assert r.graph_id == ""


class TestDisconnectionResult:
    def test_defaults(self):
        r = DisconnectionResult()
        assert r.channels == []
        assert r.delta_kind == "nothing"


# ── RootAssignment ────────────────────────────────────────────────


class TestRootAssignment:
    def test_basic_fields(self):
        r = RootAssignment(
            graph_id="g",
            root_id="root",
            channels_created=["ch1"],
            channels_listened=["ch1"],
            senders_added=["c1"],
        )
        assert r.graph_id == "g"
        assert r.root_id == "root"
        assert r.channels_created == ["ch1"]

    def test_back_compat_creature_id_alias(self):
        # Legacy callers use ``creature_id=`` instead of ``root_id=``.
        r = RootAssignment(graph_id="g", creature_id="legacy-root")
        assert r.root_id == "legacy-root"

    def test_back_compat_channels_alias(self):
        # Legacy callers use ``channels=`` instead of ``channels_listened=``.
        r = RootAssignment(graph_id="g", channels=["ch1"])
        assert r.channels_listened == ["ch1"]

    def test_root_id_wins_over_creature_id(self):
        r = RootAssignment(graph_id="g", root_id="winner", creature_id="loser")
        assert r.root_id == "winner"

    def test_default_report_channel(self):
        r = RootAssignment(graph_id="g")
        assert r.report_channel == "report_to_root"
