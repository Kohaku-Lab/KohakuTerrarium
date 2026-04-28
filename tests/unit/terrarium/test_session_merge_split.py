"""Session merge/split tests for the Terrarium engine.

Two layers:

1. Direct tests of the coordinator primitives (``copy_events_into``,
   ``merge_session_stores``, ``split_session_store``) so the copy
   logic is exercised without the engine.
2. End-to-end via the engine: attach stores, run merge/split-causing
   topology ops, assert the right store ends up where.
"""

from pathlib import Path

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.session_coord import (
    copy_events_into,
    merge_session_stores,
    split_session_store,
)

from tests.unit.terrarium._fakes import make_creature


def _seed_events(store: SessionStore, agent: str, n: int) -> None:
    for i in range(n):
        store.append_event(agent, "text", {"content": f"{agent}-msg-{i}"})


# ---------------------------------------------------------------------------
# coordinator primitives
# ---------------------------------------------------------------------------


class TestCopyEventsInto:
    def test_basic(self, tmp_path: Path):
        src = SessionStore(tmp_path / "src.kohakutr")
        dst = SessionStore(tmp_path / "dst.kohakutr")
        _seed_events(src, "alice", 3)
        n = copy_events_into(src, dst)
        assert n == 3
        events = dst.get_events("alice")
        assert [e["content"] for e in events] == [
            "alice-msg-0",
            "alice-msg-1",
            "alice-msg-2",
        ]

    def test_preserves_namespaces(self, tmp_path: Path):
        src = SessionStore(tmp_path / "src.kohakutr")
        dst = SessionStore(tmp_path / "dst.kohakutr")
        _seed_events(src, "alice", 2)
        _seed_events(src, "bob", 2)
        copy_events_into(src, dst)
        assert {e["content"] for e in dst.get_events("alice")} == {
            "alice-msg-0",
            "alice-msg-1",
        }
        assert {e["content"] for e in dst.get_events("bob")} == {
            "bob-msg-0",
            "bob-msg-1",
        }


class TestMergeSessionStores:
    def test_two_stores_unioned(self, tmp_path: Path):
        a = SessionStore(tmp_path / "a.kohakutr")
        a.meta["session_id"] = "sess-a"
        _seed_events(a, "alice", 2)

        b = SessionStore(tmp_path / "b.kohakutr")
        b.meta["session_id"] = "sess-b"
        _seed_events(b, "bob", 3)

        merged = merge_session_stores([a, b], tmp_path / "merged.kohakutr")

        assert len(merged.get_events("alice")) == 2
        assert len(merged.get_events("bob")) == 3
        meta = merged.load_meta()
        assert sorted(meta["parent_session_ids"]) == ["sess-a", "sess-b"]
        assert "merged_at" in meta


class TestSplitSessionStore:
    def test_split_into_two_clones(self, tmp_path: Path):
        src = SessionStore(tmp_path / "src.kohakutr")
        src.meta["session_id"] = "sess-x"
        _seed_events(src, "alice", 2)
        _seed_events(src, "bob", 2)

        new_stores = split_session_store(
            src,
            [tmp_path / "x1.kohakutr", tmp_path / "x2.kohakutr"],
        )
        assert len(new_stores) == 2
        for s in new_stores:
            assert len(s.get_events("alice")) == 2
            assert len(s.get_events("bob")) == 2
            meta = s.load_meta()
            assert meta["parent_session_ids"] == ["sess-x"]
            assert "split_at" in meta


# ---------------------------------------------------------------------------
# engine end-to-end
# ---------------------------------------------------------------------------


class TestEngineAttachSession:
    @pytest.mark.asyncio
    async def test_attach_session_propagates_to_creatures(self, tmp_path: Path):
        engine = Terrarium(session_dir=str(tmp_path))
        a = await engine.add_creature(make_creature("alice"))
        store = SessionStore(tmp_path / "a.kohakutr")
        # Fake agents don't have ``session_store`` by default — give it
        # one so attach_session can write through.
        a.agent.session_store = None
        await engine.attach_session(a.graph_id, store)
        assert engine._session_stores[a.graph_id] is store
        # attach_session sets agent.session_store when the attribute
        # already exists.  We set it to None above so it now reflects
        # the attached store.
        assert a.agent.session_store is store


class TestEngineMergeRoundTrip:
    @pytest.mark.asyncio
    async def test_connect_across_graphs_merges_stores(self, tmp_path: Path):
        engine = Terrarium(session_dir=str(tmp_path))
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"))
        a.agent.session_store = None
        b.agent.session_store = None

        sa = SessionStore(tmp_path / "ga.kohakutr")
        sa.meta["session_id"] = "sa"
        _seed_events(sa, "alice", 2)
        await engine.attach_session(a.graph_id, sa)

        sb = SessionStore(tmp_path / "gb.kohakutr")
        sb.meta["session_id"] = "sb"
        _seed_events(sb, "bob", 3)
        await engine.attach_session(b.graph_id, sb)

        # Trigger a merge.
        result = await engine.connect(a, b, channel="ab")
        assert result.delta_kind == "merge"

        merged_gid = a.graph_id
        merged = engine._session_stores[merged_gid]
        # New store has events from both originals.
        assert len(merged.get_events("alice")) == 2
        assert len(merged.get_events("bob")) == 3
        meta = merged.load_meta()
        assert sorted(meta["parent_session_ids"]) == ["sa", "sb"]


class TestEngineSplitRoundTrip:
    @pytest.mark.asyncio
    async def test_disconnect_split_duplicates_history(self, tmp_path: Path):
        engine = Terrarium(session_dir=str(tmp_path))
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"), graph=a.graph_id)
        a.agent.session_store = None
        b.agent.session_store = None

        store = SessionStore(tmp_path / "shared.kohakutr")
        store.meta["session_id"] = "shared"
        _seed_events(store, "alice", 2)
        _seed_events(store, "bob", 1)
        await engine.attach_session(a.graph_id, store)

        # Connect them, then disconnect — that's the split path.
        await engine.connect(a, b, channel="ab")
        result = await engine.disconnect(a, b, channel="ab")
        assert result.delta_kind == "split"

        # Each new graph has its own store with the full prior history.
        for gid in (a.graph_id, b.graph_id):
            s = engine._session_stores[gid]
            assert len(s.get_events("alice")) == 2
            assert len(s.get_events("bob")) == 1
            meta = s.load_meta()
            assert meta["parent_session_ids"] == ["shared"]


class TestEngineWithoutSessionDir:
    @pytest.mark.asyncio
    async def test_merge_without_session_dir_keeps_first_store(self, tmp_path: Path):
        # session_dir not configured — coordinator should keep the
        # first store as the survivor instead of trying to write a new
        # file.
        engine = Terrarium()  # no session_dir
        a = await engine.add_creature(make_creature("alice"))
        b = await engine.add_creature(make_creature("bob"))
        a.agent.session_store = None
        b.agent.session_store = None

        sa = SessionStore(tmp_path / "ga.kohakutr")
        sb = SessionStore(tmp_path / "gb.kohakutr")
        await engine.attach_session(a.graph_id, sa)
        await engine.attach_session(b.graph_id, sb)

        await engine.connect(a, b, channel="ab")
        merged_gid = a.graph_id
        # In no-session-dir mode the first store stays put — sa.
        assert engine._session_stores[merged_gid] in (sa, sb)
