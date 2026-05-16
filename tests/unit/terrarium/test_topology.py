"""Unit tests for :mod:`kohakuterrarium.terrarium.topology`."""

import pytest

from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    GraphTopology,
    TopologyDelta,
    TopologyState,
    _channels_used_by,
    _merge_graphs,
    add_channel,
    add_creature,
    connect,
    disconnect,
    find_components,
    new_graph_id,
    remove_channel,
    remove_creature,
    set_listen,
    set_send,
)

# ── dataclasses ──────────────────────────────────────────────────


class TestChannelInfo:
    def test_default_description(self):
        c = ChannelInfo(name="x")
        assert c.name == "x"
        assert c.description == ""

    def test_frozen(self):
        c = ChannelInfo(name="x")
        with pytest.raises(Exception):
            c.name = "y"  # type: ignore


class TestGraphTopology:
    def test_has_creature(self):
        g = GraphTopology(graph_id="g1")
        assert not g.has_creature("c1")
        g.creature_ids.add("c1")
        assert g.has_creature("c1")

    def test_has_channel(self):
        g = GraphTopology(graph_id="g1")
        assert not g.has_channel("x")
        g.channels["x"] = ChannelInfo(name="x")
        assert g.has_channel("x")


class TestTopologyState:
    def test_empty_counts(self):
        s = TopologyState()
        assert s.creature_count() == 0
        assert s.graph_count() == 0

    def test_graph_of_unknown_raises(self):
        s = TopologyState()
        with pytest.raises(KeyError):
            s.graph_of("nope")


# ── new_graph_id ─────────────────────────────────────────────────


class TestNewGraphId:
    def test_unique(self):
        a = new_graph_id()
        b = new_graph_id()
        assert a != b
        assert a.startswith("graph_")


# ── add_creature ─────────────────────────────────────────────────


class TestAddCreature:
    def test_creates_singleton_graph(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        assert s.creature_count() == 1
        assert s.graph_count() == 1
        assert s.creature_to_graph["c1"] == gid

    def test_join_existing_graph(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_creature(s, "c2", graph_id=gid)
        assert s.creature_count() == 2
        # Still one graph.
        assert s.graph_count() == 1

    def test_duplicate_raises(self):
        s = TopologyState()
        add_creature(s, "c1")
        with pytest.raises(ValueError, match="already exists"):
            add_creature(s, "c1")

    def test_unknown_graph_raises(self):
        s = TopologyState()
        with pytest.raises(KeyError):
            add_creature(s, "c1", graph_id="ghost")


# ── remove_creature ──────────────────────────────────────────────


class TestRemoveCreature:
    def test_remove_only_creature_drops_graph(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        delta = remove_creature(s, "c1")
        assert delta.kind == "nothing"
        assert s.graph_count() == 0
        assert gid not in s.graphs

    def test_remove_one_of_many(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_creature(s, "c2", graph_id=gid)
        # Connect them so they really are one component.
        connect(s, "c1", "c2", channel="ch")
        delta = remove_creature(s, "c2")
        assert delta.kind == "nothing"
        assert s.creature_count() == 1


# ── add_channel / remove_channel ─────────────────────────────────


class TestAddRemoveChannel:
    def test_add_channel(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        info = add_channel(s, gid, "ch")
        assert info.name == "ch"
        assert s.graphs[gid].has_channel("ch")

    def test_add_duplicate_channel_raises(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_channel(s, gid, "ch")
        with pytest.raises(ValueError, match="already declared"):
            add_channel(s, gid, "ch")

    def test_add_channel_unknown_graph(self):
        s = TopologyState()
        with pytest.raises(KeyError):
            add_channel(s, "ghost", "ch")

    def test_remove_channel(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_channel(s, gid, "ch")
        delta = remove_channel(s, gid, "ch")
        assert delta.kind == "nothing"
        assert not s.graphs[gid].has_channel("ch")

    def test_remove_unknown_channel_raises(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        with pytest.raises(KeyError):
            remove_channel(s, gid, "nope")

    def test_remove_channel_unknown_graph(self):
        s = TopologyState()
        with pytest.raises(KeyError):
            remove_channel(s, "ghost", "ch")

    def test_remove_channel_drops_edges(self):
        # Use a third anchoring creature so the graph doesn't split when
        # the channel is removed — the anchor keeps c1 and c2 in the
        # same component via a separate channel.
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_creature(s, "c2", graph_id=gid)
        add_creature(s, "anchor", graph_id=gid)
        add_channel(s, gid, "ch")
        add_channel(s, gid, "anchor_ch")
        set_send(s, "c1", "ch", sending=True)
        set_listen(s, "c2", "ch", listening=True)
        # Anchor links c1 and c2 even without ``ch``.
        set_send(s, "c1", "anchor_ch", sending=True)
        set_send(s, "c2", "anchor_ch", sending=True)
        set_listen(s, "anchor", "anchor_ch", listening=True)
        remove_channel(s, gid, "ch")
        assert "ch" not in s.graphs[gid].send_edges["c1"]
        assert "ch" not in s.graphs[gid].listen_edges["c2"]

    def test_remove_channel_may_split(self):
        # Two creatures only connected via the channel — removing it
        # should split the graph into two.
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_creature(s, "c2", graph_id=gid)
        add_channel(s, gid, "ch")
        set_send(s, "c1", "ch", sending=True)
        set_listen(s, "c2", "ch", listening=True)
        delta = remove_channel(s, gid, "ch")
        assert delta.kind == "split"
        assert s.graph_count() == 2


# ── set_listen / set_send ────────────────────────────────────────


class TestSetEdges:
    def test_set_listen_toggle(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_channel(s, gid, "ch")
        set_listen(s, "c1", "ch", listening=True)
        assert "ch" in s.graphs[gid].listen_edges["c1"]
        set_listen(s, "c1", "ch", listening=False)
        assert "ch" not in s.graphs[gid].listen_edges["c1"]

    def test_set_send_toggle(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_channel(s, gid, "ch")
        set_send(s, "c1", "ch", sending=True)
        assert "ch" in s.graphs[gid].send_edges["c1"]
        set_send(s, "c1", "ch", sending=False)
        assert "ch" not in s.graphs[gid].send_edges["c1"]

    def test_set_listen_unknown_channel(self):
        s = TopologyState()
        add_creature(s, "c1")
        with pytest.raises(KeyError):
            set_listen(s, "c1", "nope", listening=True)

    def test_set_send_unknown_channel(self):
        s = TopologyState()
        add_creature(s, "c1")
        with pytest.raises(KeyError):
            set_send(s, "c1", "nope", sending=True)


# ── connect ──────────────────────────────────────────────────────


class TestConnect:
    def test_merges_graphs(self):
        s = TopologyState()
        add_creature(s, "c1")
        add_creature(s, "c2")
        assert s.graph_count() == 2
        name, delta = connect(s, "c1", "c2", channel="ch")
        assert name == "ch"
        assert delta.kind == "merge"
        assert s.graph_count() == 1

    def test_auto_named_channel(self):
        s = TopologyState()
        add_creature(s, "c1")
        add_creature(s, "c2")
        name, _ = connect(s, "c1", "c2")
        assert name.startswith("c1__c2__")

    def test_unknown_sender(self):
        s = TopologyState()
        add_creature(s, "c2")
        with pytest.raises(KeyError):
            connect(s, "ghost", "c2")

    def test_unknown_receiver(self):
        s = TopologyState()
        add_creature(s, "c1")
        with pytest.raises(KeyError):
            connect(s, "c1", "ghost")

    def test_intra_graph_no_merge(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_creature(s, "c2", graph_id=gid)
        name, delta = connect(s, "c1", "c2", channel="ch")
        # Same graph already; no merge.
        assert delta.kind == "nothing"


# ── disconnect ───────────────────────────────────────────────────


class TestDisconnect:
    def test_disconnect_specific_channel(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_creature(s, "c2", graph_id=gid)
        add_channel(s, gid, "ch1")
        add_channel(s, gid, "ch2")
        set_send(s, "c1", "ch1", sending=True)
        set_send(s, "c1", "ch2", sending=True)
        set_listen(s, "c2", "ch1", listening=True)
        set_listen(s, "c2", "ch2", listening=True)
        disconnect(s, "c1", "c2", channel="ch1")
        # ch1 edges gone; ch2 edges remain.
        assert "ch1" not in s.graphs[gid].send_edges["c1"]
        assert "ch2" in s.graphs[gid].send_edges["c1"]

    def test_disconnect_all_channels(self):
        s = TopologyState()
        add_creature(s, "c1")
        add_creature(s, "c2")
        connect(s, "c1", "c2", channel="ch")
        delta = disconnect(s, "c1", "c2")
        # Channel-based connection removed → split into 2 graphs.
        assert delta.kind == "split"

    def test_different_graphs_noop(self):
        s = TopologyState()
        add_creature(s, "c1")
        add_creature(s, "c2")
        # They are in different graphs.
        delta = disconnect(s, "c1", "c2")
        assert delta.kind == "nothing"


# ── find_components ──────────────────────────────────────────────


class TestFindComponents:
    def test_empty_graph(self):
        g = GraphTopology(graph_id="g1")
        assert find_components(g) == []

    def test_isolated_creatures(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_creature(s, "c2", graph_id=gid)
        comps = find_components(s.graphs[gid])
        # Two isolated creatures = two components.
        assert len(comps) == 2

    def test_connected_via_channel(self):
        s = TopologyState()
        gid = add_creature(s, "c1")
        add_creature(s, "c2", graph_id=gid)
        add_channel(s, gid, "ch")
        set_send(s, "c1", "ch", sending=True)
        set_listen(s, "c2", "ch", listening=True)
        comps = find_components(s.graphs[gid])
        assert len(comps) == 1
        assert comps[0] == {"c1", "c2"}


# ── _merge_graphs collision ───────────────────────────────────────


class TestMergeGraphsCollision:
    def test_channel_collision_raises(self):
        s = TopologyState()
        g1 = add_creature(s, "c1")
        g2 = add_creature(s, "c2")
        add_channel(s, g1, "shared")
        add_channel(s, g2, "shared")
        with pytest.raises(ValueError, match="collide"):
            _merge_graphs(s, g1, g2)


# ── _channels_used_by ────────────────────────────────────────────


class TestChannelsUsedBy:
    def test_union_listen_send(self):
        listen = {"c1": {"ch1"}, "c2": {"ch2"}}
        send = {"c1": {"ch3"}}
        used = _channels_used_by({"c1", "c2"}, listen, send)
        assert used == {"ch1", "ch2", "ch3"}

    def test_no_creatures(self):
        assert _channels_used_by(set(), {}, {}) == set()


# ── TopologyDelta dataclass ──────────────────────────────────────


class TestTopologyDelta:
    def test_defaults(self):
        d = TopologyDelta(kind="nothing")
        assert d.old_graph_ids == []
        assert d.new_graph_ids == []
        assert d.affected_creatures == set()
