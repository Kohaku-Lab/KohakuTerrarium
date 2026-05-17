"""Read-only FTS5 / vector / hybrid memory search over a saved session.

Verbatim port of ``api/routes/sessions.py:search_session_memory``. The
HTTP route layer resolves the session name to a path, picks up the
process-level :class:`Terrarium` engine (so a live creature's store
can be reused), and delegates the search to this module.

The companion *write* path — building / rebuilding the vector index —
lives in :mod:`studio.sessions.memory_build`. ``build_embeddings``
below is kept as a thin alias for back-compat (the CLI's
``kt embedding`` and older tests both import it from here); new
callers should use ``memory_build.build_index`` directly.
"""

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kohakuterrarium.session.embedding import create_embedder
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.memory_build import (
    build_index as _build_index,
)
from kohakuterrarium.terrarium.engine import Terrarium


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
    mode: str,
    k: int,
    agent: str | None,
    engine: Terrarium | None,
) -> dict[str, Any]:
    """Run an FTS5 / vector / hybrid search across a saved session.

    Wraps the existing ``SessionMemory.search()`` — no new indexing
    behavior. Modes: ``auto`` (default), ``fts``, ``semantic``,
    ``hybrid``.
    """
    try:
        # Find the live creature (if running) to reuse its store
        # and embedder — same pattern as the search_memory builtin tool.
        live_agent, live_store = _live_store_for_path(engine, path)

        if live_store:
            store = live_store
            store.flush()
        else:
            store = SessionStore(path)

        # FTS-only queries do not touch the embedder; skipping
        # ``create_embedder`` here also dodges a slow / failing
        # HuggingFace model fetch on first run (Windows CI has been
        # observed timing out inside ``hf_hub_download``).
        if mode == "fts":
            embedder = None
        else:
            embed_config = _resolve_embed_config(store, live_agent)
            try:
                embedder = create_embedder(embed_config)
            except Exception as e:
                _ = e  # embedding unavailable, continue without
                embedder = None

        memory = SessionMemory(str(path), embedder=embedder, store=store)

        # Index unindexed events (idempotent — skips already indexed)
        meta = store.load_meta()
        for agent_name in meta.get("agents", []):
            events = store.get_events(agent_name)
            if events:
                memory.index_events(agent_name, events)

        results = memory.search(query=q, mode=mode, k=k, agent=agent)

        # Release the SessionMemory's own SQLite handles — without this
        # they linger until GC and (on Windows) block a later delete of
        # the .kohakutr file. The shared SessionStore is closed only
        # when it isn't a live creature's store.
        memory.close()
        if not live_store:
            store.close(update_status=False)
    except Exception as e:
        raise HTTPException(500, f"Memory search failed: {e}")

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
