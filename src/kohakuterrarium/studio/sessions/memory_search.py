"""Read-only FTS5 / vector / hybrid memory search over a saved session.

Verbatim port of ``api/routes/sessions.py:search_session_memory``. The
HTTP route layer resolves the session name to a path, picks up the
process-level :class:`Terrarium` engine (so a live creature's store
can be reused), and delegates the search to this module.
"""

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kohakuterrarium.session.embedding import create_embedder
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.engine import Terrarium


def _live_store_for_path(
    engine: Terrarium, path: Path
) -> tuple[Any, SessionStore | None]:
    """Find a live creature whose store points at ``path`` (if any).

    Returns ``(live_agent, live_store)``; both are ``None`` when the
    session is not currently running. The caller then opens a fresh
    ``SessionStore`` if needed.
    """
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
    """Build embeddings for a saved session (offline / CLI).

    Returns ``{path, agents, indexed_per_agent, stats}``. The CLI
    formatter renders the dict; tests assert on it directly.
    """
    store = SessionStore(path)
    try:
        meta = store.load_meta()
        agents = list(meta.get("agents", []))

        embed_config: dict[str, Any] = {"provider": provider}
        if model:
            embed_config["model"] = model
        if dimensions:
            embed_config["dimensions"] = dimensions

        embedder = create_embedder(embed_config)
        memory = SessionMemory(str(path), embedder=embedder, store=store)

        indexed: dict[str, dict[str, int]] = {}
        for agent_name in agents:
            events = store.get_events(agent_name)
            if not events:
                indexed[agent_name] = {"events": 0, "blocks": 0}
                continue
            count = memory.index_events(agent_name, events)
            indexed[agent_name] = {"events": len(events), "blocks": count}

        stats = memory.get_stats()
        return {
            "path": str(path),
            "agents": agents,
            "provider": provider,
            "model": model,
            "indexed_per_agent": indexed,
            "stats": stats,
        }
    finally:
        store.close()


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
    engine: Terrarium,
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
