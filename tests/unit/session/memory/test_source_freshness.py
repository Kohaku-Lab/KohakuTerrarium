"""Steady-state source work is bounded by changes, not history length."""

from contextlib import closing
from pathlib import Path

import numpy as np
import pytest

from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.reconcile import SessionIndexReconciler
from kohakuterrarium.session.memory.source import TailHistory
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


def test_append_crossing_catalog_projection_cannot_publish_stale_snapshot(
    tmp_path, monkeypatch
):
    """A KT append between the frontier probe and roster scan must retry."""
    from kohakuterrarium.session.memory import reconcile as reconcile_mod

    path = tmp_path / "concurrent-roster.kohakutr"
    store = SessionStore(path)
    try:
        _seed(store, count=3)
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            reconciler = SessionIndexReconciler(store, memory)
            reconciler.build(reconciler.capture(["alice", "bob"], all_agents=True))
            real_read = reconcile_mod.read_appended
            fired = []

            def concurrent_append(*args, **kwargs):
                result = real_read(*args, **kwargs)
                if not fired:
                    fired.append(True)
                    store.append_event(
                        "bob",
                        "user_input",
                        {"content": "bob concurrent-needle"},
                        turn_index=4,
                        branch_id=1,
                        parent_branch_path=[],
                    )
                return result

            monkeypatch.setattr(reconcile_mod, "read_appended", concurrent_append)
            snapshot = reconciler.capture_or_reuse(["alice"], all_agents=True)
            assert fired
            assert not snapshot.published
            assert len(snapshot.events["bob"]) == 4
            reconciler.refresh_keywords(snapshot)
            views = reconciler.selected_views(snapshot, ["bob"])
            assert [
                hit.content
                for hit in memory.search(
                    "concurrent-needle", mode="fts", branch_views=views
                )
            ] == ["bob concurrent-needle"]
            assert reconciler.capture_or_reuse(["alice"]).published
    finally:
        store.close(update_status=False)


def test_abandoned_fts_catalog_does_not_override_building_barrier(tmp_path):
    """A formerly READY source catalogue is not independent publication."""
    path = tmp_path / "stale-ready-catalog.kohakutr"
    store = SessionStore(path)
    try:
        _seed(store, count=3)
        with closing(SessionMemory(str(path), index_all_branches=True)) as original:
            source = SessionIndexReconciler(store, original).capture(
                ["alice", "bob"], all_agents=True
            )
            SessionIndexReconciler(store, original).build(source)
            assert original._state.get("alice:memory_source_catalog") is not None
            assert original._state.get("bob:memory_source_catalog") is not None
            original.force_rebuild(["alice", "bob"])
            assert original._manifest()["fts"] == "building"
            original._abort_builds()

        with closing(SessionMemory(str(path), index_all_branches=True)) as peer:
            reconcile = SessionIndexReconciler(store, peer)
            staged = reconcile.capture_or_reuse(["alice"])
            assert not staged.published
            with pytest.raises(ValueError, match="building|invalid"):
                peer.search("needle", mode="fts")
            reconcile.build(staged)
            assert peer._manifest()["fts"] == "ready"
            assert {hit.agent for hit in peer.search("needle", mode="fts")} == {
                "alice",
                "bob",
            }
    finally:
        store.close(update_status=False)


def test_appended_vector_batch_reuses_verified_suffix_without_full_scan(
    tmp_path, monkeypatch
):
    """Ordinary KT appends do not force a full re-embed source capture."""
    path = tmp_path / "incremental-vectors.kohakutr"
    store = SessionStore(path)
    try:
        _seed(store, count=3)
        with closing(
            SessionMemory(str(path), _Vectors(), index_all_branches=True)
        ) as memory:
            rec = SessionIndexReconciler(store, memory)
            rec.build(rec.capture(["alice", "bob"], all_agents=True))
            generation = memory._manifest()["vector_generation"]
            store.append_event(
                "bob",
                "user_input",
                {"content": "bob semantic-suffix-needle"},
                turn_index=4,
                branch_id=1,
                parent_branch_path=[],
            )

            def no_full_scan(*args, **kwargs):
                raise AssertionError("normal vector append reread full source")

            monkeypatch.setattr(store, "get_memory_events", no_full_scan)
            source, _views = rec.prepare_keywords(["alice"])
            assert memory._manifest()["vector"] == "dirty"
            assert len(source.events["bob"].suffix) == 1
            source, vector_names = rec.prepare_vectors(source, ["alice"])
            assert set(vector_names) == {"alice", "bob"}
            for name in vector_names:
                start, blocks = memory._vector_batch(name, source.events[name])
                vectors = _Vectors().encode([block.content for block in blocks])
                memory._commit_vectors(
                    name, start, len(source.events[name]), blocks, vectors
                )
            assert memory._manifest()["vector"] == "ready"
            assert memory._manifest()["vector_generation"] == generation
            assert "bob semantic-suffix-needle" in {
                hit.content
                for hit in memory.search(
                    "semantic-suffix-needle", mode="semantic", agent="bob", k=100
                )
            }
    finally:
        store.close(update_status=False)


def test_interrupted_vector_cannot_inherit_published_fts_catalog(tmp_path, monkeypatch):
    """A published FTS source is not proof of Vector ownership/readiness."""
    path = tmp_path / "interrupted-vector.kohakutr"
    store = SessionStore(path)
    try:
        _seed(store, count=2)
        with closing(
            SessionMemory(str(path), _Vectors(), index_all_branches=True)
        ) as first:
            rec = SessionIndexReconciler(store, first)
            rec.build(rec.capture(["alice", "bob"], all_agents=True))
            store.append_event(
                "bob",
                "user_input",
                {"content": "bob interrupted-vector-needle"},
                turn_index=3,
                branch_id=1,
                parent_branch_path=[],
            )
            rec.prepare_keywords(["alice"])
            assert first._manifest()["vector"] == "dirty"
            source = rec.capture_or_reuse(["alice"])
            assert source.published

            # A detached encoder has begun but has not committed its batch.
            rows = store.get_memory_events("bob")
            first._vector_batch("bob", rows)
            assert first._manifest()["vector"] == "building"
            calls = []
            original = store.get_memory_events

            def count_full_source(name):
                calls.append(name)
                return original(name)

            monkeypatch.setattr(store, "get_memory_events", count_full_source)
            guarded = rec.capture_or_reuse(["alice"])
            assert guarded.published  # FTS is legitimately READY.
            rebuilt, agents = rec.prepare_vectors(guarded, ["alice"])
            assert set(agents) == {"alice", "bob"}
            assert set(calls) == {"alice", "bob"}
            assert not rebuilt.published
            first._abort_vector_build()

        with closing(
            SessionMemory(str(path), _Vectors(), index_all_branches=True)
        ) as peer:
            rec = SessionIndexReconciler(store, peer)
            rec.build(rec.capture(["alice", "bob"], all_agents=True))
            assert peer._manifest()["vector"] == "ready"
            assert "bob interrupted-vector-needle" in {
                hit.content
                for hit in peer.search(
                    "interrupted-vector-needle", mode="semantic", agent="bob", k=50
                )
            }
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_live_semantic_normal_append_avoids_full_history_capture(
    tmp_path, monkeypatch
):
    """Exercise the real LiveSessionMemory semantic pipeline, not just its planner."""
    from kohakuterrarium.session.memory.async_search import live_memory_for_store

    path = tmp_path / "live-vector-append.kohakutr"
    store = SessionStore(path)
    try:
        _seed(store, count=2)
        live = await store.run(live_memory_for_store, store)
        initial, warning = await live.search(
            "bob needle",
            names=["alice", "bob"],
            config={},
            create_embedder=lambda _: _Vectors(),
            mode="semantic",
            agent="bob",
            k=20,
        )
        assert warning is None
        assert {hit.content for hit in initial} == {"bob needle 1", "bob needle 2"}
        await store.run(
            store.append_event,
            "bob",
            "user_input",
            {"content": "bob live-semantic-tail"},
            turn_index=3,
            branch_id=1,
            parent_branch_path=[],
        )

        def forbidden(*_args, **_kwargs):
            raise AssertionError("live semantic append reread all source history")

        monkeypatch.setattr(store, "get_memory_events", forbidden)
        hits, warning = await live.search(
            "live-semantic-tail",
            names=["alice", "bob"],
            config={},
            create_embedder=lambda _: _Vectors(),
            mode="semantic",
            agent="bob",
            k=20,
        )
        assert warning is None
        assert "bob live-semantic-tail" in {h.content for h in hits}
        assert (await store.run(live._memory._manifest))["vector"] == "ready"
    finally:
        store.close(update_status=False)


def test_saved_build_after_vector_abort_and_append_recaptures_full_source(
    tmp_path, monkeypatch
):
    """A virtual suffix cannot replay a destructive post-abort vector reset."""
    path = tmp_path / "vector-abort-and-append.kohakutr"
    store = SessionStore(path)
    try:
        _seed(store, count=2)
        with closing(
            SessionMemory(str(path), _Vectors(), index_all_branches=True)
        ) as memory:
            rec = SessionIndexReconciler(store, memory)
            rec.build(rec.capture(["alice", "bob"], all_agents=True))
            store.append_event(
                "bob",
                "user_input",
                {"content": "bob first-after-ready"},
                turn_index=3,
                branch_id=1,
                parent_branch_path=[],
            )
            rec.prepare_keywords(["alice"])
            # A previous vector operation reached BUILDING, then failed.
            memory._vector_batch("bob", store.get_memory_events("bob"))
            assert memory._manifest()["vector"] == "building"
            memory._abort_vector_build()
            assert memory._manifest()["vector_needs_clear"]

            store.append_event(
                "bob",
                "user_input",
                {"content": "bob second-after-abort"},
                turn_index=4,
                branch_id=1,
                parent_branch_path=[],
            )
            source = rec.capture_or_reuse(["alice"], all_agents=True)
            assert not source.published
            assert isinstance(source.events["bob"], TailHistory)
            calls = []
            get = store.get_memory_events

            def count_full(name):
                calls.append(name)
                return get(name)

            monkeypatch.setattr(store, "get_memory_events", count_full)
            rec.build(source)
            assert set(calls) == {"alice", "bob"}
            assert memory._manifest()["fts"] == memory._manifest()["vector"] == "ready"
            assert "bob second-after-abort" in {
                hit.content
                for hit in memory.search(
                    "second-after-abort", mode="semantic", agent="bob", k=50
                )
            }
    finally:
        store.close(update_status=False)


def test_first_append_to_previously_empty_agent_does_not_full_scan_vectors(
    tmp_path, monkeypatch
):
    """An empty namespace's count-zero Vector cursor is a valid prefix."""
    path = tmp_path / "empty-agent-vector-append.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        store.append_event(
            "alice",
            "user_input",
            {"content": "alice initial"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        with closing(
            SessionMemory(str(path), _Vectors(), index_all_branches=True)
        ) as memory:
            rec = SessionIndexReconciler(store, memory)
            rec.build(rec.capture(["alice", "bob"], all_agents=True))
            assert memory._state["bob:cursor:vectors:3"]["count"] == 0
            store.append_event(
                "bob",
                "user_input",
                {"content": "bob first-ever-vector"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )

            def forbidden(*args, **kwargs):
                raise AssertionError(
                    "first append after empty namespace reloaded history"
                )

            monkeypatch.setattr(store, "get_memory_events", forbidden)
            source, _ = rec.prepare_keywords(["alice"])
            assert source.events["bob"].old_count == 0
            assert len(source.events["bob"].suffix) == 1
            source, names = rec.prepare_vectors(source, ["alice"])
            assert set(names) == {"alice", "bob"}
            for name in names:
                batch, blocks = memory._vector_batch(name, source.events[name])
                memory._commit_vectors(
                    name,
                    batch,
                    len(source.events[name]),
                    blocks,
                    _Vectors().encode([block.content for block in blocks]),
                )
            assert "bob first-ever-vector" in {
                hit.content
                for hit in memory.search(
                    "first-ever-vector", mode="semantic", agent="bob", k=50
                )
            }
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_saved_semantic_search_uses_vector_suffix_after_append(
    tmp_path, monkeypatch
):
    """The real Saved Studio semantic endpoint uses append proof before build()."""
    from kohakuterrarium.studio.sessions.memory import search as studio_search

    path = tmp_path / "saved-semantic-append.kohakutr"
    with closing(SessionStore(path)) as writer:
        _seed(writer, count=2)
        with closing(
            SessionMemory(str(path), _Vectors(), index_all_branches=True)
        ) as memory:
            reconciler = SessionIndexReconciler(writer, memory)
            reconciler.build(reconciler.capture(["alice", "bob"], all_agents=True))
        writer.append_event(
            "bob",
            "user_input",
            {"content": "bob saved-semantic-tail"},
            turn_index=3,
            branch_id=1,
            parent_branch_path=[],
        )
        writer.flush()

    monkeypatch.setattr(studio_search, "create_embedder", lambda config: _Vectors())

    def forbidden(*args, **kwargs):
        raise AssertionError("Saved Semantic Append re-enumerated source")

    monkeypatch.setattr(SessionStore, "get_memory_events", forbidden)
    result = await studio_search.search_session_memory(
        path, q="saved-semantic-tail", mode="semantic", agent="bob", k=50
    )
    assert "bob saved-semantic-tail" in {item["content"] for item in result["results"]}
    with closing(SessionMemory(str(path), _Vectors(), index_all_branches=True)) as mem:
        assert mem._manifest()["fts"] == mem._manifest()["vector"] == "ready"
