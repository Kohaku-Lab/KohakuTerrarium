"""Real-store regressions for owned SessionStore / SessionMemory cleanup.

Pins that ``_search_session_sync`` closes resources it opened even when
indexing, encoding, search, or construction fails; borrowed live stores
must never be closed. Closures are verified by store ``_closed`` /
native vault handles, not by waiting for weakref collection.
"""

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from kohakuterrarium.errors import SessionError
from kohakuterrarium.session.memory.embedding import NullEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.occupancy.registry import live_store_for
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.vault.handles import is_disposed
from kohakuterrarium.studio.sessions.memory import search as mem_mod

_ORIGINAL_MEMORY_CLOSE = SessionMemory.close


def _seed_session(path: Path) -> None:
    store = SessionStore(str(path))
    try:
        store.init_meta("sess", "agent", "/p", "/w", ["alice"])
        store.append_event("alice", "user_input", {"content": "hello world"})
        store.append_event("alice", "text_chunk", {"content": "general reply"})
        store.flush()
    finally:
        store.close(update_status=False)


def _memory_closed(memory: SessionMemory) -> bool:
    """Native vault handles are released by ``SessionMemory.close``."""
    handles = (getattr(memory, name, None) for name in ("_fts", "_state", "_vec"))
    return all(h is None or is_disposed(h) for h in handles)


def _store_tables_closed(store: SessionStore) -> bool:
    """Native KVault handles are released by ``SessionStore.close``."""
    names = ("events", "meta", "state", "channels", "fts")
    handles = (getattr(store, name, None) for name in names)
    return all(h is None or is_disposed(h) for h in handles)


def _assert_owned_closed(
    store: SessionStore, memory: SessionMemory | None = None
) -> None:
    assert store._closed
    assert _store_tables_closed(store)
    if memory is not None:
        assert _memory_closed(memory)
    assert live_store_for(store._path) is None


class _CaptureMemory:
    """Wrap real SessionMemory so tests can inspect the instance after search."""

    def __init__(self) -> None:
        self.instances: list[SessionMemory] = []
        self.stores: list[SessionStore] = []

    def __call__(self, *args: Any, **kwargs: Any) -> SessionMemory:
        memory = SessionMemory(*args, **kwargs)
        self.instances.append(memory)
        store = live_store_for(memory._path)
        assert store is not None, "owned SessionStore was not registered"
        self.stores.append(store)
        return memory


class _FailingEmbedder:
    dimensions = 4

    def encode(self, texts):
        raise RuntimeError("synthetic encode failure")

    def encode_one(self, text):
        return self.encode([text])[0]


class TestOwnedStoreCleanupOnFailure:
    def test_index_failure_closes_owned_store_and_memory(self, tmp_path, monkeypatch):
        path = tmp_path / "index-fail.kohakutr"
        _seed_session(path)
        capture = _CaptureMemory()
        monkeypatch.setattr(mem_mod, "SessionMemory", capture)

        def boom(self, agent, events):
            raise RuntimeError("synthetic index failure")

        monkeypatch.setattr(SessionMemory, "index_events", boom)

        with pytest.raises(SessionError, match="synthetic index failure"):
            mem_mod._search_session_sync(
                path,
                q="hello",
                mode="fts",
                k=5,
                agent=None,
                live_agent=None,
                live_store=None,
            )

        _assert_owned_closed(capture.stores[-1], capture.instances[-1])

    def test_encode_failure_closes_owned_store_and_memory(self, tmp_path, monkeypatch):
        path = tmp_path / "encode-fail.kohakutr"
        _seed_session(path)
        capture = _CaptureMemory()
        monkeypatch.setattr(mem_mod, "SessionMemory", capture)
        monkeypatch.setattr(mem_mod, "create_embedder", lambda cfg: _FailingEmbedder())

        with pytest.raises(SessionError, match="synthetic encode failure"):
            mem_mod._search_session_sync(
                path,
                q="hello",
                mode="auto",
                k=5,
                agent=None,
                live_agent=None,
                live_store=None,
            )

        _assert_owned_closed(capture.stores[-1], capture.instances[-1])

    def test_search_failure_closes_owned_store_and_memory(self, tmp_path, monkeypatch):
        path = tmp_path / "search-fail.kohakutr"
        _seed_session(path)
        capture = _CaptureMemory()
        monkeypatch.setattr(mem_mod, "SessionMemory", capture)

        def boom(self, query, mode, k, agent):
            raise RuntimeError("synthetic search failure")

        monkeypatch.setattr(SessionMemory, "search", boom)

        with pytest.raises(SessionError, match="synthetic search failure"):
            mem_mod._search_session_sync(
                path,
                q="hello",
                mode="fts",
                k=5,
                agent=None,
                live_agent=None,
                live_store=None,
            )

        _assert_owned_closed(capture.stores[-1], capture.instances[-1])

    def test_memory_constructor_failure_closes_owned_store(self, tmp_path, monkeypatch):
        path = tmp_path / "ctor-fail.kohakutr"
        _seed_session(path)
        holders: list[SessionStore] = []

        def factory(*args: Any, **kwargs: Any) -> SessionMemory:
            store = live_store_for(path)
            assert store is not None, "owned SessionStore was not registered"
            holders.append(store)
            raise RuntimeError("synthetic memory constructor failure")

        monkeypatch.setattr(mem_mod, "SessionMemory", factory)

        with pytest.raises(SessionError, match="synthetic memory constructor failure"):
            mem_mod._search_session_sync(
                path,
                q="hello",
                mode="fts",
                k=5,
                agent=None,
                live_agent=None,
                live_store=None,
            )

        _assert_owned_closed(holders[-1])

    def test_memory_close_throw_still_closes_owned_store(self, tmp_path, monkeypatch):
        path = tmp_path / "close-throw.kohakutr"
        _seed_session(path)
        capture = _CaptureMemory()

        def exploding_close(self) -> None:
            _ORIGINAL_MEMORY_CLOSE(self)
            raise RuntimeError("synthetic memory close failure")

        monkeypatch.setattr(mem_mod, "SessionMemory", capture)
        monkeypatch.setattr(SessionMemory, "close", exploding_close)

        with pytest.raises(SessionError, match="synthetic memory close failure"):
            mem_mod._search_session_sync(
                path,
                q="hello",
                mode="fts",
                k=5,
                agent=None,
                live_agent=None,
                live_store=None,
            )

        _assert_owned_closed(capture.stores[-1], capture.instances[-1])

    def test_normal_completion_closes_owned_resources(self, tmp_path, monkeypatch):
        path = tmp_path / "ok.kohakutr"
        _seed_session(path)
        capture = _CaptureMemory()
        monkeypatch.setattr(mem_mod, "SessionMemory", capture)

        out = mem_mod._search_session_sync(
            path,
            q="hello",
            mode="fts",
            k=5,
            agent=None,
            live_agent=None,
            live_store=None,
        )
        assert out["count"] >= 1
        _assert_owned_closed(capture.stores[-1], capture.instances[-1])


class TestBorrowedLiveStoreMustNeverClose:
    def test_index_failure_leaves_borrowed_store_usable(self, tmp_path, monkeypatch):
        path = tmp_path / "live-index-fail.kohakutr"
        live_store = SessionStore(str(path))
        try:
            live_store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            live_store.append_event("alice", "user_input", {"content": "hello world"})
            live_store.flush()
            capture = _CaptureMemory()
            monkeypatch.setattr(mem_mod, "SessionMemory", capture)

            def boom(self, agent, events):
                raise RuntimeError("synthetic index failure")

            monkeypatch.setattr(SessionMemory, "index_events", boom)

            with pytest.raises(SessionError, match="synthetic index failure"):
                mem_mod._search_session_sync(
                    path,
                    q="hello",
                    mode="fts",
                    k=5,
                    agent=None,
                    live_agent=None,
                    live_store=live_store,
                )

            assert not live_store._closed
            assert not _store_tables_closed(live_store)
            assert live_store_for(path) is live_store
            assert _memory_closed(capture.instances[-1])
            events = live_store.get_events("alice")
            assert events
        finally:
            live_store.close(update_status=False)

    def test_success_leaves_borrowed_store_open(self, tmp_path, monkeypatch):
        path = tmp_path / "live-ok.kohakutr"
        live_store = SessionStore(str(path))
        try:
            live_store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            live_store.append_event("alice", "user_input", {"content": "hello world"})
            live_store.flush()
            capture = _CaptureMemory()
            monkeypatch.setattr(mem_mod, "SessionMemory", capture)

            out = mem_mod._search_session_sync(
                path,
                q="hello",
                mode="fts",
                k=5,
                agent=None,
                live_agent=None,
                live_store=live_store,
            )
            assert out["count"] >= 1
            assert not live_store._closed
            assert live_store_for(path) is live_store
            assert _memory_closed(capture.instances[-1])
            events = live_store.get_events("alice")
            assert events
        finally:
            live_store.close(update_status=False)


class TestCancelWaiterWorkerStillCloses:
    async def test_cancel_waiter_worker_continues_then_closes(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "cancel.kohakutr"
        _seed_session(path)

        entered = threading.Event()
        release = threading.Event()
        done = threading.Event()
        holders: list[SessionStore] = []
        memories: list[SessionMemory] = []
        report: dict[str, Any] = {}
        original = mem_mod._search_session_sync

        def factory(config):
            holders.append(live_store_for(path))
            entered.set()
            if not release.wait(15):
                raise RuntimeError("probe release deadline")
            return NullEmbedder()

        def capture_memory(*args, **kwargs):
            memory = SessionMemory(*args, **kwargs)
            memories.append(memory)
            return memory

        def worker(*args, **kwargs):
            try:
                result = original(*args, **kwargs)
                report["result_count"] = result["count"]
                return result
            except Exception as exc:
                report["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                report["owner_closed_at_worker_exit"] = (
                    holders[0]._closed if holders else None
                )
                done.set()

        monkeypatch.setattr(mem_mod, "create_embedder", factory)
        monkeypatch.setattr(mem_mod, "_search_session_sync", worker)
        monkeypatch.setattr(mem_mod, "SessionMemory", capture_memory)

        task = asyncio.create_task(
            mem_mod.search_session_memory(
                path, q="hello", mode="auto", k=5, engine=None
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            assert holders[0] is not None
            report["owner_open_before_cancel"] = not holders[0]._closed
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            report["waiter_cancelled"] = True
            report["worker_running_after_cancel"] = not done.is_set()
            report["owner_open_after_cancel"] = not holders[0]._closed
            release.set()
            assert await asyncio.to_thread(done.wait, 15)
            assert report["worker_running_after_cancel"]
            assert report["owner_open_after_cancel"]
            assert "error" not in report, report
            assert report["result_count"] >= 1
            assert report["owner_closed_at_worker_exit"]
            _assert_owned_closed(holders[0], memories[-1])
        finally:
            release.set()
            await asyncio.to_thread(done.wait, 15)
            for memory in memories:
                if not _memory_closed(memory):
                    memory.close()
            for store in holders:
                if store is not None and not store._closed:
                    store.close(update_status=False)

    async def test_cancel_waiter_encode_failure_still_closes(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "cancel-fail.kohakutr"
        _seed_session(path)

        entered = threading.Event()
        release = threading.Event()
        done = threading.Event()
        holders: list[SessionStore] = []
        memories: list[SessionMemory] = []
        report: dict[str, Any] = {}
        original = mem_mod._search_session_sync

        def factory(config):
            holders.append(live_store_for(path))
            entered.set()
            if not release.wait(15):
                raise RuntimeError("probe release deadline")
            return _FailingEmbedder()

        def capture_memory(*args, **kwargs):
            memory = SessionMemory(*args, **kwargs)
            memories.append(memory)
            return memory

        def worker(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except Exception as exc:
                report["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                report["owner_closed_at_worker_exit"] = (
                    holders[0]._closed if holders else None
                )
                done.set()

        monkeypatch.setattr(mem_mod, "create_embedder", factory)
        monkeypatch.setattr(mem_mod, "_search_session_sync", worker)
        monkeypatch.setattr(mem_mod, "SessionMemory", capture_memory)

        task = asyncio.create_task(
            mem_mod.search_session_memory(
                path, q="hello", mode="auto", k=5, engine=None
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not done.is_set()
            assert not holders[0]._closed
            release.set()
            assert await asyncio.to_thread(done.wait, 15)
            assert "synthetic encode failure" in report["error"]
            assert report["error"].startswith("SessionError")
            assert report["owner_closed_at_worker_exit"]
            _assert_owned_closed(holders[0], memories[-1])
        finally:
            release.set()
            await asyncio.to_thread(done.wait, 15)
            for memory in memories:
                if not _memory_closed(memory):
                    memory.close()
            for store in holders:
                if store is not None and not store._closed:
                    store.close(update_status=False)
