"""Live memory searches with embedding work isolated from session storage."""

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Callable

from kohakuterrarium.session.embedding import BaseEmbedder, NullEmbedder
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

EMBEDDING_TIMEOUT = 30.0


def _compute(future: Future, fn: Callable, args: tuple) -> None:
    if not future.set_running_or_notify_cancel():
        return
    try:
        future.set_result(fn(*args))
    except BaseException as exc:
        future.set_exception(exc)


class _EmbeddingWorker:
    """Allow at most one outstanding model call, without owning database handles."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: Future | None = None
        self._closed = False

    async def run(self, fn: Callable, *args: Any):
        with self._lock:
            if self._closed:
                raise RuntimeError("Session memory is closed")
            if self._pending is not None and not self._pending.done():
                raise TimeoutError("A previous embedding operation is still running")
            future = self._pending = Future()
            threading.Thread(
                target=_compute,
                args=(future, fn, args),
                name="kt-memory-embedding",
                daemon=True,
            ).start()
        return await asyncio.wait_for(asyncio.wrap_future(future), EMBEDDING_TIMEOUT)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._pending is not None:
                self._pending.cancel()


class LiveSessionMemory:
    """Serialize index mutations on a store while awaiting pure model work separately."""

    def __init__(self, store):
        self._store = store
        self._memory = SessionMemory(store.path)
        self._worker = _EmbeddingWorker()
        self._semantic_lock = asyncio.Lock()
        self._embedder: BaseEmbedder | None = None
        self._closed = False
        store.register_companion_closer(self.close)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._worker.close()
            self._memory.close()

    def _index_keywords(self, names: list[str]) -> None:
        for name in names:
            self._memory._index_keywords(name, self._store.get_events(name))

    def _vector_batch(self, name: str):
        events = self._store.get_events(name)
        start, blocks = self._memory._vector_batch(name, events)
        return start, len(events), blocks

    async def search(
        self,
        query: str,
        *,
        names: list[str],
        config: dict | None,
        create_embedder: Callable,
        mode: str = "auto",
        k: int = 10,
        agent: str | None = None,
        semantic_fallback: bool = False,
    ):
        if mode not in ("auto", "fts", "hybrid", "semantic"):
            raise ValueError(f"Unknown search mode {mode!r}")
        await self._store.run(self._index_keywords, names)
        warning = None
        if mode != "fts":
            try:
                async with self._semantic_lock:
                    if self._embedder is None:
                        embedder = await self._worker.run(create_embedder, config)
                        await self._store.run(self._memory._set_embedder, embedder)
                        self._embedder = embedder
                    if isinstance(self._embedder, NullEmbedder):
                        if mode != "semantic":
                            results = await self._store.run(
                                self._memory._search_fts, query, k, agent
                            )
                            return results, None
                        raise ValueError("semantic search needs an embedding model")
                    for name in names:
                        start, count, blocks = await self._store.run(
                            self._vector_batch, name
                        )
                        vectors = (
                            await self._worker.run(
                                self._embedder.encode, [b.content for b in blocks]
                            )
                            if blocks
                            else []
                        )
                        await self._store.run(
                            self._memory._commit_vectors,
                            name,
                            start,
                            count,
                            blocks,
                            vectors,
                        )
                    vector = await self._worker.run(self._embedder.encode_one, query)
                    results = await self._store.run(
                        self._memory._search_vector,
                        vector,
                        k if mode == "semantic" else k * 2,
                        agent,
                    )
                    if mode == "semantic":
                        return results, None
                    keywords = await self._store.run(
                        self._memory._search_fts, query, k * 2, agent
                    )
                    return self._memory._fuse_results(keywords, results, k), None
            except Exception as exc:
                if self._closed or (mode == "semantic" and not semantic_fallback):
                    raise
                warning = f"Embedding unavailable; using keyword search: {str(exc) or type(exc).__name__}"
                logger.warning(
                    "Memory search using FTS fallback",
                    error=str(exc) or type(exc).__name__,
                )
        results = await self._store.run(self._memory._search_fts, query, k, agent)
        return results, warning


def live_memory_for_store(store) -> LiveSessionMemory:
    """Get or create the store-owned index on its affinity thread."""
    memory = getattr(store, "_live_memory", None)
    if memory is None:
        memory = store._live_memory = LiveSessionMemory(store)
    return memory
