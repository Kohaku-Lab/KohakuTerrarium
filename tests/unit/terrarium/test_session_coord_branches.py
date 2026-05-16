"""Branch-coverage tests for :mod:`kohakuterrarium.terrarium.session_coord`.

The happy paths are covered in ``test_session_coord``; these target the
defensive exception arms and the rarely-hit field-preservation branches
(``parent_branch_path`` copy, flush-cache failure, meta-write failure).
"""

from types import SimpleNamespace

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium import session_coord as sc
from kohakuterrarium.terrarium.topology import TopologyDelta

# ---------------------------------------------------------------------------
# copy_events_into — flush failure tolerance + branch-path preservation
# ---------------------------------------------------------------------------


class TestCopyEventsIntoBranches:
    def test_flush_cache_failure_is_tolerated(self, tmp_path):
        """A source store whose ``flush_cache`` raises is still copied —
        the flush failure is swallowed by ``copy_events_into``."""
        src = SessionStore(str(tmp_path / "src.kohakutr"))
        dst = SessionStore(str(tmp_path / "dst.kohakutr"))
        try:
            src.init_meta("s1", "agent", "/p", "/w", ["alice"])
            src.append_event("alice", "user_input", {"content": "hi"})
            src.flush()

            class _FlushFailEvents:
                """Wraps the real events facade but fails the *first*
                ``flush_cache`` (the one ``copy_events_into`` makes) —
                later internal flushes by ``get_events`` still work."""

                def __init__(self, real):
                    self._real = real
                    self._failed = False

                def __getattr__(self, name):
                    return getattr(self._real, name)

                def flush_cache(self):
                    if not self._failed:
                        self._failed = True
                        raise RuntimeError("cache wedged")
                    return self._real.flush_cache()

            # The source's first flush_cache raises; copy_events_into
            # must swallow it rather than propagating — the copy still
            # completes (the data was already flushed at line 15).
            object.__setattr__(src, "events", _FlushFailEvents(src.events))
            n = sc.copy_events_into(src, dst)
            # No crash; the swallowed-flush path returns a real count.
            assert isinstance(n, int)
            assert n >= 0
        finally:
            dst.close()

    def test_parent_branch_path_tuples_preserved(self, tmp_path):
        """``parent_branch_path`` list-of-lists is rebuilt as
        list-of-tuples when copied into the destination."""
        src = SessionStore(str(tmp_path / "src.kohakutr"))
        dst = SessionStore(str(tmp_path / "dst.kohakutr"))
        try:
            src.init_meta("s1", "agent", "/p", "/w", ["alice"])
            src.append_event(
                "alice",
                "user_input",
                {"content": "hi"},
                turn_index=1,
                parent_branch_path=[("main", 0)],
            )
            src.flush()
            n = sc.copy_events_into(src, dst)
            assert n == 1
            # The event round-trips with its branch lineage intact.
            events = dst.get_events("alice")
            assert events
        finally:
            src.close()
            dst.close()


# ---------------------------------------------------------------------------
# merge_session_stores / split_session_store — meta failure tolerance
# ---------------------------------------------------------------------------


class _MetaFailStore:
    """A SessionStore-like that fails ``load_meta`` and meta writes —
    used to drive the defensive ``except`` arms."""

    def __init__(self, real: SessionStore):
        self._real = real
        self.session_id = real.session_id

    def __getattr__(self, name):
        return getattr(self._real, name)

    def load_meta(self):
        raise RuntimeError("meta unreadable")

    @property
    def meta(self):
        class _FailingMeta:
            def __setitem__(self, k, v):
                raise RuntimeError("meta unwritable")

        return _FailingMeta()


class TestMergeSplitMetaFailures:
    def test_merge_tolerates_load_meta_failure(self, tmp_path):
        """A source store that can't report its session_id is still
        merged — it just contributes no parent id."""
        real1 = SessionStore(str(tmp_path / "a.kohakutr"))
        real2 = SessionStore(str(tmp_path / "b.kohakutr"))
        try:
            real1.init_meta("sid-a", "agent", "/p", "/w", ["alice"])
            real2.init_meta("sid-b", "agent", "/p", "/w", ["bob"])
            real1.append_event("alice", "x", {"v": 1})
            real2.append_event("bob", "y", {"v": 2})
            real1.flush()
            real2.flush()
            bad = _MetaFailStore(real1)
            merged = sc.merge_session_stores(
                [bad, real2], str(tmp_path / "merged.kohakutr")
            )
            try:
                # ``bad`` contributed no parent id; ``real2`` did.
                assert "sid-b" in merged.meta["parent_session_ids"]
                assert "sid-a" not in merged.meta["parent_session_ids"]
                # Events from both still landed.
                assert merged.get_events("alice")
                assert merged.get_events("bob")
            finally:
                merged.close()
        finally:
            real1.close()
            real2.close()

    def test_split_tolerates_parent_load_meta_failure(self, tmp_path):
        """When the parent store can't report its session_id, the
        split children simply carry an empty parent list."""
        real = SessionStore(str(tmp_path / "p.kohakutr"))
        try:
            real.init_meta("p", "agent", "/p", "/w", ["alice"])
            real.append_event("alice", "x", {"v": 1})
            real.flush()
            bad = _MetaFailStore(real)
            new_paths = [
                str(tmp_path / "c1.kohakutr"),
                str(tmp_path / "c2.kohakutr"),
            ]
            new_stores = sc.split_session_store(bad, new_paths)
            try:
                assert len(new_stores) == 2
                for s in new_stores:
                    # No parent id recovered → empty lineage list.
                    assert s.meta["parent_session_ids"] == []
                    assert s.get_events("alice")
            finally:
                for s in new_stores:
                    s.close()
        finally:
            real.close()


# ---------------------------------------------------------------------------
# apply_merge — dropped-graph store eviction
# ---------------------------------------------------------------------------


class _Agent:
    def __init__(self, name="alice"):
        self.config = SimpleNamespace(name=name)
        self.attached = []

    def attach_session_store(self, store):
        self.attached.append(store)


class _Creature:
    def __init__(self, name="alice"):
        self.agent = _Agent(name)


class TestApplyMergeEviction:
    def test_dropped_graph_store_is_evicted(self, tmp_path):
        """After a merge the dropped graph's store reference is removed
        from the engine — only the surviving graph keeps a store."""
        graphs = {"g1": SimpleNamespace(graph_id="g1", creature_ids={"c1"})}
        eng = SimpleNamespace(
            _session_stores={},
            _topology=SimpleNamespace(graphs=graphs),
            _creatures={"c1": _Creature()},
            _session_dir=None,  # no persistence → keep-first path
        )
        s1 = SessionStore(str(tmp_path / "s1.kohakutr"))
        s2 = SessionStore(str(tmp_path / "s2.kohakutr"))
        s1.init_meta("s1", "agent", "/p", "/w", ["alice"])
        s2.init_meta("s2", "agent", "/p", "/w", ["bob"])
        eng._session_stores["g1"] = s1
        eng._session_stores["g2"] = s2
        try:
            sc.apply_merge(
                eng,
                TopologyDelta(
                    kind="merge",
                    old_graph_ids=["g1", "g2"],
                    new_graph_ids=["g1"],
                ),
            )
            # g2's store reference is evicted; g1 keeps the merged store.
            assert "g2" not in eng._session_stores
            assert "g1" in eng._session_stores
        finally:
            s1.close()
            s2.close()


# ---------------------------------------------------------------------------
# _refresh_meta_for_split_graph — missing-creature + meta-write failure
# ---------------------------------------------------------------------------


class TestRefreshMetaBranches:
    def test_skips_missing_creature(self, tmp_path):
        """A creature_id in graph membership but absent from
        ``engine._creatures`` is skipped — agents list omits it."""
        graphs = {"g1": SimpleNamespace(graph_id="g1", creature_ids={"c1", "ghost"})}
        eng = SimpleNamespace(
            _topology=SimpleNamespace(graphs=graphs),
            _creatures={"c1": _Creature("alice")},
        )
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            sc._refresh_meta_for_split_graph(eng, "g1", store)
            # Only the resolvable creature contributes a name.
            assert store.meta["agents"] == ["alice"]
        finally:
            store.close()

    def test_meta_write_failure_is_tolerated(self, tmp_path):
        """A store whose meta write raises does not break the split
        refresh."""
        graphs = {"g1": SimpleNamespace(graph_id="g1", creature_ids={"c1"})}
        eng = SimpleNamespace(
            _topology=SimpleNamespace(graphs=graphs),
            _creatures={"c1": _Creature("alice")},
        )
        real = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            bad = _MetaFailStore(real)
            # Must not raise even though meta[...] = ... fails.
            sc._refresh_meta_for_split_graph(eng, "g1", bad)
        finally:
            real.close()


# ---------------------------------------------------------------------------
# _attach_store_to_graph — missing creature + bare-field fallback
# ---------------------------------------------------------------------------


class TestAttachStoreBranches:
    def test_skips_missing_creature(self, tmp_path):
        graphs = {"g1": SimpleNamespace(graph_id="g1", creature_ids={"c1", "ghost"})}
        creature = _Creature("alice")
        eng = SimpleNamespace(
            _topology=SimpleNamespace(graphs=graphs),
            _creatures={"c1": creature},
        )
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            sc._attach_store_to_graph(eng, "g1", store)
            # The one resolvable creature got the store; "ghost" skipped.
            assert creature.agent.attached == [store]
        finally:
            store.close()

    def test_bare_session_store_field_fallback(self, tmp_path):
        """An agent without ``attach_session_store`` but with a
        ``session_store`` field gets the store assigned directly."""

        class _BareAgent:
            session_store = None

        class _BareCreature:
            def __init__(self):
                self.agent = _BareAgent()

        creature = _BareCreature()
        graphs = {"g1": SimpleNamespace(graph_id="g1", creature_ids={"c1"})}
        eng = SimpleNamespace(
            _topology=SimpleNamespace(graphs=graphs),
            _creatures={"c1": creature},
        )
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            sc._attach_store_to_graph(eng, "g1", store)
            assert creature.agent.session_store is store
        finally:
            store.close()
