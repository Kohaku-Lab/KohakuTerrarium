"""Live memory searches with embedding work isolated from session storage."""

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Callable

from kohakuterrarium.session.memory.embedding import BaseEmbedder, NullEmbedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.context import (
    MemoryContextError,
    MemoryContextNavigator,
)
from kohakuterrarium.session.memory.visibility import (
    AgentBranchVisibility,
    MemoryCandidateLimitError,
    MemoryLineageUnavailableError,
    requested_store_view,
    resolve_search_view,
)
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
        self._memory = SessionMemory(store.path, index_all_branches=True)
        self._context = MemoryContextNavigator(store)
        self._worker = _EmbeddingWorker()
        self._semantic_lock = asyncio.Lock()
        self._embedder: BaseEmbedder | None = None
        self._closed = False
        store.register_companion_closer(self.close)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._worker.close()
            self._context.close()
            self._memory.close()

    def _index_keywords(
        self,
        names: list[str],
        overrides: dict[str, dict[int, int]] | None = None,
    ) -> dict[str, dict[int, int]]:
        """Index immutable facts and resolve each agent's selected projection."""
        views: dict[str, dict[int, int]] = {}
        manifest = self._memory._manifest()
        migration = (
            manifest is None
            or manifest.get("format") != self._memory._index_format
            or manifest.get("fts") != "ready"
        )
        # A session-wide migration cannot be published with only the active
        # agent upgraded. Existing agent indexes are rebuilt together.
        refresh_names = (
            list(
                dict.fromkeys(
                    [
                        *self._store.load_meta().get("agents", []),
                        *sorted(self._memory._known_agents()),
                        *names,
                    ]
                )
            )
            if migration
            else names
        )
        for name in refresh_names:
            events = self._store.get_memory_events(name)
            self._memory._index_keywords(name, events)
            if name in names:
                selected = (
                    overrides[name]
                    if overrides is not None and name in overrides
                    else requested_store_view(self._store, name)
                )
                views[name] = resolve_search_view(events, selected)
        return views

    def _vector_batch(self, name: str):
        events = self._store.get_memory_events(name)
        start, blocks = self._memory._vector_batch(name, events)
        return start, len(events), blocks

    async def _with_anchors(self, results, views, warning, query):
        if self._closed:
            raise RuntimeError("Session memory is closed")
        if results:
            results = await self._store.run(
                self._context.register_results, results, views, query
            )
        return results, warning

    async def read_context(
        self,
        anchor: str,
        *,
        before: int = 3,
        after: int = 3,
        offset: int | None = None,
    ):
        """Read a verified history window without loading an embedder."""
        if self._closed:
            raise RuntimeError("Session memory is closed")
        return await self._store.run(
            self._context.expand, anchor, before, after, offset
        )

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
        selected_branch_view: dict[int, int] | None = None,
        selected_agent: str | None = None,
    ):
        if mode not in ("auto", "fts", "hybrid", "semantic"):
            raise ValueError(f"Unknown search mode {mode!r}")
        overrides = (
            {selected_agent: selected_branch_view}
            if selected_branch_view is not None and selected_agent in names
            else None
        )
        views = await self._store.run(self._index_keywords, names, overrides)
        scope = AgentBranchVisibility(views)
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
                                self._memory._search_fts, query, k, agent, scope=scope
                            )
                            return await self._with_anchors(results, views, None, query)
                        raise ValueError("semantic search needs an embedding model")
                    manifest = self._memory._manifest()
                    # Model / format migration must finish every Agent before
                    # the session-wide vector corpus can be published.
                    vector_names = (
                        list(manifest.get("agents", names))
                        if manifest and manifest.get("vector") != "ready"
                        else names
                    )
                    for name in vector_names:
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
                        scope=scope,
                    )
                    if mode == "semantic":
                        return await self._with_anchors(results, views, None, query)
                    keywords = await self._store.run(
                        self._memory._search_fts, query, k * 2, agent, scope=scope
                    )
                    fused = self._memory._fuse_results(keywords, results, k)
                    return await self._with_anchors(fused, views, None, query)
            except Exception as exc:
                if (
                    self._closed
                    or isinstance(
                        exc,
                        (
                            MemoryCandidateLimitError,
                            MemoryLineageUnavailableError,
                            MemoryContextError,
                        ),
                    )
                    or (mode == "semantic" and not semantic_fallback)
                ):
                    raise
                warning = f"Embedding unavailable; using keyword search: {str(exc) or type(exc).__name__}"
                logger.warning(
                    "Memory search using FTS fallback",
                    error=str(exc) or type(exc).__name__,
                )
        results = await self._store.run(
            self._memory._search_fts, query, k, agent, scope=scope
        )
        return await self._with_anchors(results, views, warning, query)


def live_memory_for_store(store) -> LiveSessionMemory:
    """Get or create the store-owned index on its affinity thread."""
    memory = getattr(store, "_live_memory", None)
    if memory is None:
        memory = store._live_memory = LiveSessionMemory(store)
    return memory
