"""CLI memory commands — terminal formatters around session memory.

The actual indexing / search logic lives in
:mod:`kohakuterrarium.studio.sessions.memory_search`; this module is
strictly the rich-CLI presentation layer.
"""

from kohakuterrarium.cli.run import _resolve_session
from kohakuterrarium.session.embedding import create_embedder
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.memory_search import build_embeddings
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
        result = build_embeddings(
            path, provider=provider, model=model, dimensions=dimensions
        )
    except Exception as e:
        print(f"Error: {e}")
        return 1

    print(f"Session: {path.name}")
    print(f"Agents: {', '.join(result['agents'])}")
    print(f"Embedding: {provider}" + (f" ({model})" if model else ""))
    print()

    for agent_name, info in result["indexed_per_agent"].items():
        if info["events"] == 0:
            print(f"  {agent_name}: no events")
        else:
            print(
                f"  {agent_name}: {info['blocks']} blocks indexed ({info['events']} events)"
            )

    stats = result["stats"]
    print(
        f"\nDone. FTS: {stats['fts_blocks']} blocks, "
        f"Vector: {stats['vec_blocks']} blocks "
        f"({stats['dimensions']}d)"
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

    store = SessionStore(path)
    try:
        # Try to create embedder for query encoding (semantic/hybrid)
        embedder = None
        if mode in ("semantic", "hybrid", "auto"):
            try:
                embedder = create_embedder({"provider": "auto"})
            except Exception as e:
                logger.debug(
                    "Failed to create embedder for search", error=str(e), exc_info=True
                )

        # SessionMemory discovers existing vector tables via saved dimensions
        memory = SessionMemory(str(path), embedder=embedder, store=store)

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
