"""CLI memory commands — terminal formatters around session memory.

The actual indexing logic lives in
:mod:`kohakuterrarium.studio.sessions.memory.build` (write path) and
:mod:`kohakuterrarium.studio.sessions.memory.search` (read path); this
module is strictly the rich-CLI presentation layer.
"""

from kohakuterrarium.cli.run import _resolve_session
from kohakuterrarium.session.memory.embedding import create_embedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.visibility import (
    requested_store_view,
    resolve_search_view,
)
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
    memory = None
    try:
        # Query vectors must come from the same configured model that
        # produced the persisted index, never the CLI's own auto default.
        embedder = None
        if mode in ("semantic", "hybrid", "auto"):
            try:
                saved = store.state.get("embedding_config")
                config = saved if isinstance(saved, dict) else {"provider": "auto"}
                embedder = create_embedder(config)
            except Exception as e:
                logger.warning(
                    "Failed to create embedder for search", error=str(e), exc_info=True
                )

        # Saved dimensions let SessionMemory discover existing vector tables.
        memory = SessionMemory(str(path), embedder=embedder, index_all_branches=True)
        # The CLI must not silently read an obsolete selected-only index after
        # a Studio or live search upgraded this session's index format.
        branch_views = {}
        for name in store.load_meta().get("agents", []):
            events = store.get_memory_events(name)
            branch_views[name] = resolve_search_view(
                events, requested_store_view(store, name)
            )
            if events:
                # A search is not authorized to rewrite vectors using an
                # independently chosen embedding model. Catch up FTS only.
                memory._index_keywords(name, events)

        if mode in ("semantic", "hybrid") and not memory.has_vectors:
            print("No vector index found. Run 'kt embedding' first, or use --mode fts")
            if mode == "semantic":
                return 1
            mode = "fts"

        try:
            results = memory.search(
                query, mode=mode, k=k, agent=agent, branch_views=branch_views
            )
        except ValueError as exc:
            if mode not in ("auto", "hybrid") or "vector index stale" not in str(exc):
                raise
            print("Vector index requires a rebuild; using FTS instead.")
            mode = "fts"
            results = memory.search(
                query, mode=mode, k=k, agent=agent, branch_views=branch_views
            )

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
        if memory is not None:
            memory.close()
        store.close(update_status=False)
