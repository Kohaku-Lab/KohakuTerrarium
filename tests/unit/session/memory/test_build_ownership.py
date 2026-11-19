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
