"""Unit tests for :mod:`kohakuterrarium.terrarium.session_coord`."""

from types import SimpleNamespace


from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium import session_coord as sc
from kohakuterrarium.terrarium.topology import TopologyDelta

# ── copy_events_into ──────────────────────────────────────────


class TestCopyEventsInto:
    def test_basic_copy(self, tmp_path):
        src = SessionStore(str(tmp_path / "src.kohakutr"))
        dst = SessionStore(str(tmp_path / "dst.kohakutr"))
        try:
            src.init_meta("s1", "agent", "/p", "/w", ["alice"])
            src.append_event("alice", "user_input", {"content": "hi"})
            src.append_event("alice", "user_input", {"content": "bye"})
            src.flush()

            n = sc.copy_events_into(src, dst)
            assert n == 2
            events = dst.get_events("alice")
            assert len(events) == 2
        finally:
            src.close()
            dst.close()

    def test_copy_preserves_branch_fields(self, tmp_path):
        src = SessionStore(str(tmp_path / "src.kohakutr"))
        dst = SessionStore(str(tmp_path / "dst.kohakutr"))
        try:
            src.init_meta("s1", "agent", "/p", "/w", ["alice"])
            src.append_event(
                "alice",
                "user_input",
                {"content": "hi"},
                turn_index=2,
            )
            src.flush()
            n = sc.copy_events_into(src, dst)
            assert n == 1
        finally:
            src.close()
            dst.close()


# ── merge_session_stores ──────────────────────────────────────


class TestMergeSessionStores:
    def test_merge_two(self, tmp_path):
        s1 = SessionStore(str(tmp_path / "a.kohakutr"))
        s2 = SessionStore(str(tmp_path / "b.kohakutr"))
        try:
            s1.init_meta("sid-a", "agent", "/p", "/w", ["alice"])
            s2.init_meta("sid-b", "agent", "/p", "/w", ["bob"])
            s1.append_event("alice", "x", {"v": 1})
            s2.append_event("bob", "y", {"v": 2})
            s1.flush()
            s2.flush()

            merged = sc.merge_session_stores(
                [s1, s2], str(tmp_path / "merged.kohakutr")
            )
            try:
                parents = merged.meta["parent_session_ids"]
                assert "sid-a" in parents
                assert "sid-b" in parents
                assert merged.get_events("alice")
                assert merged.get_events("bob")
            finally:
                merged.close()
        finally:
            s1.close()
            s2.close()


# ── split_session_store ───────────────────────────────────────


class TestSplitSessionStore:
    def test_split_two(self, tmp_path):
        src = SessionStore(str(tmp_path / "parent.kohakutr"))
        try:
            src.init_meta("parent-sid", "agent", "/p", "/w", ["alice"])
            src.append_event("alice", "x", {"v": 1})
            src.flush()
            new_paths = [
                str(tmp_path / "c1.kohakutr"),
                str(tmp_path / "c2.kohakutr"),
            ]
            new_stores = sc.split_session_store(src, new_paths)
            try:
                assert len(new_stores) == 2
                for s in new_stores:
                    assert "parent-sid" in s.meta["parent_session_ids"]
                    assert s.get_events("alice")
            finally:
                for s in new_stores:
                    s.close()
        finally:
            src.close()


# ── _store_path_for ───────────────────────────────────────────


class TestStorePathFor:
    def test_no_session_dir(self):
        engine = SimpleNamespace()
        assert sc._store_path_for(engine, "g1") is None

    def test_with_session_dir(self, tmp_path):
        engine = SimpleNamespace(_session_dir=str(tmp_path))
        out = sc._store_path_for(engine, "g1")
        assert out == tmp_path / "g1.kohakutr"


# ── apply_merge ───────────────────────────────────────────────


class _FakeAgent:
    def __init__(self):
        self.session_store = None
        self.config = SimpleNamespace(name="alice")
        self.attached = []

    def attach_session_store(self, store):
        self.attached.append(store)
        self.session_store = store


class _FakeCreature:
    def __init__(self, name="alice"):
        self.agent = _FakeAgent()
        self.agent.config.name = name


def _make_engine(tmp_path, *, session_dir=True):
    creatures = {"c1": _FakeCreature()}
    graphs = {"g1": SimpleNamespace(graph_id="g1", creature_ids={"c1"})}
    eng = SimpleNamespace(
        _session_stores={},
        _topology=SimpleNamespace(graphs=graphs),
        _creatures=creatures,
    )
    if session_dir:
        eng._session_dir = str(tmp_path)
    return eng


class TestApplyMerge:
    def test_wrong_kind_noop(self):
        eng = SimpleNamespace()
        # Should not raise.
        sc.apply_merge(eng, TopologyDelta(kind="nothing"))

    def test_no_stores_noop(self, tmp_path):
        eng = _make_engine(tmp_path)
        sc.apply_merge(
            eng,
            TopologyDelta(
                kind="merge", old_graph_ids=["g1", "g2"], new_graph_ids=["g1"]
            ),
        )
        # _session_stores remains empty.
        assert eng._session_stores == {}

    def test_merge_with_persistence(self, tmp_path):
        eng = _make_engine(tmp_path)
        s1 = SessionStore(str(tmp_path / "s1.kohakutr"))
        s1.init_meta("s1", "agent", "/p", "/w", ["alice"])
        s1.append_event("alice", "x", {"v": 1})
        s1.flush()
        eng._session_stores["g1"] = s1
        try:
            sc.apply_merge(
                eng,
                TopologyDelta(
                    kind="merge",
                    old_graph_ids=["g1"],
                    new_graph_ids=["g1"],
                ),
            )
            # New store is at the merged path.
            assert "g1" in eng._session_stores
        finally:
            for s in list(eng._session_stores.values()):
                s.close()
            s1.close()

    def test_merge_without_persistence_keeps_first(self, tmp_path):
        # No session_dir set on engine.
        eng = _make_engine(tmp_path, session_dir=False)
        s1 = SessionStore(str(tmp_path / "a.kohakutr"))
        s1.init_meta("a", "agent", "/p", "/w", ["alice"])
        eng._session_stores["g1"] = s1
        try:
            sc.apply_merge(
                eng,
                TopologyDelta(
                    kind="merge",
                    old_graph_ids=["g1"],
                    new_graph_ids=["g1"],
                ),
            )
            assert eng._session_stores["g1"] is s1
        finally:
            s1.close()


# ── apply_split ───────────────────────────────────────────────


class TestApplySplit:
    def test_wrong_kind_noop(self):
        sc.apply_split(SimpleNamespace(), TopologyDelta(kind="nothing"))

    def test_no_parent_store_noop(self, tmp_path):
        eng = _make_engine(tmp_path)
        sc.apply_split(
            eng,
            TopologyDelta(
                kind="split",
                old_graph_ids=["g1"],
                new_graph_ids=["a", "b"],
            ),
        )
        assert "a" not in eng._session_stores

    def test_split_with_persistence(self, tmp_path):
        eng = _make_engine(tmp_path)
        # Add additional graphs in topology for new ids.
        eng._topology.graphs["a"] = SimpleNamespace(graph_id="a", creature_ids={"c1"})
        eng._topology.graphs["b"] = SimpleNamespace(graph_id="b", creature_ids=set())
        parent = SessionStore(str(tmp_path / "p.kohakutr"))
        parent.init_meta("p", "agent", "/p", "/w", ["alice"])
        parent.append_event("alice", "x", {"v": 1})
        parent.flush()
        eng._session_stores["g1"] = parent
        try:
            sc.apply_split(
                eng,
                TopologyDelta(
                    kind="split",
                    old_graph_ids=["g1"],
                    new_graph_ids=["a", "b"],
                ),
            )
            assert "a" in eng._session_stores
            assert "b" in eng._session_stores
        finally:
            for s in list(eng._session_stores.values()):
                s.close()
            parent.close()

    def test_split_without_persistence_keeps_parent_on_first(self, tmp_path):
        eng = _make_engine(tmp_path, session_dir=False)
        eng._topology.graphs["a"] = SimpleNamespace(graph_id="a", creature_ids={"c1"})
        parent = SessionStore(str(tmp_path / "p.kohakutr"))
        parent.init_meta("p", "agent", "/p", "/w", ["alice"])
        eng._session_stores["g1"] = parent
        try:
            sc.apply_split(
                eng,
                TopologyDelta(
                    kind="split",
                    old_graph_ids=["g1"],
                    new_graph_ids=["a", "b"],
                ),
            )
            assert eng._session_stores["a"] is parent
        finally:
            parent.close()


# ── _refresh_meta_for_split_graph + _attach_store_to_graph ───


class TestRefreshAndAttach:
    def test_refresh_unknown_graph_noop(self, tmp_path):
        eng = _make_engine(tmp_path)
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            sc._refresh_meta_for_split_graph(eng, "ghost", store)
        finally:
            store.close()

    def test_refresh_writes_meta(self, tmp_path):
        eng = _make_engine(tmp_path)
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            sc._refresh_meta_for_split_graph(eng, "g1", store)
            assert store.meta["agents"] == ["alice"]
            assert store.meta["config_type"] == "agent"
        finally:
            store.close()

    def test_attach_unknown_graph_noop(self, tmp_path):
        eng = _make_engine(tmp_path)
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            sc._attach_store_to_graph(eng, "ghost", store)
        finally:
            store.close()

    def test_attach_uses_agent_helper(self, tmp_path):
        eng = _make_engine(tmp_path)
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            sc._attach_store_to_graph(eng, "g1", store)
            agent = eng._creatures["c1"].agent
            assert agent.attached == [store]
        finally:
            store.close()
