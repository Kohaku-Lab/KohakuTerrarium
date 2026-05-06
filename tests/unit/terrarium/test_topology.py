"""Tests for ``terrarium.topology`` — pure data, no Agent.

Covers graph membership, BFS connectivity, merge/split deltas, channel
uniqueness, and the invariants the runtime engine relies on.
"""

import pytest

from kohakuterrarium.terrarium.topology import (
    GraphTopology,
    TopologyState,
    add_channel,
    add_creature,
    connect,
    disconnect,
    find_components,
    remove_channel,
    remove_creature,
    set_listen,
    set_send,
)

# ---------------------------------------------------------------------------
# basic add/remove
# ---------------------------------------------------------------------------


class TestAddCreature:
    def test_solo_creates_singleton_graph(self):
        s = TopologyState()
        gid = add_creature(s, "alice")
        assert s.creature_count() == 1
        assert s.graph_count() == 1
        assert s.creature_to_graph["alice"] == gid
        assert "alice" in s.graphs[gid].creature_ids

    def test_two_solos_make_two_graphs(self):
        s = TopologyState()
        ga = add_creature(s, "alice")
        gb = add_creature(s, "bob")
        assert ga != gb
        assert s.graph_count() == 2

    def test_attach_to_existing_graph(self):
        s = TopologyState()
        ga = add_creature(s, "alice")
        gb = add_creature(s, "bob", graph_id=ga)
        assert ga == gb
        assert s.graph_count() == 1
        assert s.graphs[ga].creature_ids == {"alice", "bob"}

    def test_duplicate_id_rejected(self):
        s = TopologyState()
        add_creature(s, "alice")
        with pytest.raises(ValueError):
            add_creature(s, "alice")

    def test_unknown_graph_rejected(self):
        s = TopologyState()
        with pytest.raises(KeyError):
            add_creature(s, "alice", graph_id="bogus")


class TestRemoveCreature:
    def test_solo_removal_drops_graph(self):
        s = TopologyState()
        gid = add_creature(s, "alice")
        delta = remove_creature(s, "alice")
        assert s.creature_count() == 0
        assert s.graph_count() == 0
        assert delta.kind == "nothing"
        assert gid in delta.old_graph_ids
        assert delta.affected_creatures == {"alice"}

    def test_remove_from_pair_keeps_graph_when_isolated(self):
        s = TopologyState()
        gid = add_creature(s, "alice")
        add_creature(s, "bob", graph_id=gid)
        delta = remove_creature(s, "bob")
        assert delta.kind == "nothing"
        assert s.creature_count() == 1
        assert s.graphs[gid].creature_ids == {"alice"}


# ---------------------------------------------------------------------------
# channels
# ---------------------------------------------------------------------------


class TestAddChannel:
    def test_basic(self):
        s = TopologyState()
        gid = add_creature(s, "alice")
        info = add_channel(s, gid, "ch1")
        assert info.name == "ch1"
        assert "ch1" in s.graphs[gid].channels

    def test_duplicate_name_rejected(self):
        s = TopologyState()
        gid = add_creature(s, "alice")
        add_channel(s, gid, "ch1")
        with pytest.raises(ValueError):
            add_channel(s, gid, "ch1")

    def test_unknown_graph_rejected(self):
        s = TopologyState()
        with pytest.raises(KeyError):
            add_channel(s, "bogus", "ch1")

    def test_set_listen_on_unknown_channel_rejected(self):
        s = TopologyState()
        add_creature(s, "alice")
        with pytest.raises(KeyError):
            set_listen(s, "alice", "missing", listening=True)


class TestRemoveChannel:
    def test_basic(self):
        s = TopologyState()
        gid = add_creature(s, "alice")
        add_channel(s, gid, "ch1")
        delta = remove_channel(s, gid, "ch1")
        assert "ch1" not in s.graphs[gid].channels
        assert delta.kind == "nothing"

    def test_drops_dangling_edges(self):
        s = TopologyState()
        gid = add_creature(s, "alice")
        add_creature(s, "bob", graph_id=gid)
        add_channel(s, gid, "ch1")
        set_send(s, "alice", "ch1", sending=True)
        set_listen(s, "bob", "ch1", listening=True)
        # Both creatures share ch1; removing it splits the graph.
        delta = remove_channel(s, gid, "ch1")
        assert delta.kind == "split"
        assert len(delta.new_graph_ids) == 2


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


class TestConnect:
    def test_connect_within_graph(self):
        s = TopologyState()
        gid = add_creature(s, "alice")
        add_creature(s, "bob", graph_id=gid)
        add_channel(s, gid, "ch1")
        name, delta = connect(s, "alice", "bob", channel="ch1")
        assert name == "ch1"
        assert delta.kind == "nothing"
        assert "ch1" in s.graphs[gid].send_edges["alice"]
        assert "ch1" in s.graphs[gid].listen_edges["bob"]

    def test_connect_across_graphs_merges(self):
        s = TopologyState()
        ga = add_creature(s, "alice")
        gb = add_creature(s, "bob")
        assert ga != gb
        name, delta = connect(s, "alice", "bob", channel="ch1")
        assert delta.kind == "merge"
        assert s.graph_count() == 1
        assert s.creature_to_graph["alice"] == s.creature_to_graph["bob"]
        # All affected by merge → both creatures included
        assert {"alice", "bob"}.issubset(delta.affected_creatures)

    def test_auto_channel_name(self):
        s = TopologyState()
        add_creature(s, "alice")
        add_creature(s, "bob")
        name, delta = connect(s, "alice", "bob")  # no channel arg
        assert name.startswith("alice__bob__")
        assert delta.kind == "merge"

    def test_unknown_creature_rejected(self):
        s = TopologyState()
        add_creature(s, "alice")
        with pytest.raises(KeyError):
            connect(s, "alice", "ghost", channel="x")


class TestDisconnect:
    def test_split_when_only_link(self):
        s = TopologyState()
        add_creature(s, "alice")
        add_creature(s, "bob")
        connect(s, "alice", "bob", channel="ch1")
        # one shared graph with one channel; disconnect → split
        delta = disconnect(s, "alice", "bob", channel="ch1")
        assert delta.kind == "split"
        assert len(delta.new_graph_ids) == 2
        # Each creature ends up in a different graph.
        assert s.creature_to_graph["alice"] != s.creature_to_graph["bob"]

    def test_no_split_when_other_path_exists(self):
        # alice-ch1->bob, alice-ch2->bob → disconnect ch1 keeps them
        # connected via ch2.
        s = TopologyState()
        add_creature(s, "alice")
        add_creature(s, "bob")
        connect(s, "alice", "bob", channel="ch1")
        # they're already in the same graph after first connect
        gid = s.creature_to_graph["alice"]
        add_channel(s, gid, "ch2")
        set_send(s, "alice", "ch2", sending=True)
        set_listen(s, "bob", "ch2", listening=True)
        delta = disconnect(s, "alice", "bob", channel="ch1")
        assert delta.kind == "nothing"
        assert s.graph_count() == 1

    def test_disconnect_already_separate(self):
        s = TopologyState()
        add_creature(s, "alice")
        add_creature(s, "bob")
        delta = disconnect(s, "alice", "bob", channel="ch1")
        assert delta.kind == "nothing"


# ---------------------------------------------------------------------------
# BFS connectivity
# ---------------------------------------------------------------------------


class TestFindComponents:
    def test_isolated_creatures(self):
        g = GraphTopology(graph_id="g")
        g.creature_ids = {"a", "b", "c"}
        comps = find_components(g)
        assert len(comps) == 3
        assert {frozenset(c) for c in comps} == {
            frozenset({"a"}),
            frozenset({"b"}),
            frozenset({"c"}),
        }

    def test_chain(self):
        # a→ch1→b→ch2→c (path through channels makes one component)
        s = TopologyState()
        gid = add_creature(s, "a")
        add_creature(s, "b", graph_id=gid)
        add_creature(s, "c", graph_id=gid)
        add_channel(s, gid, "ch1")
        add_channel(s, gid, "ch2")
        set_send(s, "a", "ch1", sending=True)
        set_listen(s, "b", "ch1", listening=True)
        set_send(s, "b", "ch2", sending=True)
        set_listen(s, "c", "ch2", listening=True)
        comps = find_components(s.graphs[gid])
        assert len(comps) == 1
        assert comps[0] == {"a", "b", "c"}

    def test_two_components_in_one_graph(self):
        # Manually craft a graph with two disconnected pieces — this can
        # happen mid-mutation before _normalize_components runs.
        g = GraphTopology(graph_id="g")
        g.creature_ids = {"a", "b", "c", "d"}
        from kohakuterrarium.terrarium.topology import ChannelInfo

        g.channels["ch1"] = ChannelInfo(name="ch1")
        g.channels["ch2"] = ChannelInfo(name="ch2")
        g.send_edges = {"a": {"ch1"}, "c": {"ch2"}}
        g.listen_edges = {"b": {"ch1"}, "d": {"ch2"}}
        comps = find_components(g)
        assert len(comps) == 2
        comp_sets = {frozenset(c) for c in comps}
        assert frozenset({"a", "b"}) in comp_sets
        assert frozenset({"c", "d"}) in comp_sets


# ---------------------------------------------------------------------------
# split mechanics in detail
# ---------------------------------------------------------------------------


class TestSplitDetails:
    def test_split_preserves_each_components_channels(self):
        s = TopologyState()
        gid = add_creature(s, "a")
        add_creature(s, "b", graph_id=gid)
        add_creature(s, "c", graph_id=gid)
        add_creature(s, "d", graph_id=gid)
        add_channel(s, gid, "ab")
        add_channel(s, gid, "cd")
        add_channel(s, gid, "bc")
        set_send(s, "a", "ab", sending=True)
        set_listen(s, "b", "ab", listening=True)
        set_send(s, "c", "cd", sending=True)
        set_listen(s, "d", "cd", listening=True)
        set_send(s, "b", "bc", sending=True)
        set_listen(s, "c", "bc", listening=True)
        # Single graph with chain a-b-c-d.  Removing bc splits it.
        delta = remove_channel(s, gid, "bc")
        assert delta.kind == "split"
        assert s.graph_count() == 2

        # Find which graph holds {a,b} and which holds {c,d}.
        g_ab = next(g for g in s.graphs.values() if g.creature_ids == {"a", "b"})
        g_cd = next(g for g in s.graphs.values() if g.creature_ids == {"c", "d"})
        assert "ab" in g_ab.channels and "cd" not in g_ab.channels
        assert "cd" in g_cd.channels and "ab" not in g_cd.channels

    def test_split_picks_largest_component_to_keep_id(self):
        s = TopologyState()
        gid = add_creature(s, "a")
        add_creature(s, "b", graph_id=gid)
        add_creature(s, "c", graph_id=gid)
        add_channel(s, gid, "ab")
        add_channel(s, gid, "bc")
        set_send(s, "a", "ab", sending=True)
        set_listen(s, "b", "ab", listening=True)
        set_send(s, "b", "bc", sending=True)
        set_listen(s, "c", "bc", listening=True)
        # Now disconnect 'a' from 'b' (split off a from {b,c}).
        delta = disconnect(s, "a", "b", channel="ab")
        assert delta.kind == "split"
        assert gid in s.graphs  # original kept
        # Original graph keeps the 2-creature component {b,c}.
        assert s.graphs[gid].creature_ids == {"b", "c"}
        # 'a' moved to a brand-new graph.
        assert s.creature_to_graph["a"] != gid


# ---------------------------------------------------------------------------
# affected_creatures sanity
# ---------------------------------------------------------------------------


class TestAffectedCreatures:
    def test_merge_includes_everyone_in_new_graph(self):
        s = TopologyState()
        ga = add_creature(s, "a")
        add_creature(s, "b", graph_id=ga)
        gc = add_creature(s, "c")
        add_creature(s, "d", graph_id=gc)
        # Merge {a,b} with {c,d}
        _, delta = connect(s, "b", "c", channel="bc")
        assert delta.kind == "merge"
        assert delta.affected_creatures == {"a", "b", "c", "d"}

    def test_split_includes_everyone_from_original_graph(self):
        s = TopologyState()
        gid = add_creature(s, "a")
        add_creature(s, "b", graph_id=gid)
        add_channel(s, gid, "ab")
        set_send(s, "a", "ab", sending=True)
        set_listen(s, "b", "ab", listening=True)
        delta = remove_channel(s, gid, "ab")
        assert delta.kind == "split"
        assert {"a", "b"}.issubset(delta.affected_creatures)
