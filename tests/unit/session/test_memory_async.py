"""Live memory isolation, cancellation, and index progress regressions."""

import asyncio
import threading
from concurrent.futures import Future
from types import SimpleNamespace

import numpy as np
import pytest

from kohakuterrarium.session import memory_async
from kohakuterrarium.session.embedding import BaseEmbedder, NullEmbedder
from kohakuterrarium.session.memory_async import (
    _compute,
    _EmbeddingWorker,
    live_memory_for_store,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions import memory_search


class Embedder(BaseEmbedder):
    dimensions = 2

    def encode(self, texts):
        return np.ones((len(texts), 2), dtype=np.float32)


@pytest.fixture
def store(tmp_path):
    store = SessionStore(tmp_path / "memory.kohakutr")
    store.init_meta("s", "agent", "", str(tmp_path), ["alice", "bob"])
    for name in ("alice", "bob"):
        store.append_event(name, "user_input", {"content": f"{name} needle"})
    yield store
    store.close(update_status=False)


async def search(memory, factory, **kwargs):
    return await memory.search(
        "needle", names=["alice", "bob"], config={}, create_embedder=factory, **kwargs
    )


async def test_concurrent_searches_share_model_and_do_not_duplicate_vectors(store):
    memory = await store.run(live_memory_for_store, store)
    calls = []

    def create(config):
        calls.append(config)
        return Embedder()

    results = await asyncio.gather(*(search(memory, create) for _ in range(3)))
    assert len(calls) == 1
    for hits, warning in results:
        assert warning is None
        assert {hit.content for hit in hits} == {"alice needle", "bob needle"}
    stats = await store.run(memory._memory.get_stats)
    assert stats["vec_blocks"] == 2
    await store.run(store.append_event, "bob", "user_input", {"content": "new needle"})
    await search(memory, create, mode="fts")
    hits, _ = await search(memory, create, mode="semantic", agent="bob")
    assert {hit.content for hit in hits} == {"bob needle", "new needle"}
    assert len(calls) == 1
    assert (await store.run(memory._memory.get_stats))["vec_blocks"] == 3


async def test_timeout_falls_back_without_spawning_more_model_calls(store, monkeypatch):
    memory = await store.run(live_memory_for_store, store)
    monkeypatch.setattr(memory_async, "EMBEDDING_TIMEOUT", 0.05)
    release = threading.Event()
    calls = []

    def blocked(config):
        calls.append(config)
        assert release.wait(5)
        return Embedder()

    try:
        for _ in range(2):
            hits, warning = await search(memory, blocked)
            assert {hit.content for hit in hits} == {"alice needle", "bob needle"}
            assert "keyword search" in warning
        assert len(calls) == 1
        hits, warning = await search(memory, blocked, mode="fts")
        assert len(hits) == 2 and warning is None
        with pytest.raises(TimeoutError, match="still running"):
            await search(memory, blocked, mode="semantic")
        await asyncio.wait_for(asyncio.to_thread(store.close, update_status=False), 1)
    finally:
        release.set()
        await asyncio.to_thread(memory._worker._pending.result, 2)


async def test_cancelled_search_never_installs_late_model(store):
    memory = await store.run(live_memory_for_store, store)
    entered, release = threading.Event(), threading.Event()

    def blocked(_):
        entered.set()
        assert release.wait(5)
        return Embedder()

    task = asyncio.create_task(search(memory, blocked))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(asyncio.to_thread(store.close, update_status=False), 1)
        release.set()
        await asyncio.to_thread(memory._worker._pending.result, 2)
        assert memory._embedder is None
        assert memory._memory._vec is None
        with pytest.raises(RuntimeError, match="closed"):
            await memory._worker.run(Embedder)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("provider", ["none", "error"])
async def test_missing_model_modes_preserve_strict_semantic_contract(store, provider):
    memory = await store.run(live_memory_for_store, store)

    def create(_):
        if provider == "error":
            raise ValueError("model unavailable")
        return NullEmbedder()

    hits, warning = await search(memory, create)
    assert len(hits) == 2
    assert bool(warning) == (provider == "error")
    with pytest.raises(ValueError):
        await search(memory, create, mode="semantic")
    hits, warning = await search(
        memory, create, mode="semantic", semantic_fallback=True
    )
    assert len(hits) == 2 and warning
    with pytest.raises(ValueError, match="Unknown search mode"):
        await search(memory, create, mode="invalid")


async def test_studio_live_search_uses_same_isolation_and_cache(store, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    agent = SimpleNamespace(session_store=store, config=None)
    engine = SimpleNamespace(list_creatures=lambda: [SimpleNamespace(agent=agent)])

    def blocked(_):
        entered.set()
        assert release.wait(5)
        return Embedder()

    monkeypatch.setattr(memory_search, "create_embedder", blocked)
    task = asyncio.create_task(
        memory_search.search_session_memory(store.path, q="needle", engine=engine)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        result = await asyncio.wait_for(
            memory_search.search_session_memory(
                store.path, q="needle", mode="fts", engine=engine
            ),
            1,
        )
        assert {hit["content"] for hit in result["results"]} == {
            "alice needle",
            "bob needle",
        }
        release.set()
        result = await task
        assert result["count"] == 2
        assert (await store.run(store._live_memory._memory.get_stats))[
            "vec_blocks"
        ] == 2
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_worker_recovers_after_error_and_rejects_after_close():
    worker = _EmbeddingWorker()

    def fail():
        raise ValueError("external model error")

    with pytest.raises(ValueError, match="external model error"):
        await worker.run(fail)
    assert await worker.run(lambda: 42) == 42
    worker.close()
    with pytest.raises(RuntimeError, match="closed"):
        await worker.run(lambda: 43)
    future = Future()
    future.cancel()
    _compute(future, fail, ())
    assert future.cancelled()
