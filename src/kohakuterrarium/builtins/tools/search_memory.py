"""Search memory tool: search session history via FTS or semantic search.

Model work stays outside the live store's serialized database executor.
"""

import asyncio
from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.session.embedding import create_embedder
from kohakuterrarium.session.memory_async import (
    LiveSessionMemory,
    live_memory_for_store,
)

# Display cap per search result: large enough to surface the meaningful body
# of a recovered tool output while bounding the tool-result context.
SEARCH_RESULT_DISPLAY_CHARS = 2000


@register_builtin("search_memory")
class SearchMemoryTool(BaseTool):
    """Search indexed session events by keyword or semantic similarity."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "search_memory"

    @property
    def description(self) -> str:
        return "Search this session's earlier events by keyword or meaning. Use to recall details already dropped from context. Not for searching files - use grep."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = args.get("query", "")
        if not query:
            return ToolResult(
                error="No query provided. Usage: search_memory(query='...')"
            )

        mode = args.get("mode", "auto")
        k = int(args.get("k", 5))
        agent = args.get("agent", None)

        context: ToolContext | None = kwargs.get("context")
        if not context:
            return ToolResult(error="No context available (session not attached)")

        memory = await self._get_memory(context)
        if memory is None:
            return ToolResult(
                error="Session memory not available. "
                "No session store attached or embedding not configured."
            )

        warning = None
        try:
            store = getattr(context.agent, "session_store", None)
            if isinstance(memory, LiveSessionMemory):
                config = (
                    await store.run(self._load_embed_config, store, context.agent)
                    if mode != "fts"
                    else None
                )
                results, warning = await memory.search(
                    query,
                    names=[context.agent_name or "agent"],
                    config=config,
                    create_embedder=create_embedder,
                    mode=mode,
                    k=k,
                    agent=agent,
                )
            else:
                results = await asyncio.to_thread(
                    memory.search, query, mode=mode, k=k, agent=agent
                )
        except Exception as e:
            return ToolResult(error=f"Search failed: {e}")

        if not results:
            return ToolResult(
                output=(
                    f"{warning}\nNo results found." if warning else "No results found."
                ),
                exit_code=0,
            )

        lines = [f"Found {len(results)} result(s) for: {query}\n"]
        if warning:
            lines.insert(0, warning + "\n")
        for i, r in enumerate(results, 1):
            header = f"#{i} [round {r.round_num}] {r.block_type}"
            if r.tool_name:
                header += f":{r.tool_name}"
            if r.agent:
                header += f" ({r.agent})"
            age = r.age_str
            if age:
                header += f" {age}"
            lines.append(header)
            # Bound each result so one event cannot consume the tool-result context.
            content = r.content
            if len(content) > SEARCH_RESULT_DISPLAY_CHARS:
                content = (
                    content[:SEARCH_RESULT_DISPLAY_CHARS]
                    + f"... ({len(r.content)} chars total)"
                )
            lines.append(content)
            lines.append("")

        return ToolResult(output="\n".join(lines), exit_code=0)

    async def _get_memory(self, context: ToolContext) -> Any:
        """Return the store-owned index without initializing an embedding model."""
        session = context.session
        agent = context.agent
        if not agent or not hasattr(agent, "session_store") or not agent.session_store:
            return getattr(session, "_memory", None)
        store = agent.session_store
        memory = await store.run(live_memory_for_store, store)
        if session:
            session._memory = memory
        return memory

    def _load_embed_config(self, store: Any, agent: Any) -> dict[str, Any] | None:
        """Resolve embedding configuration by persistence precedence."""
        # Persisted state preserves the embedding model used by an existing index.
        try:
            saved = store.state.get("embedding_config")
            if isinstance(saved, dict):
                return saved
        except (KeyError, Exception):
            pass

        if agent and hasattr(agent, "config"):
            memory_cfg = getattr(agent.config, "memory", None)
            if isinstance(memory_cfg, dict) and "embedding" in memory_cfg:
                return memory_cfg["embedding"]

        return {"provider": "auto"}
