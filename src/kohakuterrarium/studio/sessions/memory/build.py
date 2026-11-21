"""Inspect and build saved-session memory indexes.

Search operations live in :mod:`studio.sessions.memory_search`; this module
creates indexes through :class:`kohakuterrarium.session.memory.index.SessionMemory`.
Builds are suitable for a background thread and report synchronous progress for
HTTP and WebSocket adapters to forward.
"""

from pathlib import Path
from typing import Any, Callable

from kohakuterrarium.session.memory.embedding import (
    create_embedder,
    effective_embedding_config,
)
from kohakuterrarium.session.memory.embedding_spec import persisted_embedding_config
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.query import _ALL_BRANCH_INDEX_FORMAT
from kohakuterrarium.session.memory.reconcile import SessionIndexReconciler
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


ProgressCallback = Callable[[dict[str, Any]], None]


def index_status(path: Path) -> dict[str, Any]:
    """Return a status snapshot for a session's vector index.

    Pure read — opens the SessionStore + SessionMemory, reads counters,
    closes them again. Safe to call from a request handler.

    Returns the same shape regardless of whether an index exists:

    - ``indexed``        — bool, ``True`` if any vector blocks exist
    - ``embedder``       — provider string saved at index time, or ``None``
    - ``model``          — model name saved at index time, or ``None``
    - ``dimensions``     — vector dimensions, or ``None``
    - ``fts_blocks``     — full-text-search block count (FTS is built
      on-demand by ``search_session_memory`` so it can be populated
      even when no vector index exists)
    - ``vec_blocks``     — vector-index block count
    - ``agents``         — list of agent names that have events to index
    """
    store = SessionStore(path)
    try:
        meta = store.load_meta()
        agents = list(meta.get("agents", []))
        # Search-only mode reports persisted index state without loading an embedder.
        memory = SessionMemory(str(path), embedder=None)
        try:
            stats = memory.get_stats()
            saved_provider = None
            saved_model = None
            try:
                saved = store.state.get("embedding_config")
                if isinstance(saved, dict):
                    saved_provider = saved.get("provider")
                    saved_model = saved.get("model")
            except (KeyError, Exception):
                pass
            vec_blocks = int(stats.get("vec_blocks") or 0)
            manifest = memory._manifest()
            ready = bool(
                manifest
                and manifest.get("format") == _ALL_BRANCH_INDEX_FORMAT
                and manifest.get("vector") == "ready"
                and manifest.get("fts") == "ready"
            )
            return {
                "indexed": ready and vec_blocks > 0,
                "embedder": saved_provider,
                "model": saved_model,
                "dimensions": stats.get("dimensions"),
                "fts_blocks": int(stats.get("fts_blocks") or 0),
                "vec_blocks": vec_blocks,
                "agents": agents,
            }
        finally:
            memory.close()
    finally:
        store.close(update_status=False)


def index_status_quick(path: Path) -> bool:
    """Cheap probe — does ``path`` have a vector index?

    Used by the saved-session listing so the row shows a "Build /
    Rebuild" affordance without paying for a full meta read per row.
    Opens the store, reads only the ``vec_dimensions`` state key,
    closes.
    """
    store = SessionStore(path)
    try:
        memory = SessionMemory(str(path), embedder=None)
        try:
            manifest = memory._manifest()
            return bool(
                manifest
                and manifest.get("format") == _ALL_BRANCH_INDEX_FORMAT
                and manifest.get("vector") == "ready"
                and manifest.get("fts") == "ready"
                and memory.has_vectors
                and (memory.get_stats().get("vec_blocks") or 0) > 0
            )
        finally:
            memory.close()
    finally:
        store.close(update_status=False)


def build_index(
    path: Path,
    *,
    provider: str = "auto",
    model: str | None = None,
    dimensions: int | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Build (or rebuild) the vector index for a saved session.

    Streams progress through ``progress`` as ``{phase, percent,
    blocks_indexed, blocks_total, agent}`` dicts. Phases:

    - ``"scan"``  — opening the store, listing agents
    - ``"embed"`` — running the embedder + writing vector blocks
    - ``"write"`` — flushing SQLite state at the end

    Returns the same payload as
    :func:`studio.sessions.memory_search.build_embeddings` so the CLI
    + the new HTTP route share the same response shape.

    ``provider == "auto"`` resolves to ``"model2vec"`` for the
    embedder ctor (matches the lazy-init behaviour in
    :mod:`session.embedding`).
    """

    def emit(phase: str, percent: int, agent: str = "", **extra: Any) -> None:
        if progress is None:
            return
        try:
            progress(
                {
                    "phase": phase,
                    "percent": percent,
                    "agent": agent,
                    **extra,
                }
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(
                "memory build progress callback raised", error=str(e), exc_info=True
            )

    emit("scan", 0, blocks_indexed=0, blocks_total=0)
    store = SessionStore(path)
    try:
        meta = store.load_meta()
        agents = list(meta.get("agents", []))
        if not agents:
            emit("write", 100, blocks_indexed=0, blocks_total=0)
            resolved_provider = "model2vec" if provider == "auto" else provider
            return {
                "path": str(path),
                "agents": [],
                "provider": resolved_provider,
                "model": model,
                "indexed_per_agent": {},
                # Keep the no-agent result compatible with SessionMemory.get_stats().
                "stats": {
                    "fts_blocks": 0,
                    "vec_blocks": 0,
                    "has_vectors": False,
                    "dimensions": 0,
                },
            }

        resolved_provider = "model2vec" if provider == "auto" else provider
        embed_config: dict[str, Any] = {"provider": resolved_provider}
        if model:
            embed_config["model"] = model
        if dimensions:
            embed_config["dimensions"] = dimensions
        embedder = create_embedder(embed_config)
        memory = SessionMemory(str(path), embedder=embedder, index_all_branches=True)
        try:
            reconciler = SessionIndexReconciler(store, memory)
            source = reconciler.capture(agents, all_agents=True)
            total_events_seen = sum(len(source.events[name]) for name in agents)
            indexed_per_agent: dict[str, dict[str, int]] = {}
            emit("scan", 10, blocks_indexed=0, blocks_total=total_events_seen)

            done_events = 0

            def on_agent(name: str, count: int, blocks: int) -> None:
                nonlocal done_events
                if name not in agents:
                    return  # Retired Agent namespaces still need empty checkpoints.
                indexed_per_agent[name] = {"events": count, "blocks": blocks}
                done_events += count
                percent = (
                    10 + int(80 * done_events / max(1, total_events_seen))
                    if total_events_seen
                    else 90
                )
                emit(
                    "embed",
                    percent,
                    agent=name,
                    blocks_indexed=done_events,
                    blocks_total=total_events_seen,
                )

            reconciler.build(source, force=force, progress=on_agent)

            # Persist configuration only for a non-empty vector index; otherwise
            # status must continue to present the session as needing its first build.
            total_blocks_indexed = sum(
                info["blocks"] for info in indexed_per_agent.values()
            )
            if total_blocks_indexed > 0:
                # A successful build must not silently publish an unrestorable
                # model identity if writing its effective spec fails.
                store.state["embedding_config"] = persisted_embedding_config(
                    effective_embedding_config(embed_config, embedder)
                )

            stats = memory.get_stats()
            emit(
                "write",
                100,
                blocks_indexed=total_events_seen,
                blocks_total=total_events_seen,
            )
            return {
                "path": str(path),
                "agents": agents,
                "provider": resolved_provider,
                "model": model,
                "indexed_per_agent": indexed_per_agent,
                "stats": stats,
            }
        finally:
            memory.close()
    finally:
        store.close(update_status=False)


__all__ = ["index_status", "index_status_quick", "build_index"]
