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


@register_builtin("search_memory")
class SearchMemoryTool(BaseTool):
    """Search session history using keyword or semantic search.

    Searches the indexed session event log for relevant context.
    Results include round number, timestamp, and content.
    """

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

        # Get or create SessionMemory from context
        memory = self._get_memory(context)
        if memory is None:
            return ToolResult(
                error="Session memory not available. "
                "No session store attached or embedding not configured."
            )

        # Ensure events are indexed
        self._ensure_indexed(context, memory)

        # Search
        try:
            results = memory.search(query, mode=mode, k=k, agent=agent)
        except Exception as e:
            return ToolResult(error=f"Search failed: {e}")

        if not results:
            return ToolResult(output="No results found.", exit_code=0)

        # Format results
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
            # Content (truncated for context window efficiency)
            content = r.content
            if len(content) > 500:
                content = content[:500] + f"... ({len(r.content)} chars total)"
            lines.append(content)
            lines.append("")

        return ToolResult(output="\n".join(lines), exit_code=0)

    def _get_memory(self, context: ToolContext) -> Any:
        """Get or create SessionMemory from the agent context."""
        # Check if already cached on the session
        session = context.session
        if session and hasattr(session, "_memory"):
            return session._memory

        # Need session store to create memory
        agent = context.agent
        if not agent or not hasattr(agent, "session_store") or not agent.session_store:
            return None

        store = agent.session_store

        # Load embedding config from session state or agent config
        embed_config = self._load_embed_config(store, agent)
        try:
            embedder = create_embedder(embed_config)
        except Exception as e:
            logger.warning("Embedder creation failed, using FTS only", error=str(e))
            embedder = None

        memory = SessionMemory(store._path, embedder=embedder)

        # Cache on session for reuse
        if session:
            session._memory = memory

        return memory

    def _load_embed_config(self, store: Any, agent: Any) -> dict[str, Any] | None:
        """Load embedding config from session state, then agent config."""
        # 1. Check session state (saved by previous embedding run or agent start)
        try:
            saved = store.state.get("embedding_config")
            if isinstance(saved, dict):
                return saved
        except (KeyError, Exception):
            pass

        # 2. Check agent config
        if agent and hasattr(agent, "config"):
            memory_cfg = getattr(agent.config, "memory", None)
            if isinstance(memory_cfg, dict) and "embedding" in memory_cfg:
                return memory_cfg["embedding"]

        # 3. Default: auto-detect
        return {"provider": "auto"}

    def _ensure_indexed(self, context: ToolContext, memory: Any) -> None:
        """Index any unindexed events."""
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
