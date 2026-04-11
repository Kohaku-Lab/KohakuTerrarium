"""CLI memory commands — build embeddings and search sessions."""

from kohakuterrarium.cli.run import _resolve_session
from kohakuterrarium.session.embedding import create_embedder
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.store import SessionStore
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

    store = SessionStore(path)
    try:
        meta = store.load_meta()
        agents = meta.get("agents", [])

        # Build embedder config
        embed_config: dict = {"provider": provider}
        if model:
            embed_config["model"] = model
        if dimensions:
            embed_config["dimensions"] = dimensions

        print(f"Session: {path.name}")
        print(f"Agents: {', '.join(agents)}")
        print(f"Embedding: {provider}" + (f" ({model})" if model else ""))
        print()

        embedder = create_embedder(embed_config)
        memory = SessionMemory(str(path), embedder=embedder, store=store)

        total_blocks = 0
        for agent_name in agents:
            events = store.get_events(agent_name)
            if not events:
                print(f"  {agent_name}: no events")
                continue
            count = memory.index_events(agent_name, events)
            total_blocks += count
            print(f"  {agent_name}: {count} blocks indexed ({len(events)} events)")

        stats = memory.get_stats()
        print(
            f"\nDone. FTS: {stats['fts_blocks']} blocks, "
            f"Vector: {stats['vec_blocks']} blocks "
            f"({stats['dimensions']}d)"
        )
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        store.close()


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
            # Show content (truncated for display)
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
