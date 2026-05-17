"""Memory-index status + progress-emitting build for a saved session.

Companion to :mod:`studio.sessions.memory_search`: the search routes
read indexes, this module *creates* them. Both eventually call into
:class:`kohakuterrarium.session.memory.SessionMemory`.

The build job runs on a background thread (it spends most of its
time inside the embedder + SQLite writes) and reports progress via
a sync callback that the HTTP/WS adapter forwards to the client.
"""

from pathlib import Path
from typing import Any, Callable

from kohakuterrarium.session.embedding import create_embedder
from kohakuterrarium.session.memory import SessionMemory
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
        # No embedder — open in search-only mode so SessionMemory can
        # reflect whatever index has been written previously.
        memory = SessionMemory(str(path), embedder=None, store=store)
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
            return {
                "indexed": vec_blocks > 0,
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
        memory = SessionMemory(str(path), embedder=None, store=store)
        try:
            return (
                memory.has_vectors and (memory.get_stats().get("vec_blocks") or 0) > 0
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
            logger.debug("memory build progress callback raised", error=str(e))

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
                # Same shape as ``SessionMemory.get_stats()`` so callers
                # (CLI formatter, HTTP route) don't have to special-case
                # the no-agents path.
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
        memory = SessionMemory(str(path), embedder=embedder, store=store)
        try:
            # Force rebuild — clear the previous indexed-count so
            # ``index_events`` re-indexes from event 0.
            if force:
                for agent_name in agents:
                    try:
                        memory._set_indexed_count(agent_name, 0)
                        memory._clear_fts(agent_name)
                    except Exception as e:  # pragma: no cover - defensive
                        logger.debug(
                            "force-rebuild clear failed",
                            agent=agent_name,
                            error=str(e),
                        )

            indexed_per_agent: dict[str, dict[str, int]] = {}
            total_events_seen = 0
            # Two-pass: count events first so percent has a meaningful
            # denominator, then index. Counting is a cheap meta read.
            agent_events: dict[str, list[dict]] = {}
            for agent_name in agents:
                events = store.get_events(agent_name) or []
                agent_events[agent_name] = events
                total_events_seen += len(events)
            emit(
                "scan",
                10,
                blocks_indexed=0,
                blocks_total=total_events_seen,
            )

            done_events = 0
            for agent_name in agents:
                events = agent_events[agent_name]
                if not events:
                    indexed_per_agent[agent_name] = {"events": 0, "blocks": 0}
                    continue
                blocks_count = memory.index_events(agent_name, events)
                indexed_per_agent[agent_name] = {
                    "events": len(events),
                    "blocks": blocks_count,
                }
                done_events += len(events)
                percent = (
                    10 + int(80 * done_events / max(1, total_events_seen))
                    if total_events_seen
                    else 90
                )
                emit(
                    "embed",
                    percent,
                    agent=agent_name,
                    blocks_indexed=done_events,
                    blocks_total=total_events_seen,
                )

            # Persist the embedder choice so subsequent searches use
            # the same provider/model without the caller having to
            # repeat the config — but ONLY when at least one block was
            # actually written. Otherwise ``index_status`` would
            # report a configured embedder for a session that has no
            # vectors, and the frontend would show "Rebuild" instead
            # of "Build".
            total_blocks_indexed = sum(
                info["blocks"] for info in indexed_per_agent.values()
            )
            if total_blocks_indexed > 0:
                try:
                    store.state["embedding_config"] = {
                        "provider": resolved_provider,
                        "model": model,
                        "dimensions": dimensions,
                    }
                except Exception as e:  # pragma: no cover - state vault corruption
                    logger.warning("embedding_config persist failed", error=str(e))

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
