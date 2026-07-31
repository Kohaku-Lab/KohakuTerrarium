"""Search memory tool: search session history via FTS or semantic search."""

from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.session.embedding import create_embedder
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

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
        return "Search session history (keyword or semantic). Use when you need details from earlier in the conversation."

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

        memory = self._get_memory(context)
        if memory is None:
            return ToolResult(
                error="Session memory not available. "
                "No session store attached or embedding not configured."
            )

        self._ensure_indexed(context, memory)

        try:
            results = memory.search(query, mode=mode, k=k, agent=agent)
        except Exception as e:
            return ToolResult(error=f"Search failed: {e}")

        if not results:
            return ToolResult(output="No results found.", exit_code=0)

        lines = [f"Found {len(results)} result(s) for: {query}\n"]
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

    def _get_memory(self, context: ToolContext) -> Any:
        """Return the session-scoped memory index, creating it when needed."""
        session = context.session
        if session and hasattr(session, "_memory"):
            return session._memory

        agent = context.agent
        if not agent or not hasattr(agent, "session_store") or not agent.session_store:
            return None

        store = agent.session_store

        embed_config = self._load_embed_config(store, agent)
        try:
            embedder = create_embedder(embed_config)
        except Exception as e:
            logger.warning("Embedder creation failed, using FTS only", error=str(e))
            embedder = None

        memory = SessionMemory(store._path, embedder=embedder)

        # The session owns the cache so it cannot leak across attached stores.
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

    def _ensure_indexed(self, context: ToolContext, memory: Any) -> None:
        """Bring the memory index up to date with the attached session store."""
        agent = context.agent
        if not agent or not hasattr(agent, "session_store") or not agent.session_store:
            return

        store = agent.session_store
        agent_name = context.agent_name or "agent"

        try:
            events = store.get_events(agent_name)
            if events:
                memory.index_events(agent_name, events)
        except Exception as e:
            logger.warning("Memory indexing failed", error=str(e))
