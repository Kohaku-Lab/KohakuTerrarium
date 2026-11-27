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
from kohakuterrarium.session.memory.embedding import create_embedder
from kohakuterrarium.session.memory.navigation import MemoryNavigationError
from kohakuterrarium.session.memory.async_search import (
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
        return (
            "Search this session's history by keyword or meaning. "
            "Use it to recall prior turns and explore context via anchors. "
            "Not for files - use grep."
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = args.get("query", "")
        anchor = args.get("anchor", "")
        if bool(query) == bool(anchor):
            return ToolResult(
                error="Provide exactly one of query or anchor in search_memory."
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

        if anchor:
            if not isinstance(memory, LiveSessionMemory):
                return ToolResult(error="Anchors require an attached live session.")
            try:
                window = await memory.read_context(
                    anchor,
                    before=args.get("before", 3),
                    after=args.get("after", 3),
                    offset=args.get("offset"),
                )
                return ToolResult(output=self._format_window(window), exit_code=0)
            except MemoryNavigationError as exc:
                return ToolResult(error=f"{exc.code}: {exc}")

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
                warning = getattr(results, "warning", None)
        except Exception as e:
            return ToolResult(error=f"Search failed: {e}")

        if not results:
            no_hits = (
                "No eligible hits within the scanned candidate budget."
                if getattr(results, "warning", None)
                else "No results found."
            )
            return ToolResult(
                output="\n".join((warning, no_hits)) if warning else no_hits,
                exit_code=0,
            )

        if isinstance(memory, LiveSessionMemory):
            try:
                results = await memory.attach_anchors(results, query)
            except MemoryNavigationError as exc:
                # An incompatible historical index must not break legacy search.
                scope = (
                    " for some hits"
                    if any(getattr(r, "anchor", "") for r in results)
                    else ""
                )
                warning = (warning + "\n" if warning else "") + (
                    f"Anchors unavailable{scope} ({exc.code}): {exc}"
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
            if getattr(r, "anchor", ""):
                lines.append(f"anchor: {r.anchor}")
            # Bound each result so one event cannot consume the tool-result context.
            content = r.content
            if len(content) > SEARCH_RESULT_DISPLAY_CHARS:
                content = (
                    content[:SEARCH_RESULT_DISPLAY_CHARS]
                    + f"... ({len(r.content)} chars total)"
                )
            lines.append(content)
            lines.append("")

        tool_call = next(
            (
                r
                for r in results
                if getattr(r, "anchor", "")
                and r.block_type == "tool"
                and r.content.startswith("[tool:")
            ),
            None,
        )
        if tool_call:
            lines.extend(
                (
                    "Navigation (optional): Follow this tool call to its "
                    "recorded output, which may not be directly searchable:",
                    f'search_memory(anchor="{tool_call.anchor}", before=0, after=3)',
                    "Continue from the last returned event's anchor if more "
                    "neighboring events are needed.",
                )
            )

        return ToolResult(output="\n".join(lines), exit_code=0)

    @staticmethod
    def _format_window(window) -> str:
        lines = [f"Memory context ({len(window.events)} events, chronological):"]
        for event in window.events:
            lines.append(f"[{event.role}] {event.kind}")
            lines.append(f"anchor: {event.anchor}")
            lines.append(event.content)
            if event.truncated:
                lines.append(
                    f"content_slice={event.start}:{event.end}/{event.total}"
                    f" next_offset={event.end if event.end < event.total else 'none'}"
                )
                if event.truncated and event.end < event.total:
                    lines.append(
                        "Continue: "
                        f'search_memory(anchor="{event.anchor}", '
                        f"before=0, after=0, offset={event.end})"
                    )
            lines.append("")
        before = (
            "unknown" if window.has_before is None else str(window.has_before).lower()
        )
        after = "unknown" if window.has_after is None else str(window.has_after).lower()
        lines.append(f"has_before={before} has_after={after}")
        return "\n".join(lines)

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
