"""Search saved-session memory with FTS, vector, or hybrid retrieval.

Live stores can be reused through a process-local Terrarium engine. Index builds
live in :mod:`studio.sessions.memory_build`; :func:`build_embeddings` remains a
compatibility alias for existing CLI and Python callers.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.errors import SessionError, SessionNotFoundError
from kohakuterrarium.session.embedding import create_embedder
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.memory_build import (
    build_index as _build_index,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _live_store_for_path(
    engine: Terrarium | None, path: Path
) -> tuple[Any, SessionStore | None]:
    """Find a live creature whose store points at ``path`` (if any).

    Returns ``(live_agent, live_store)``; both are ``None`` when the
    session is not currently running OR ``engine`` is ``None`` (lab-host
    mode runs no host agent engine — live creatures live on workers
    and the host has nothing to walk).  The caller then opens a fresh
    ``SessionStore`` if needed.
    """
    if engine is None:
        return None, None
    for creature in engine.list_creatures():
        ag = creature.agent
        if ag and hasattr(ag, "session_store") and ag.session_store:
            ss = ag.session_store
            if str(path) in str(getattr(ss, "_path", "")):
                return ag, ss
    return None, None


def build_embeddings(
    path: Path,
    *,
    provider: str = "model2vec",
    model: str | None = None,
    dimensions: int | None = None,
) -> dict[str, Any]:
    """Build embeddings for a saved session (offline / CLI alias).

    Thin pass-through to :func:`memory_build.build_index` so the CLI
    surface and the HTTP/WS surface share one implementation. The
    canonical home for the build logic is ``memory_build``; this
    function exists so the long-standing ``kt embedding`` import path
    keeps working without touching every caller.
    """
    return _build_index(
        path,
        provider=provider,
        model=model,
        dimensions=dimensions,
        force=False,
        progress=None,
    )


def _resolve_embed_config(store: SessionStore, live_agent: Any) -> dict[str, Any]:
    """Mirror ``builtins/tools/search_memory.py`` config resolution."""
    embed_config: dict[str, Any] | None = None
    try:
        saved = store.state.get("embedding_config")
        if isinstance(saved, dict):
            embed_config = saved
    except (KeyError, Exception):
        pass
    if embed_config is None and live_agent and hasattr(live_agent, "config"):
        memory_cfg = getattr(live_agent.config, "memory", None)
        if isinstance(memory_cfg, dict) and "embedding" in memory_cfg:
            embed_config = memory_cfg["embedding"]
    if embed_config is None:
        embed_config = {"provider": "auto"}
    return embed_config


async def search_session_memory(
    path: Path,
    *,
    q: str,
    mode: str = "auto",
    k: int = 10,
    agent: str | None = None,
    engine: Terrarium | None = None,
) -> dict[str, Any]:
    """Run an FTS5 / vector / hybrid search across a saved session.

    Wraps the existing ``SessionMemory.search()`` — no new indexing
    behavior. Modes: ``auto`` (default), ``fts``, ``semantic``,
    ``hybrid``.

    This adapter keeps the legacy HTTP contract: ``SessionMemory.search``
    is strict (an explicit ``semantic`` request without an embedder, or
    an unknown mode, raises ``ValueError`` — E4), but the web frontend
    offers ``semantic`` in its mode picker regardless of whether an
    index / embedding model exists, and the old endpoint answered that
    with FTS-fallback results.  Degrade here (log + fall back to FTS)
    instead of bubbling the ValueError into a 500.

    Raises :class:`SessionNotFoundError` when ``path`` does not exist
    (BEFORE opening anything — ``SessionStore(path)`` would otherwise
    mint an empty ``.kohakutr`` as a side effect of the lookup) and
    :class:`SessionError` when the search itself fails.
    """
    path = Path(path)
    if not path.exists():
        raise SessionNotFoundError(f"Session not found: {path}")
    try:
        # Reuse a live store and its agent configuration when the session is running.
        live_agent, live_store = _live_store_for_path(engine, path)

        if live_store:
            store = live_store
            store.flush()
        else:
            store = SessionStore(path)

        # FTS does not need an embedder, avoiding unnecessary model downloads.
        if mode == "fts":
            embedder = None
        else:
            embed_config = _resolve_embed_config(store, live_agent)
            try:
                embedder = create_embedder(embed_config)
            except Exception as e:
                _ = e  # Search can fall back to FTS when embedding is unavailable.
                embedder = None

        memory = SessionMemory(str(path), embedder=embedder)

        # Incremental indexing skips events already represented in memory.
        meta = store.load_meta()
        for agent_name in meta.get("agents", []):
            events = store.get_events(agent_name)
            if events:
                memory.index_events(agent_name, events)

        # Preserve the adapter contract by degrading unsupported semantic requests to FTS.
        effective_mode = mode
        if effective_mode not in ("auto", "fts", "semantic", "hybrid"):
            logger.warning("Unknown search mode, falling back to FTS", requested=mode)
            effective_mode = "fts"
        elif effective_mode == "semantic" and not memory.has_vectors:
            logger.warning("No embedding model, falling back to FTS")
            effective_mode = "fts"

        results = memory.search(query=q, mode=effective_mode, k=k, agent=agent)

        # Close owned SQLite handles promptly so Windows can delete stopped sessions;
        # a live creature retains ownership of its shared SessionStore.
        memory.close()
        if not live_store:
            store.close(update_status=False)
    except Exception as e:
        # Callers receive a concise typed error while logs retain the traceback.
        logger.exception(
            "memory_search failed",
            path=str(path),
            query=q,
            mode=mode,
            k=k,
            agent=agent,
        )
        raise SessionError(f"Memory search failed: {type(e).__name__}: {e}")

    return {
        "session_name": path.stem,
        "query": q,
        "mode": mode,
        "k": k,
        "count": len(results),
        "results": [
            {
                "content": r.content,
                "round": r.round_num,
                "block": r.block_num,
                "agent": r.agent,
                "block_type": r.block_type,
                "score": r.score,
                "ts": r.ts,
                "tool_name": r.tool_name,
                "channel": r.channel,
            }
            for r in results
        ],
    }
