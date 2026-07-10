"""Unit tests for :mod:`kohakuterrarium.terrarium.topology_snapshot`."""

import kohakuterrarium.terrarium.topology_snapshot as topo_snap
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder, _FakeAgent


def _payload(creature_id="alice"):
    return {
        "channels": [{"name": "runtime", "description": "d"}],
        "listen_edges": {creature_id: ["runtime"]},
        "send_edges": {creature_id: ["runtime"]},
    }


async def _engine_with_creature(tmp_path):
    t = await TestTerrariumBuilder().build()
    agent = _FakeAgent(name="alice")
    agent.attach_session_store = lambda s: None
    c = Creature(creature_id="alice", name="alice", agent=agent, config=agent.config)
    await t.add_creature(c, start=False)
    gid = c.graph_id
    store = SessionStore(str(tmp_path / "s.kohakutr"))
    t._session_stores[gid] = store
    return t, gid, store


class TestReplayFailureAtomicity:
    async def test_transient_channel_failure_keeps_saved_snapshot(self, tmp_path):
        # A transient add_channel error during replay must not become
        # permanent by overwriting the saved snapshot with the partial
        # result.
        t, gid, store = await _engine_with_creature(tmp_path)
        try:
            saved = _payload()
            store.meta[topo_snap.META_KEY] = saved

            async def boom(*args, **kwargs):
                raise RuntimeError("transient")

            t.add_channel = boom
            await topo_snap.replay(t, gid)
            after = store.meta[topo_snap.META_KEY]
            assert after["channels"] == saved["channels"]
            assert after["listen_edges"] == saved["listen_edges"]
            assert after["send_edges"] == saved["send_edges"]
        finally:
            await t.shutdown()
            store.close()

    async def test_unresolved_creature_edge_keeps_saved_snapshot(self, tmp_path):
        # An edge whose creature isn't in the rebuilt graph (post-split
        # leftover) is skipped for THIS resume but must stay in the
        # saved snapshot for a future resume where it resolves.
        t, gid, store = await _engine_with_creature(tmp_path)
        try:
            saved = _payload(creature_id="ghost")
            store.meta[topo_snap.META_KEY] = saved
            await topo_snap.replay(t, gid)
            after = store.meta[topo_snap.META_KEY]
            assert after["listen_edges"] == {"ghost": ["runtime"]}
        finally:
            await t.shutdown()
            store.close()

    async def test_later_mutation_keeps_unresolved_edges(self, tmp_path):
        # An incomplete replay leaves the live graph partial; the NEXT
        # ordinary mutation snapshots that partial state — without the
        # leftover union, the unresolved edge is permanently erased.
        t, gid, store = await _engine_with_creature(tmp_path)
        try:
            saved = {
                "channels": [{"name": "runtime", "description": "r"}],
                "listen_edges": {"missing": ["runtime"]},
                "send_edges": {"alice": ["runtime"]},
            }
            store.meta[topo_snap.META_KEY] = saved
            await topo_snap.replay(t, gid)
            # Ordinary runtime mutation AFTER the incomplete replay.
            await t.add_channel(gid, "later", description="")
            after = store.meta[topo_snap.META_KEY]
            assert after["listen_edges"].get("missing") == ["runtime"], (
                "unresolved saved edge erased by the post-replay "
                f"mutation snapshot: {after!r}"
            )
            assert after["send_edges"].get("alice") == ["runtime"]
            assert {c["name"] for c in after["channels"]} >= {"runtime", "later"}
        finally:
            await t.shutdown()
            store.close()

    async def test_successful_replay_writes_complete_snapshot(self, tmp_path):
        t, gid, store = await _engine_with_creature(tmp_path)
        try:
            store.meta[topo_snap.META_KEY] = _payload()
            await topo_snap.replay(t, gid)
            after = store.meta[topo_snap.META_KEY]
            assert {c["name"] for c in after["channels"]} == {"runtime"}
            assert after["listen_edges"] == {"alice": ["runtime"]}
            assert after["send_edges"] == {"alice": ["runtime"]}
        finally:
            await t.shutdown()
            store.close()


class TestMalformedMetadataDefense:
    async def test_malformed_edge_value_is_ignored_not_fatal(self, tmp_path):
        # Stale / hand-damaged persisted metadata must degrade to
        # "ignore the invalid value", never abort the resume.
        t, gid, store = await _engine_with_creature(tmp_path)
        try:
            store.meta[topo_snap.META_KEY] = {
                "channels": [{"name": "runtime", "description": "r"}],
                "listen_edges": {"ghost": [["unhashable"], "runtime", ""]},
                "send_edges": {},
            }
            await topo_snap.replay(t, gid)
            leftovers = t._topology_replay_leftovers.get(gid)
            assert leftovers is not None
            # Valid unresolved entry preserved; malformed values gone.
            assert leftovers["listen_edges"] == {"ghost": ["runtime"]}
            assert any(c.get("name") == "runtime" for c in leftovers["channels"])
        finally:
            await t.shutdown()
            store.close()

    async def test_malformed_channel_declaration_is_ignored(self, tmp_path):
        t, gid, store = await _engine_with_creature(tmp_path)
        try:
            store.meta[topo_snap.META_KEY] = {
                "channels": [{"name": ["unhashable"]}],
                "listen_edges": {"ghost": ["runtime"]},
                "send_edges": {},
            }
            await topo_snap.replay(t, gid)
            leftovers = t._topology_replay_leftovers.get(gid)
            assert leftovers is not None
            assert leftovers["listen_edges"] == {"ghost": ["runtime"]}
            # Closure minted a minimal declaration for the referenced
            # channel; the malformed declaration was ignored.
            assert any(c.get("name") == "runtime" for c in leftovers["channels"])
        finally:
            await t.shutdown()
            store.close()


class TestSplitDependencyClosure:
    async def test_split_children_keep_unresolved_edge_with_its_channel(self, tmp_path):
        # Full lifecycle: replay restores the channel but not its ghost
        # edge → a later disconnect SPLITS the graph → normalization
        # prunes channels no live edge uses. Each child's snapshot must
        # still carry BOTH the ghost edge AND the channel declaration
        # it depends on, or cold replay can never resolve it.
        from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

        t = (
            await TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_connection("alice", "bob", channel="link")
            .build()
        )
        try:
            gid = next(iter(t._topology.graphs))
            store = SessionStore(str(tmp_path / "parent.kohakutr"))
            t._session_stores[gid] = store
            t._owned_sessions.add(gid)
            store.meta[topo_snap.META_KEY] = {
                "channels": [{"name": "runtime", "description": "r"}],
                "listen_edges": {"ghost": ["runtime"]},
                "send_edges": {},
            }
            await topo_snap.replay(t, gid)
            assert "runtime" in t._topology.graphs[gid].channels

            t._session_dir = str(tmp_path)
            result = await t.disconnect("alice", "bob", channel="link")
            assert result.delta_kind == "split"

            checked = 0
            for cgid in t._topology.graphs:
                child_store = t._session_stores.get(cgid)
                if child_store is None:
                    continue
                saved = child_store.meta.get(topo_snap.META_KEY)
                if saved is None:
                    topo_snap.snapshot(t, cgid)
                    saved = child_store.meta.get(topo_snap.META_KEY)
                assert saved is not None
                assert saved["listen_edges"].get("ghost") == [
                    "runtime"
                ], f"child {cgid} lost the unresolved edge: {saved!r}"
                assert any(c.get("name") == "runtime" for c in saved["channels"]), (
                    f"child {cgid} carries a DANGLING edge — the runtime "
                    f"channel declaration is missing: {saved!r}"
                )
                checked += 1
            assert checked, "split produced no snapshotted children"
        finally:
            await t.shutdown()
            for st in list(t._session_stores.values()):
                try:
                    st.close()
                except Exception:
                    pass
            store.close()


class TestPerGraphSuppression:
    async def test_replay_on_one_graph_does_not_suppress_another(self, tmp_path):
        t, gid, store = await _engine_with_creature(tmp_path)
        try:
            other_store = SessionStore(str(tmp_path / "other.kohakutr"))
            t._session_stores["graph_other"] = other_store
            t._topology.graphs["graph_other"] = type(t._topology.graphs[gid])(
                graph_id="graph_other"
            )
            t._restoring_topology_graphs = {gid}
            topo_snap.snapshot(t, gid)
            assert topo_snap.META_KEY not in other_store.meta
            topo_snap.snapshot(t, "graph_other")
            assert topo_snap.META_KEY in other_store.meta
            assert topo_snap.META_KEY not in store.meta
            other_store.close()
        finally:
            t._restoring_topology_graphs = set()
            await t.shutdown()
            store.close()
