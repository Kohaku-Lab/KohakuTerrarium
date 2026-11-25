"""CLI memory commands — terminal formatters around session memory.

The actual indexing logic lives in
:mod:`kohakuterrarium.studio.sessions.memory.build` (write path) and
:mod:`kohakuterrarium.studio.sessions.memory.search` (read path); this
module is strictly the rich-CLI presentation layer.
"""

import sys

from kohakuterrarium.cli.run import _resolve_session
from kohakuterrarium.session.memory.embedding import create_embedder
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.rebuild import (
    inspect_index,
    rebuild_session_memory,
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


def rebuild_memory_cli(
    session_query: str,
    *,
    vectors: str = "auto",
    provider: str | None = None,
    model: str | None = None,
    dimensions: int | None = None,
    dry_run: bool = False,
) -> int:
    """Restore the Session's derived Search index without altering event history.

    Interactive users can choose how to treat existing vectors; automation
    must provide an explicit choice when semantic rows are present.
    """
    path = _resolve_session(session_query)
    if path is None:
        print(f"Session not found: {session_query}")
        return 1
    try:
        current = inspect_index(path)
        print(f"Session: {path.name}")
        print(f"Existing FTS: {current.fts_rows} indexed blocks")
        if current.vector_rows:
            print(
                "Existing vectors: "
                + ", ".join(
                    f"{count} blocks ({dims}d)" for dims, count in current.vector_rows
                )
            )
        else:
            print("Existing vectors: none")
        print("Plan: rebuild current selected-lineage Search index for all agents.")
        print("Search coverage can change if the old index included other branches.")
        print("Source history will remain unchanged; original database is backed up.")
        if dry_run:
            if current.vectors_present and vectors == "auto":
                print("Action required: choose --vectors rebuild or --vectors drop.")
            return 0

        if vectors == "auto" and current.vectors_present:
            if not sys.stdin.isatty():
                print(
                    "Existing semantic index requires a choice: "
                    "--vectors rebuild (may incur model/API cost), or "
                    "--vectors drop (disables old semantic index)."
                )
                return 1
            print(
                "Existing vectors need re-embedding for the current index "
                "format; old vectors will not be reused without proof."
            )
            choice = input("Rebuild vectors [r], drop old vectors [d], abort [Enter]? ")
            if choice.strip().lower() in ("r", "rebuild"):
                vectors = "rebuild"
            elif choice.strip().lower() in ("d", "drop"):
                vectors = "drop"
            else:
                print("Cancelled without modifying the Session.")
                return 1

        result = rebuild_session_memory(
            path,
            vectors=vectors,
            provider=provider,
            model=model,
            dimensions=dimensions,
        )
        print(
            f"Rebuilt {result.fts_rows} FTS and {result.vector_rows} vector blocks "
            f"from {result.events} events across {len(result.agents)} agents."
        )
        if result.vector_policy == "drop":
            print("Old vector index explicitly disabled; re-embed later to restore it.")
        print(f"Original database backup: {result.backup}")
        print("Done. Search and Anchor metadata now use the current index format.")
        return 0
    except Exception as exc:
        # Providers can raise ImportError and library-specific load failures.
        # The lower-level rebuild cleans staging in its finally block; keep
        # expected model/setup failures actionable instead of a CLI traceback.
        print(f"Rebuild refused or incomplete: {exc}")
        print(
            "Check that the Session is closed and the embedding provider is "
            "available, then retry. Any existing backup is left intact."
        )
        return 1


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
