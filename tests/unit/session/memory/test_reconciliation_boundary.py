"""The session index protocol must not expose partial materializations."""

import numpy as np
import pytest

from kohakuterrarium.session.memory.async_search import live_memory_for_store
from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.store import SessionStore


class _Embedder(BaseEmbedder):
    dimensions = 3

    def encode(self, texts):
        return np.tile(np.array([1, 0, 0], dtype=np.float32), (len(texts), 1))


@pytest.mark.asyncio
async def test_vector_rebuild_refreshes_every_participants_keywords(tmp_path):
    path = tmp_path / "cross-agent.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        for name in ("alice", "bob"):
            await store.run(
                store.append_event,
                name,
                "user_input",
                {"content": f"{name} old needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        memory = await store.run(live_memory_for_store, store)
        for name in ("alice", "bob"):
            rows = await store.run(store.get_memory_events, name)
            await store.run(memory._memory._index_keywords, name, rows)

        await store.run(
            store.append_event,
            "bob",
            "user_input",
            {"content": "bob new needle"},
            turn_index=2,
            branch_id=1,
            parent_branch_path=[[1, 1]],
        )
        for _ in range(2):
            hits, warning = await memory.search(
                "needle",
                names=["alice"],
                config={},
                create_embedder=lambda _: _Embedder(),
                mode="semantic",
                agent="alice",
                k=5,
            )
            assert warning is None
            assert {h.content for h in hits} == {"alice old needle"}
        manifest = await store.run(memory._memory._manifest)
        bob_fts = await store.run(
            lambda: memory._memory._state.get("bob:cursor:keywords")
        )
        bob_vec = await store.run(
            lambda: memory._memory._state.get("bob:cursor:vectors:3")
        )
        assert manifest["vector"] == "ready"
        assert bob_fts["count"] == bob_vec["count"] == 2
    finally:
        store.close(update_status=False)


def test_source_reference_upgrade_marks_building_before_deletion(tmp_path, monkeypatch):
    path = tmp_path / "source.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        store.append_event(
            "alice",
            "user_input",
            {"content": "needle"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        raw = store.get_events("alice")
        with_refs = store.get_memory_events("alice")
        index = SessionMemory(str(path), index_all_branches=True)
        try:
            index._index_keywords("alice", raw)
            assert index._manifest()["fts"] == "ready"
            assert (
                index._state["alice:cursor:keywords"]["source_refs_complete"] is False
            )

            def fail_insert(*args, **kwargs):
                raise OSError("injected source-ref rebuild failure")

            monkeypatch.setattr(index._fts, "insert", fail_insert)
            with pytest.raises(OSError, match="injected"):
                index._index_keywords("alice", with_refs)
            assert index._manifest()["fts"] == "building"
            with pytest.raises(ValueError, match="index.*building"):
                index.search("needle", mode="fts")
        finally:
            index.close()
    finally:
        store.close(update_status=False)


def test_physical_database_aliases_share_mutation_and_build_locks(tmp_path):
    path = tmp_path / "real.kohakutr"
    alias = tmp_path / "alias.kohakutr"
    primary = SessionMemory(str(path), index_all_branches=True)
    try:
        try:
            alias.symlink_to(path)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable on this platform: {exc}")
        secondary = SessionMemory(str(alias), index_all_branches=True)
        try:
            with primary._mutation():
                with pytest.raises(RuntimeError, match="being modified"):
                    with secondary._mutation():
                        pass
            primary._claim_build("fts")
            with pytest.raises(RuntimeError, match="already in progress"):
                secondary._claim_build("fts")
        finally:
            secondary.close()
    finally:
        primary.close()


def test_rebuild_planning_does_not_mutate_live_fts_rows(tmp_path):
    path = tmp_path / "planning.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        store.append_event(
            "alice",
            "user_input",
            {"content": "needle"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        memory = SessionMemory(str(path), index_all_branches=True)
        try:
            memory.index_events("alice", store.get_events("alice"))
            count = memory._fts.count()
            before = dict(memory._manifest())
            before_cursor = dict(memory._state["alice:cursor:keywords"])
            plan, _ = memory._prepare(
                "alice", store.get_memory_events("alice"), "keywords", 0
            )
            assert plan.reset
            assert memory._fts.count() == count
            assert memory._manifest() == before
            assert memory._state["alice:cursor:keywords"] == before_cursor
        finally:
            memory.close()
    finally:
        store.close(update_status=False)


def test_vector_commit_refuses_changed_keyword_source_snapshot(tmp_path):
    path = str(tmp_path / "stale-plan.kohakutr")
    memory = SessionMemory(path, _Embedder(), index_all_branches=True)
    rows = [
        {
            "type": "user_input",
            "content": "first",
            "event_id": 1,
            "turn_index": 1,
            "branch_id": 1,
            "parent_branch_path": [],
        },
        {
            "type": "user_input",
            "content": "second",
            "event_id": 2,
            "turn_index": 2,
            "branch_id": 1,
            "parent_branch_path": [[1, 1]],
        },
    ]
    try:
        memory._index_keywords("alice", rows[:1])
        batch, blocks = memory._vector_batch("alice", rows[:1])
        vectors = memory._embedder.encode([block.content for block in blocks])
        memory._index_keywords("alice", rows)
        with pytest.raises(RuntimeError, match="snapshot|changed"):
            memory._commit_vectors("alice", batch, 1, blocks, vectors)
        assert memory._manifest()["vector"] != "ready"
        memory.index_events("alice", rows)
        assert memory._manifest()["vector"] == "ready"
        assert memory.get_stats()["vec_blocks"] == 2
    finally:
        memory.close()


def test_legacy_vector_cursor_without_keyword_revision_rebuilds(tmp_path):
    path = str(tmp_path / "legacy-keyword-revision.kohakutr")
    rows = [
        {
            "type": "user_input",
            "content": "needle",
            "turn_index": 1,
            "branch_id": 1,
            "event_id": 1,
            "parent_branch_path": [],
        }
    ]
    memory = SessionMemory(path, _Embedder(), index_all_branches=True)
    try:
        memory.index_events("alice", rows)
        assert memory._manifest()["vector"] == "ready"
        cursor = dict(memory._state["alice:cursor:vectors:3"])
        cursor.pop("keyword_revision")
        memory._state["alice:cursor:vectors:3"] = cursor
        with pytest.raises(ValueError, match="vector.*(stale|incompatible)"):
            memory.search("needle", mode="semantic", agent="alice")
        assert memory.index_events("alice", rows) == 1
        assert memory._manifest()["vector"] == "ready"
        assert memory.get_stats()["vec_blocks"] == 1
        assert len(memory.search("needle", mode="semantic", agent="alice")) == 1
    finally:
        memory.close()
