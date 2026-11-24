"""CLI memory commands — terminal formatters around session memory.

The actual indexing logic lives in
:mod:`kohakuterrarium.studio.sessions.memory.build` (write path) and
:mod:`kohakuterrarium.studio.sessions.memory.search` (read path); this
module is strictly the rich-CLI presentation layer.
"""

from kohakuterrarium.cli.run import _resolve_session
from kohakuterrarium.session.memory.embedding import create_embedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.migration import backfill_legacy_sources
from kohakuterrarium.session.occupancy.registry import live_store_for
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.memory.build import build_index
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def embedding_cli(
    session_query: str,
    provider: str = "model2vec",
    model: str | None = None,
    dimensions: int | None = None,
) -> int:
    """Build embeddings for a session (offline)."""
    path = _resolve_session(session_query)
    if path is None:
        print(f"Session not found: {session_query}")
        return 1

    try:
        result = build_index(
            path,
            provider=provider,
            model=model,
            dimensions=dimensions,
        )
    except Exception as e:
        print(f"Error: {e}")
        return 1

    print(f"Session: {path.name}")
    print(f"Agents: {', '.join(result['agents'])}")
    # Report the resolved provider because automatic selection may change it.
    print(
        f"Embedding: {result.get('provider', provider)}"
        + (f" ({model})" if model else "")
    )
    print()

    for agent_name, info in result["indexed_per_agent"].items():
        if info["events"] == 0:
            print(f"  {agent_name}: no events")
        else:
            print(
                f"  {agent_name}: {info['blocks']} blocks indexed ({info['events']} events)"
            )

    stats = result["stats"] or {}
    print(
        f"\nDone. FTS: {stats.get('fts_blocks', 0)} blocks, "
        f"Vector: {stats.get('vec_blocks', 0)} blocks "
        f"({stats.get('dimensions', 0)}d)"
    )
    return 0


def migrate_anchors_cli(session_query: str, agent: str) -> int:
    """Explicit offline provenance migration for one Agent's old index rows.

    Does not build embeddings, touch the raw event log, or publish a Session-
    wide migration marker. The operator must close all other session writers.
    """
    path = _resolve_session(session_query)
    if path is None:
        print(f"Session not found: {session_query}")
        return 1
    if not agent or agent != agent.strip():
        print("An exact non-empty Agent namespace is required.")
        return 1

    # A same-process live writer may not have opted into FileLock. Reject it
    # before constructing a second store with its own independent handles.
    if live_store_for(path) is not None:
        print("Session is already open here; stop live agents before migrating.")
        return 1

    store = memory = None
    try:
        store = SessionStore(path, writer_lock=True)
        memory = SessionMemory(str(path))  # saved vector dimensions, no encoder
        report = backfill_legacy_sources(store, memory, agent)
        print(
            f"Anchor metadata for {agent}: "
            f"FTS patched={report.fts_patched}, present={report.fts_present}; "
            f"Vector patched={report.vector_patched}, present={report.vector_present}"
        )
        print("Done. No Embedding computation or global migration marker.")
        return 0
    except Exception as exc:
        print(f"Migration refused or incomplete: {exc}")
        print("Stop all Session writers, verify index freshness, then retry.")
        return 1
    finally:
        if memory is not None:
            memory.close()
        if store is not None:
            store.close(update_status=False)


def search_cli(
    session_query: str,
    query: str,
    mode: str = "auto",
    agent: str | None = None,
    k: int = 10,
) -> int:
    """Search a session's memory."""
    path = _resolve_session(session_query)
    if path is None:
        print(f"Session not found: {session_query}")
        return 1

    # Read-only: searching must not bump the session's last_active.
    store = SessionStore.open_readonly(path)
    try:
        # Semantic modes can fall back when no query embedder is available.
        embedder = None
        if mode in ("semantic", "hybrid", "auto"):
            try:
                embedder = create_embedder({"provider": "auto"})
            except Exception as e:
                logger.warning(
                    "Failed to create embedder for search", error=str(e), exc_info=True
                )

        # Saved dimensions let SessionMemory discover existing vector tables.
        memory = SessionMemory(str(path), embedder=embedder)

        if mode in ("semantic", "hybrid") and not memory.has_vectors:
            print("No vector index found. Run 'kt embedding' first, or use --mode fts")
            if mode == "semantic":
                return 1
            mode = "fts"

        results = memory.search(query, mode=mode, k=k, agent=agent)

        if not results:
            print("No results found.")
            return 0

        print(f"Found {len(results)} result(s) for: {query}")
        print(f"Mode: {mode}")
        print()

        for i, r in enumerate(results, 1):
            age = r.age_str
            header = f"#{i}  [round {r.round_num}]  {r.block_type}"
            if r.tool_name:
                header += f":{r.tool_name}"
            if r.agent:
                header += f"  ({r.agent})"
            if age:
                header += f"  {age}"
            print(header)
            content = r.content
            if len(content) > 200:
                content = content[:200] + "..."
            for line in content.split("\n"):
                print(f"  {line}")
            print()

        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        store.close()
