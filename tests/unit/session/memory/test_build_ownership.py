"""A build may continue across Agents, but an aborted owner must not survive."""

import asyncio
import threading
from contextlib import closing

import numpy as np
import pytest

from kohakuterrarium.session.memory.async_search import live_memory_for_store
from kohakuterrarium.session.memory.embedding import BaseEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.reconcile import SessionIndexReconciler
from kohakuterrarium.session.store import SessionStore


class _Embedder(BaseEmbedder):
    dimensions = 3

    def encode(self, texts):
        return np.ones((len(texts), self.dimensions), dtype=np.float32)


class _FailEncode(_Embedder):
    def encode(self, texts):
        raise OSError("injected model failure")


def _rows(agent, *, count=1):
    return [
        {
            "event_id": n + 1,
            "type": "user_input",
            "content": f"{agent} needle {n}",
            "turn_index": n + 1,
            "branch_id": 1,
            "parent_branch_path": [] if n == 0 else [[n, 1]],
        }
        for n in range(count)
    ]


def _failure(*args, **kwargs):
    raise OSError("injected FTS failure")


@pytest.mark.asyncio
async def test_failed_live_keyword_refresh_abandons_build_and_peer_recovers(
    tmp_path, monkeypatch
):
    path = tmp_path / "live-fts.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        for row in _rows("alice", count=1):
            store.append_event(
                "alice",
                row["type"],
                {"content": row["content"]},
                turn_index=row["turn_index"],
                branch_id=1,
                parent_branch_path=row["parent_branch_path"],
            )
        live = await store.run(live_memory_for_store, store)
        hits, _ = await live.search(
            "needle",
            names=["alice"],
            config=None,
            create_embedder=lambda _: None,
            mode="fts",
        )
        assert len(hits) == 1
        store.append_event(
            "alice",
            "user_input",
            {"content": "needle second"},
            turn_index=2,
            branch_id=1,
            parent_branch_path=[[1, 1]],
        )
        with monkeypatch.context() as patch:
            patch.setattr(live._memory._fts, "insert", _failure)
            with pytest.raises(OSError, match="injected"):
                await live.search(
                    "needle",
                    names=["alice"],
                    config=None,
                    create_embedder=lambda _: None,
                    mode="fts",
                )
        assert live._memory._manifest()["fts"] != "ready"
        assert live._memory._fts_build_lock is None
        with closing(SessionMemory(str(path), index_all_branches=True)) as peer:
            peer._index_keywords("alice", store.get_memory_events("alice"))
            assert peer._manifest()["fts"] == "ready"
            assert len(peer.search("needle", mode="fts")) == 2
    finally:
        store.close(update_status=False)


@pytest.mark.parametrize("entry", ["first-clear", "force-clear", "append-insert"])
def test_fts_fault_aborts_before_owner_close(tmp_path, monkeypatch, entry):
    path = tmp_path / f"{entry}.kohakutr"
    rows = _rows("alice")
    with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
        if entry != "first-clear":
            memory._index_keywords("alice", rows)
        rows_to_build = rows if entry != "append-insert" else _rows("alice", count=2)
        target = memory._fts
        if entry == "append-insert":
            monkeypatch.setattr(target, "insert", _failure)
        else:
            monkeypatch.setattr(target, "clear", _failure)
        with pytest.raises(OSError, match="injected"):
            if entry == "force-clear":
                memory.force_rebuild(["alice"])
            else:
                memory._index_keywords("alice", rows_to_build)
        assert memory._manifest()["fts"] != "ready"
        assert memory._fts_build_lock is None
        with closing(SessionMemory(str(path), index_all_branches=True)) as peer:
            peer._index_keywords("alice", rows_to_build)
            assert peer._manifest()["fts"] == "ready"
            assert len(peer.search("needle", mode="fts")) == len(rows_to_build)


@pytest.mark.parametrize("stage", ["encode", "vector-insert"])
def test_sync_vector_fault_releases_owner_and_peer_can_recover(
    tmp_path, monkeypatch, stage
):
    path = tmp_path / f"vector-{stage}.kohakutr"
    bad = _FailEncode() if stage == "encode" else _Embedder()
    rows = _rows("alice")
    with closing(SessionMemory(str(path), bad, index_all_branches=True)) as memory:
        if stage == "vector-insert":
            monkeypatch.setattr(memory._vec, "insert", _failure)
        with pytest.raises(OSError, match="injected"):
            memory.index_events("alice", rows)
        assert memory._vector_build_lock is None
        assert memory._manifest()["vector"] != "ready"
        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as peer:
            peer.index_events("alice", rows)
            assert peer._manifest()["vector"] == "ready"
            assert len(peer.search("needle", mode="semantic", agent="alice")) == 1


def test_cross_agent_success_retains_owner_but_failed_progress_aborts(tmp_path):
    path = tmp_path / "multi-agent-abort.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        for name in ("alice", "bob"):
            store.append_event(
                name,
                "user_input",
                {"content": f"{name} needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
            source = SessionIndexReconciler(store, memory).capture(
                ["alice", "bob"], all_agents=True
            )
            memory.force_rebuild(list(source.agents))
            memory._index_keywords("alice", source.events["alice"])
            assert memory._manifest()["fts"] == "building"
            assert memory._fts_build_lock is not None
            with closing(SessionMemory(str(path), index_all_branches=True)) as peer:
                with pytest.raises(RuntimeError, match="already in progress"):
                    peer._index_keywords("bob", source.events["bob"])

            def fail_progress(*args):
                raise OSError("injected progress failure")

            with pytest.raises(OSError, match="progress failure"):
                SessionIndexReconciler(store, memory).build(
                    source, force=True, progress=fail_progress
                )
            assert memory._fts_build_lock is None
            assert memory._manifest()["fts"] != "ready"
            with closing(SessionMemory(str(path), index_all_branches=True)) as peer:
                peer._index_keywords("alice", source.events["alice"])
                peer._index_keywords("bob", source.events["bob"])
                assert peer._manifest()["fts"] == "ready"
    finally:
        store.close(update_status=False)


def test_failed_second_agent_aborts_both_shared_owners(tmp_path, monkeypatch):
    """A single cross-Agent failure cannot strand FTS or Vector ownership."""
    path = tmp_path / "both-build-locks.kohakutr"
    alice, bob = _rows("alice"), _rows("bob")
    with closing(
        SessionMemory(str(path), _Embedder(), index_all_branches=True)
    ) as owner:
        owner.force_rebuild(["alice", "bob"])
        owner.index_events("alice", alice)
        assert owner._manifest()["fts"] == "building"
        assert owner._manifest()["vector"] == "building"
        assert owner._fts_build_lock is not None
        assert owner._vector_build_lock is not None
        with monkeypatch.context() as patch:
            patch.setattr(owner._fts, "insert", _failure)
            with pytest.raises(OSError, match="injected"):
                owner.index_events("bob", bob)
        assert owner._fts_build_lock is None
        assert owner._vector_build_lock is None
        assert owner._manifest()["fts"] != "ready"
        assert owner._manifest()["vector"] != "ready"
        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as peer:
            peer.index_events("alice", alice)
            peer.index_events("bob", bob)
            assert peer._manifest()["fts"] == peer._manifest()["vector"] == "ready"
            assert len(peer.search("needle", mode="semantic")) == 2


def test_failed_abort_persistence_still_releases_ownership_and_original_error(
    tmp_path, monkeypatch
):
    path = tmp_path / "abort-write-failure.kohakutr"
    rows = _rows("alice")
    with closing(SessionMemory(str(path), index_all_branches=True)) as owner:
        owner._index_keywords("alice", rows)

        def fail_then_block_abort(*args, **kwargs):
            monkeypatch.setattr(owner, "_save_manifest", _failure)
            raise RuntimeError("ORIGINAL-INSERT-FAILURE")

        with monkeypatch.context() as patch:
            patch.setattr(owner._fts, "insert", fail_then_block_abort)
            with pytest.raises(RuntimeError, match="ORIGINAL-INSERT-FAILURE"):
                owner._index_keywords("alice", _rows("alice", count=2))
        assert owner._fts_build_lock is None
        assert owner._manifest()["fts"] == "building"
        with closing(SessionMemory(str(path), index_all_branches=True)) as peer:
            peer._index_keywords("alice", _rows("alice", count=2))
            assert peer._manifest()["fts"] == "ready"


def test_agent_source_reset_failure_releases_fts_owner(tmp_path, monkeypatch):
    path = tmp_path / "agent-clear.kohakutr"
    before = _rows("alice")
    changed = [{**before[0], "content": "replacement needle"}]
    with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
        memory._index_keywords("alice", before)
        with monkeypatch.context() as patch:
            patch.setattr(memory._fts, "delete", _failure)
            with pytest.raises(OSError, match="injected"):
                memory._index_keywords("alice", changed)
        assert memory._fts_build_lock is None
        assert memory._manifest()["fts"] != "ready"
        with closing(SessionMemory(str(path), index_all_branches=True)) as peer:
            peer._index_keywords("alice", changed)
            assert [hit.content for hit in peer.search("needle", mode="fts")] == [
                "replacement needle"
            ]


@pytest.mark.asyncio
async def test_cancelled_live_fts_waiter_does_not_strand_accepted_worker(
    tmp_path, monkeypatch
):
    path = tmp_path / "cancelled-waiter.kohakutr"
    store = SessionStore(path)
    started, release = threading.Event(), threading.Event()
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        store.append_event(
            "alice",
            "user_input",
            {"content": "first needle"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        live = await store.run(live_memory_for_store, store)
        await live.search(
            "needle",
            names=["alice"],
            config=None,
            create_embedder=lambda _: None,
            mode="fts",
        )
        store.append_event(
            "alice",
            "user_input",
            {"content": "second needle"},
            turn_index=2,
            branch_id=1,
            parent_branch_path=[[1, 1]],
        )

        def interrupted_insert(*args, **kwargs):
            started.set()
            if not release.wait(10):
                raise TimeoutError("worker blocked")
            raise OSError("injected FTS worker failure")

        with monkeypatch.context() as patch:
            patch.setattr(live._memory._fts, "insert", interrupted_insert)
            task = asyncio.create_task(
                live.search(
                    "needle",
                    names=["alice"],
                    config=None,
                    create_embedder=lambda _: None,
                    mode="fts",
                )
            )
            assert await asyncio.to_thread(started.wait, 8)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            # Store's accepted mutation stays in FIFO order even after
            # the asyncio caller stops waiting for its result.
            await store.run(lambda: None)
        assert live._memory._fts_build_lock is None
        assert live._memory._manifest()["fts"] != "ready"
        with closing(SessionMemory(str(path), index_all_branches=True)) as peer:
            peer._index_keywords("alice", store.get_memory_events("alice"))
            assert len(peer.search("needle", mode="fts")) == 2
    finally:
        release.set()
        store.close(update_status=False)


def test_release_error_does_not_leave_a_phantom_build_owner(tmp_path, monkeypatch):
    path = tmp_path / "unlock.kohakutr"
    with closing(SessionMemory(str(path), index_all_branches=True)) as memory:
        memory._claim_build("fts")
        lock = memory._fts_build_lock
        release = lock.release

        def raise_after_unlock():
            release()
            raise OSError("injected unlock failure")

        monkeypatch.setattr(lock, "release", raise_after_unlock)
        with pytest.raises(OSError, match="unlock failure"):
            memory._abort_builds()
        assert memory._fts_build_lock is None
        with closing(SessionMemory(str(path), index_all_branches=True)) as peer:
            peer._claim_build("fts")
            assert peer._fts_build_lock is not None


@pytest.mark.parametrize("second_has_events", [False, True])
def test_nonforced_reconciliation_enrolls_complete_source_roster(
    tmp_path, second_has_events
):
    """No partial FTS/vector corpus may be published after the first Agent."""
    path = tmp_path / "nonforce-roster.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        store.append_event(
            "alice",
            "user_input",
            {"content": "alice needle"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        if second_has_events:
            store.append_event(
                "bob",
                "user_input",
                {"content": "bob needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as owner:
            reconciler = SessionIndexReconciler(store, owner)
            source = reconciler.capture(["alice", "bob"], all_agents=True)
            assert source.agents == ("alice", "bob")

            def interrupt_after_first(name, count, indexed):
                assert name == "alice"
                manifest = owner._manifest()
                assert manifest["agents"] == ["alice", "bob"]
                assert manifest["fts"] == manifest["vector"] == "building"
                assert manifest["fts_pending"] == ["bob"]
                assert manifest["vector_pending"] == ["bob"]
                assert owner._fts_build_lock is not None
                assert owner._vector_build_lock is not None
                with closing(
                    SessionMemory(str(path), _Embedder(), index_all_branches=True)
                ) as reader:
                    with pytest.raises(ValueError, match="invalid|building"):
                        reader.search("needle", mode="fts")
                    with pytest.raises(ValueError, match="stale|invalid"):
                        reader.search("needle", mode="semantic")
                raise OSError("injected progress stop")

            with pytest.raises(OSError, match="progress stop"):
                reconciler.build(source, force=False, progress=interrupt_after_first)
            assert owner._fts_build_lock is None
            assert owner._vector_build_lock is None
            assert owner._manifest()["fts"] != "ready"
            assert owner._manifest()["vector"] != "ready"

        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as peer:
            assert SessionIndexReconciler(store, peer).build(source) == {
                "alice": 1,
                "bob": int(second_has_events),
            }
            assert peer._manifest()["fts"] == peer._manifest()["vector"] == "ready"
            assert peer._manifest()["agents"] == ["alice", "bob"]
            hits = peer.search("needle", mode="semantic")
            assert {hit.agent for hit in hits} == (
                {"alice", "bob"} if second_has_events else {"alice"}
            )
    finally:
        store.close(update_status=False)


def test_vector_abort_with_contended_mutation_lock_releases_owner(tmp_path):
    """A competing short FTS mutation cannot strand the vector build lock."""
    from kohakuterrarium.utils.file.lock import FileLock

    path = tmp_path / "vector-abort-contention.kohakutr"
    rows = _rows("alice")
    with closing(
        SessionMemory(str(path), _Embedder(), index_all_branches=True)
    ) as owner:
        owner._index_keywords("alice", rows)
        start, blocks = owner._vector_batch("alice", rows)
        vectors = owner._embedder.encode([b.content for b in blocks])
        assert owner._vector_build_lock is not None
        assert owner._manifest()["vector"] == "building"

        blocker = FileLock(f"{path}.memory-index.lock")
        blocker.acquire()
        try:
            with pytest.raises(RuntimeError, match="being modified"):
                owner._commit_vectors("alice", start, len(rows), blocks, vectors)
            # This cleanup used to raise the same lock contention and leave
            # the long build lock held in this still-live SessionMemory.
            owner._abort_vector_build()
            assert owner._vector_build_lock is None
            assert owner._manifest()["vector"] != "ready"
        finally:
            blocker.release()

        with pytest.raises(RuntimeError, match="ownership|changed|retry"):
            owner._commit_vectors("alice", start, len(rows), blocks, vectors)
        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as peer:
            peer.index_events("alice", rows)
            assert peer._manifest()["vector"] == "ready"
            assert len(peer.search("needle", mode="semantic")) == 1


def test_cross_index_abort_under_mutation_contention_frees_both_locks(tmp_path):
    """Shared abnormal-exit cleanup must not depend on reacquiring short lock."""
    from kohakuterrarium.utils.file.lock import FileLock

    path = tmp_path / "both-abort-contention.kohakutr"
    with closing(
        SessionMemory(str(path), _Embedder(), index_all_branches=True)
    ) as owner:
        owner.force_rebuild(["alice", "bob"])
        owner.index_events("alice", _rows("alice"))
        assert owner._fts_build_lock is not None
        assert owner._vector_build_lock is not None
        blocker = FileLock(f"{path}.memory-index.lock")
        blocker.acquire()
        try:
            owner._abort_builds()
            assert owner._fts_build_lock is None
            assert owner._vector_build_lock is None
            assert owner._manifest()["fts"] == "building"
            assert owner._manifest()["vector"] == "building"
        finally:
            blocker.release()
        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as peer:
            peer.index_events("alice", _rows("alice"))
            peer.index_events("bob", _rows("bob"))
            assert peer._manifest()["fts"] == peer._manifest()["vector"] == "ready"


def test_nonforced_reconciliation_from_ready_retains_all_participants(tmp_path):
    """A previously READY corpus is also staged as one indivisible build."""
    path = tmp_path / "ready-roster.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        for name in ("alice", "bob"):
            store.append_event(
                name,
                "user_input",
                {"content": f"{name} initial needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as owner:
            reconciler = SessionIndexReconciler(store, owner)
            initial = reconciler.capture(["alice", "bob"], all_agents=True)
            reconciler.build(initial)
            manifest = owner._manifest()
            assert manifest["fts"] == manifest["vector"] == "ready"

            store.append_event(
                "bob",
                "text",
                {"content": "bob later needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
            changed = reconciler.capture(["alice", "bob"], all_agents=True)
            statuses = []

            def monitor(name, count, indexed):
                current = owner._manifest()
                statuses.append((name, current["fts"], current["vector"]))
                if name == "alice":
                    assert current["fts_pending"] == ["bob"]
                    assert current["vector_pending"] == ["bob"]
                    assert owner._fts_build_lock is not None
                    assert owner._vector_build_lock is not None

            reconciler.build(changed, progress=monitor)
            assert statuses == [
                ("alice", "building", "building"),
                ("bob", "ready", "ready"),
            ]
            assert owner._fts_build_lock is owner._vector_build_lock is None
            assert {hit.content for hit in owner.search("needle", mode="semantic")} == {
                "alice initial needle",
                "bob initial needle",
                "bob later needle",
            }
    finally:
        store.close(update_status=False)


def test_keyword_refresh_roster_failure_stays_unreadable(tmp_path, monkeypatch):
    """Live/Reader keyword reconciliation cannot publish Alice without Bob."""
    path = tmp_path / "keyword-roster.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        for name in ("alice", "bob"):
            store.append_event(
                name,
                "user_input",
                {"content": f"{name} needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        with closing(SessionMemory(str(path), index_all_branches=True)) as owner:
            reconcile = SessionIndexReconciler(store, owner)
            source = reconcile.capture(["alice", "bob"], all_agents=True)
            real_index = owner._index_keywords

            def fail_on_bob(name, rows):
                if name == "bob":
                    manifest = owner._manifest()
                    assert manifest["fts"] == "building"
                    assert manifest["agents"] == ["alice", "bob"]
                    assert manifest["fts_pending"] == ["bob"]
                    assert owner._fts_build_lock is not None
                    raise OSError("bob keyword refresh failed")
                return real_index(name, rows)

            with monkeypatch.context() as patch:
                patch.setattr(owner, "_index_keywords", fail_on_bob)
                with pytest.raises(OSError, match="bob keyword refresh failed"):
                    reconcile.refresh_keywords(source)
            assert owner._fts_build_lock is None
            assert owner._manifest()["fts"] != "ready"
            with pytest.raises(ValueError, match="invalid|building"):
                owner.search("needle", mode="fts")

        with closing(SessionMemory(str(path), index_all_branches=True)) as peer:
            assert SessionIndexReconciler(store, peer).refresh_keywords(source) == {
                "alice": 1,
                "bob": 1,
            }
            assert peer._manifest()["fts"] == "ready"
            assert {hit.agent for hit in peer.search("needle", mode="fts")} == {
                "alice",
                "bob",
            }
    finally:
        store.close(update_status=False)


def test_unchanged_keyword_refresh_does_not_restart_published_build(tmp_path):
    """Fully current source must not churn a READY index on every search."""
    path = tmp_path / "keyword-noop.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        for name in ("alice", "bob"):
            store.append_event(
                name,
                "user_input",
                {"content": f"{name} needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as owner:
            reconcile = SessionIndexReconciler(store, owner)
            source = reconcile.capture(["alice", "bob"], all_agents=True)
            reconcile.build(source)
            baseline = dict(owner._manifest())
            assert baseline["fts"] == baseline["vector"] == "ready"
            assert reconcile.refresh_keywords(source) == {"alice": 0, "bob": 0}
            assert owner._manifest() == baseline
            assert owner._fts_build_lock is owner._vector_build_lock is None
    finally:
        store.close(update_status=False)


@pytest.mark.asyncio
async def test_live_semantic_abort_recovers_after_short_lock_conflict(tmp_path):
    """The real Store Affinity cleanup path must release long ownership."""
    from kohakuterrarium.utils.file.lock import FileLock

    path = tmp_path / "live-vector-contention.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "agent", "", str(tmp_path), ["alice"])
        store.append_event(
            "alice",
            "user_input",
            {"content": "alice needle"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        live = await store.run(live_memory_for_store, store)
        rows = await store.run(store.get_memory_events, "alice")
        await store.run(live._memory._index_keywords, "alice", rows)
        await store.run(live._memory._set_embedder, _Embedder())
        batch, blocks = await store.run(live._memory._vector_batch, "alice", rows)
        vectors = _Embedder().encode([block.content for block in blocks])
        assert live._memory._vector_build_lock is not None

        blocker = FileLock(f"{path}.memory-index.lock")
        blocker.acquire()
        try:
            with pytest.raises(RuntimeError, match="being modified"):
                async with live._semantic_operation():
                    await store.run(
                        live._memory._commit_vectors,
                        "alice",
                        batch,
                        len(rows),
                        blocks,
                        vectors,
                    )
            assert live._memory._vector_build_lock is None
            assert live._memory._manifest()["vector"] == "building"
        finally:
            blocker.release()

        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as peer:
            peer.index_events("alice", rows)
            assert peer._manifest()["vector"] == "ready"
            assert len(peer.search("needle", mode="semantic")) == 1
    finally:
        store.close(update_status=False)


def test_new_full_build_does_not_adopt_stale_vector_building_rows(tmp_path):
    """An independent builder must clear orphan vectors from a prior owner."""
    path = tmp_path / "stale-building-vector.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        store.append_event(
            "alice",
            "user_input",
            {"content": "first needle"},
            turn_index=1,
            branch_id=1,
            parent_branch_path=[],
        )
        rows = store.get_memory_events("alice")
        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as old:
            old.index_events("alice", rows)
            store.append_event(
                "alice",
                "text",
                {"content": "second needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
            newer = store.get_memory_events("alice")
            old._index_keywords("alice", newer)
            old._vector_batch("alice", newer)
            assert old._manifest()["vector"] == "building"
            assert old._manifest()["vector_needs_clear"] is False
            old._vec.insert(
                np.ones(3, dtype=np.float32),
                {"agent": "orphan", "content": "ghost needle", "block_id": "orphan"},
            )
        # The previous process/owner is gone but its partial native rows
        # and vector=building manifest remain.
        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as peer:
            source = SessionIndexReconciler(store, peer).capture(
                ["alice", "bob"], all_agents=True
            )
            SessionIndexReconciler(store, peer).build(source, force=False)
            assert peer._manifest()["fts"] == peer._manifest()["vector"] == "ready"
            assert peer.get_stats()["vec_blocks"] == 2
            assert {hit.content for hit in peer.search("needle", mode="semantic")} == {
                "first needle",
                "second needle",
            }
    finally:
        store.close(update_status=False)


def test_complete_noop_reconciler_build_preserves_published_readability(tmp_path):
    """A complete, unchanged source is a read, not another logical build."""
    path = tmp_path / "noop-batch.kohakutr"
    store = SessionStore(path)
    try:
        store.init_meta("s", "terrarium", "", str(tmp_path), ["alice", "bob"])
        for name in ("alice", "bob"):
            store.append_event(
                name,
                "user_input",
                {"content": f"{name} needle"},
                turn_index=1,
                branch_id=1,
                parent_branch_path=[],
            )
        with closing(
            SessionMemory(str(path), _Embedder(), index_all_branches=True)
        ) as owner:
            reconcile = SessionIndexReconciler(store, owner)
            source = reconcile.capture(["alice", "bob"], all_agents=True)
            reconcile.build(source)
            baseline = dict(owner._manifest())
            writes = []
            original = owner._save_manifest

            def track(manifest):
                writes.append((manifest["fts"], manifest["vector"]))
                return original(manifest)

            owner._save_manifest = track

            def check_midway(name, count, indexed):
                assert indexed == 0
                assert owner._manifest() == baseline
                assert owner._fts_build_lock is owner._vector_build_lock is None
                assert {hit.agent for hit in owner.search("needle", mode="fts")} == {
                    "alice",
                    "bob",
                }

            assert reconcile.build(source, progress=check_midway) == {
                "alice": 0,
                "bob": 0,
            }
            assert writes == []
            assert owner._manifest() == baseline
    finally:
        store.close(update_status=False)
