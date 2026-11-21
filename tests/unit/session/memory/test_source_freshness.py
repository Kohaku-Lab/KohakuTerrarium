"""Steady-state source work is bounded by changes, not history length."""

from contextlib import closing
from pathlib import Path

import numpy as np
import pytest

from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.reconcile import SessionIndexReconciler
from kohakuterrarium.session.store import SessionStore


class _Vectors(BaseEmbedder):
    dimensions = 3

    def encode(self, texts):
        return np.ones((len(texts), self.dimensions), dtype=np.float32)


def _seed(store, *, count=40, agents=("alice", "bob")):
    store.init_meta("s", "terrarium", "", str(Path(store.path).parent), list(agents))
    for agent in agents:
        for turn in range(1, count + 1):
            store.append_event(
                agent,
                "user_input",
                {"content": f"{agent} needle {turn}"},
                turn_index=turn,
                branch_id=1,
                parent_branch_path=[],
            )


@pytest.mark.parametrize("with_vectors", [False, True])
def test_steady_query_uses_published_catalog_without_source_reads(
    tmp_path, monkeypatch, with_vectors
):
    path = tmp_path / "steady.kohakutr"
    with closing(SessionStore(path)) as store:
        _seed(store)
        with closing(
            SessionMemory(
                str(path),
                _Vectors() if with_vectors else None,
                index_all_branches=True,
            )
        ) as memory:
            reconciler = SessionIndexReconciler(store, memory)
            snapshot = reconciler.capture(["alice", "bob"], all_agents=True)
            reconciler.build(snapshot)
            before = dict(memory._manifest())

            def cannot_reenumerate(*args, **kwargs):
                raise AssertionError("steady query read the complete source")

            monkeypatch.setattr(store, "get_memory_events", cannot_reenumerate)
            for _ in range(3):
                src, views = reconciler.prepare_keywords(["alice"])
                assert src.published
                assert src.agents == ("alice", "bob")
                if with_vectors:
                    src, pending = reconciler.prepare_vectors(src, ["alice"])
                    assert pending == ()
                mode = "semantic" if with_vectors else "fts"
                hits = memory.search(
                    "needle",
                    mode=mode,
                    agent="alice",
                    branch_views=views,
                    k=3,
                )
                assert len(hits) == 3
                assert {hit.agent for hit in hits} == {"alice"}
                assert memory._manifest() == before


def test_append_reads_only_physical_suffix_and_preserves_other_agent(
    tmp_path, monkeypatch
):
    path = tmp_path / "append.kohakutr"
    store = SessionStore(path)
    try:
        _seed(store, count=20)
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            reconcile = SessionIndexReconciler(store, memory)
            reconcile.build(reconcile.capture(["alice", "bob"], all_agents=True))
            old = dict(memory._manifest())
            store.append_event(
                "bob",
                "user_input",
                {"content": "new-bob-needle"},
                turn_index=21,
                branch_id=1,
                parent_branch_path=[],
            )

            def cannot_reenumerate(*args, **kwargs):
                raise AssertionError("append re-read full prefix")

            monkeypatch.setattr(store, "get_memory_events", cannot_reenumerate)
            source, views = reconcile.prepare_keywords(["alice"])
            assert not source.published
            assert len(source.events["alice"].suffix) == 0
            assert len(source.events["bob"].suffix) == 1
            assert memory._manifest()["fts"] == "ready"
            assert [
                hit.content
                for hit in memory.search(
                    "new-bob-needle",
                    mode="fts",
                    agent="bob",
                    branch_views={**views, "bob": {21: 1}},
                )
            ] == ["new-bob-needle"]
            assert memory._manifest()["fts_revision"] > old["fts_revision"]
            again, _ = reconcile.prepare_keywords(["alice"])
            assert again.published
    finally:
        store.close(update_status=False)


def test_saved_query_reuses_persisted_source_without_restoring_event_counters(
    tmp_path, monkeypatch
):
    path = tmp_path / "saved.kohakutr"
    with closing(SessionStore(path)) as writer:
        _seed(writer)
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            reconciler = SessionIndexReconciler(writer, memory)
            reconciler.build(reconciler.capture(["alice", "bob"], all_agents=True))

    def forbidden_restore(*args):
        raise AssertionError("read-only search reconstructed event counters")

    monkeypatch.setattr(SessionStore, "_restore_counters", forbidden_restore)
    with closing(SessionStore.open_memory_reader(path)) as reader:
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:

            def cannot_enumerate(*args, **kwargs):
                raise AssertionError("saved search enumerated source")

            monkeypatch.setattr(reader, "get_memory_events", cannot_enumerate)
            source, views = SessionIndexReconciler(reader, memory).prepare_keywords(
                ["alice"], all_agents=True
            )
            assert source.published
            assert len(views["alice"]) == 40
            assert len(memory.search("needle", mode="fts", branch_views=views)) == 10


def test_branch_view_changes_reproject_summary_without_reading_source(
    tmp_path, monkeypatch
):
    path = tmp_path / "views.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        for branch, label in ((1, "old-needle"), (2, "new-needle")):
            store.append_event(
                "alice",
                "user_input",
                {"content": label},
                turn_index=1,
                branch_id=branch,
                parent_branch_path=[],
            )
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            reconcile = SessionIndexReconciler(store, memory)
            reconcile.build(reconcile.capture(["alice"], all_agents=True))
            monkeypatch.setattr(
                store,
                "get_memory_events",
                lambda *_: (_ for _ in ()).throw(
                    AssertionError("branch switch must not reread source")
                ),
            )
            for branch, expected in ((1, "old-needle"), (2, "new-needle")):
                store.state["alice:branch_view"] = {"1": branch}
                _, views = reconcile.prepare_keywords(["alice"])
                assert [
                    hit.content
                    for hit in memory.search("needle", mode="fts", branch_views=views)
                ] == [expected]
    finally:
        store.close(update_status=False)


def test_dirty_vector_rebuild_is_not_skipped_by_published_fts_source(tmp_path):
    """FTS may be current while vector data still need materialization."""
    path = tmp_path / "dirty-vector.kohakutr"
    store = SessionStore(path)
    try:
        _seed(store, count=2)
        with closing(
            SessionMemory(str(path), _Vectors(), index_all_branches=True)
        ) as memory:
            rec = SessionIndexReconciler(store, memory)
            rec.build(rec.capture(["alice", "bob"], all_agents=True))
            baseline = memory._manifest()["vector_generation"]
            store.append_event(
                "bob",
                "user_input",
                {"content": "bob new-vector-needle"},
                turn_index=3,
                branch_id=1,
                parent_branch_path=[],
            )
            source, _ = rec.prepare_keywords(["alice"])
            assert memory._manifest()["vector"] == "dirty"
            assert rec.capture_or_reuse(["alice"]).published
            rec.build(rec.capture_or_reuse(["alice"]))
            assert memory._manifest()["vector"] == "ready"
            assert memory._manifest()["vector_generation"] == baseline
            assert [
                h.content
                for h in memory.search(
                    "new-vector-needle", mode="semantic", agent="bob"
                )
            ][0] == "bob new-vector-needle"
    finally:
        store.close(update_status=False)


def test_source_identity_rejects_forked_catalog_with_unchanged_key_count(tmp_path):
    """Same number of physical rows is insufficient across Session identity."""
    import shutil

    parent = tmp_path / "parent.kohakutr"
    child = tmp_path / "child.kohakutr"
    with closing(SessionStore(parent)) as writer:
        _seed(writer, count=2, agents=("alice",))
        with closing(SessionMemory(str(parent), index_all_branches=True)) as memory:
            rec = SessionIndexReconciler(writer, memory)
            rec.build(rec.capture(["alice"], all_agents=True))
    shutil.copyfile(parent, child)
    with closing(SessionStore(child)) as forked:
        forked.meta["session_id"] = "independent-fork"
        with closing(SessionMemory(str(child), index_all_branches=True)) as memory:
            rec = SessionIndexReconciler(forked, memory)
            source = rec.capture_or_reuse(["alice"])
            assert not source.published
            assert len(source.events["alice"]) == 2
            rec.build(source)
            assert rec.capture_or_reuse(["alice"]).published


def test_mirrored_id_append_invalidates_physical_source_frontier(tmp_path):
    path = tmp_path / "mirrored-id.kohakutr"
    store = SessionStore(path)
    try:
        _seed(store, count=2, agents=("alice",))
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            rec = SessionIndexReconciler(store, memory)
            rec.build(rec.capture(["alice"], all_agents=True))
            # Imported mirrored records reuse a logical ID; the physical key
            # sequence, not event_id uniqueness, drives the frontier.
            store.append_event(
                "alice",
                "user_input",
                {"event_id": 1, "content": "imported-needle"},
                turn_index=3,
                branch_id=1,
                parent_branch_path=[],
            )
            source, views = rec.prepare_keywords(["alice"])
            assert not source.published
            assert len(source.events["alice"].suffix) == 1
            assert [
                h.content
                for h in memory.search(
                    "imported-needle", mode="fts", branch_views=views
                )
            ] == ["imported-needle"]
    finally:
        store.close(update_status=False)


def test_live_second_store_observes_committed_append_without_full_scan(
    tmp_path, monkeypatch
):
    """Another KT writer's committed physical append invalidates a read-only view."""
    path = tmp_path / "two-store.kohakutr"
    writer = SessionStore(path)
    reader = None
    try:
        _seed(writer, count=3, agents=("alice",))
        with closing(SessionMemory(str(path), index_all_branches=True)) as first:
            rec = SessionIndexReconciler(writer, first)
            rec.build(rec.capture(["alice"], all_agents=True))
        reader = SessionStore.open_memory_reader(path)
        with closing(SessionMemory(str(path), index_all_branches=True)) as second:
            rec = SessionIndexReconciler(reader, second)
            source = rec.capture_or_reuse(["alice"])
            assert source.published
            writer.append_event(
                "alice",
                "user_input",
                {"content": "new-cross-store-needle"},
                turn_index=4,
                branch_id=1,
                parent_branch_path=[],
            )
            writer.flush()

            def forbidden(*_args, **_kwargs):
                raise AssertionError("independent KT append caused a full read")

            monkeypatch.setattr(reader, "get_memory_events", forbidden)
            source, views = rec.prepare_keywords(["alice"])
            assert len(source.events["alice"].suffix) == 1
            assert [
                hit.content
                for hit in second.search(
                    "new-cross-store-needle", mode="fts", branch_views=views
                )
            ] == ["new-cross-store-needle"]
    finally:
        if reader is not None:
            reader.close(update_status=False)
        writer.close(update_status=False)


@pytest.mark.asyncio
async def test_saved_studio_uses_existing_source_catalog_without_capture(
    tmp_path, monkeypatch
):
    """Saved Studio API reopen must not enumerate history for a READY index."""
    from kohakuterrarium.studio.sessions.memory.search import search_session_memory

    path = tmp_path / "saved-studio.kohakutr"
    with closing(SessionStore(path)) as writer:
        _seed(writer, count=3, agents=("alice",))
        with closing(SessionMemory(str(path), index_all_branches=True)) as index:
            rec = SessionIndexReconciler(writer, index)
            rec.build(rec.capture(["alice"], all_agents=True))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("saved Studio query re-enumerated source")

    monkeypatch.setattr(SessionStore, "get_memory_events", forbidden)
    for _ in range(3):
        response = await search_session_memory(
            path, q="needle", mode="fts", k=3, agent="alice"
        )
        assert response["count"] == 3
        assert {item["agent"] for item in response["results"]} == {"alice"}
