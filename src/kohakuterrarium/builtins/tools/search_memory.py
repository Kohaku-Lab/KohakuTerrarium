"""Search memory tool: search session history via FTS or semantic search.

Model work stays outside the live store's serialized database executor.
"""

import asyncio
from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.core.conversation.continuation import selected_branch_view
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolContext,
    ToolResult,
)
from kohakuterrarium.session.memory.embedding import (
    create_embedder,
    resolve_session_embedding_config,
)
from kohakuterrarium.session.memory.context import MemoryContextError
from kohakuterrarium.session.memory.preview import preview_text
from kohakuterrarium.session.memory.async_search import (
    LiveSessionMemory,
    live_memory_for_store,
)

# Display cap per search result: large enough to surface the meaningful body
# of a recovered tool output while bounding the tool-result context.
SEARCH_RESULT_DISPLAY_CHARS = 2000


def _navigation_integer(value: Any) -> Any:
    """Bracket/XML arguments are text; normalize plain decimal integers only."""
    if (
        isinstance(value, str)
        and len(value) <= 18
        and value.isascii()
        and value.isdecimal()
    ):
        return int(value)
    return value


def _include_search_history(value: Any) -> bool:
    """Normalize booleans from native APIs and text tool-call formats."""
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ValueError("include_search_history must be true or false")


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
            "Search earlier session events or inspect nearby history using an anchor. "
            "Use it to recover dropped context. Not for searching files - use grep."
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        query = args.get("query", "")
        anchor = args.get("anchor", "")
        if bool(query) == bool(anchor):
            return ToolResult(
                error="Provide exactly one of query or anchor. "
                "Usage: search_memory(query='...') or "
                "search_memory(anchor='...', before=3, after=3)"
            )

        mode = args.get("mode", "auto")
        agent = args.get("agent", None)
        try:
            include_search_history = _include_search_history(
                args.get("include_search_history", False)
            )
        except ValueError as exc:
            return ToolResult(error=str(exc))

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
                return ToolResult(
                    error="Context navigation requires an attached live SessionStore."
                )
            try:
                window = await memory.read_context(
                    anchor,
                    before=_navigation_integer(args.get("before", 3)),
                    after=_navigation_integer(args.get("after", 3)),
                    offset=_navigation_integer(args.get("offset")),
                )
                return ToolResult(
                    output=self._format_window(window),
                    exit_code=0,
                    metadata={
                        "has_before": window.has_before,
                        "has_after": window.has_after,
                        "event_count": len(window.events),
                    },
                )
            except MemoryContextError as exc:
                return ToolResult(
                    error=f"{exc.code}: {exc}", metadata={"code": exc.code}
                )
            except Exception as exc:
                return ToolResult(error=f"Context read failed: {exc}")

        k = int(args.get("k", 5))
        warning = None
        try:
            store = getattr(context.agent, "session_store", None)
            if isinstance(memory, LiveSessionMemory):
                # Resolve the active (possibly explicitly selected) lineage on
                # the store owner thread, not from an event-loop database read.
                current_view = await store.run(selected_branch_view, context.agent)
                config = (
                    await store.run(self._load_embed_config, store, context.agent)
                    if mode != "fts"
                    else None
                )
                results, warning = await memory.search(
                    query,
                    # Explicit agent queries must index the target namespace
                    # rather than only whatever creature issued the call.
                    names=[agent] if agent else [context.agent_name or "agent"],
                    config=config,
                    create_embedder=create_embedder,
                    mode=mode,
                    k=k,
                    agent=agent,
                    selected_branch_view=current_view or None,
                    selected_agent=context.agent_name or "agent",
                    include_search_history=include_search_history,
                )
            else:
                results = await asyncio.to_thread(
                    memory.search,
                    query,
                    mode=mode,
                    k=k,
                    agent=agent,
                    include_search_history=include_search_history,
                )
        except MemoryContextError as exc:
            return ToolResult(error=f"{exc.code}: {exc}", metadata={"code": exc.code})
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
            if r.anchor:
                lines.append(f"anchor: {r.anchor}")
            # Bound each result so one event cannot consume the tool-result context.
            lines.append(
                preview_text(
                    r.content, SEARCH_RESULT_DISPLAY_CHARS, focus=query
                ).content
            )
            lines.append("")

        if any(result.anchor for result in results):
            lines.append("Navigation (tool-generated, optional):")
            lines.append(
                "If the excerpt omits context needed for your task, call "
                "search_memory with anchor set to the relevant hit's anchor, "
                "before=3, after=3. Otherwise no further retrieval is needed."
            )
        return ToolResult(output="\n".join(lines), exit_code=0)

    @staticmethod
    def _format_window(window) -> str:
        noun = "event" if len(window.events) == 1 else "events"
        lines = [f"Memory context ({len(window.events)} {noun}, chronological):"]
        for event in window.events:
            header = f"[{event.role}] {event.kind}"
            if event.turn_index is not None:
                header += f" [turn {event.turn_index}"
                if event.branch_id is not None:
                    header += f" branch {event.branch_id}"
                header += "]"
            if event.role == "match" and event.part_index:
                header += f" [part {event.part_index}]"
            lines.extend((header, f"anchor: {event.anchor}", event.content))
            if event.truncated:
                next_offset = (
                    str(event.content_end)
                    if event.content_end < event.content_total
                    else "none"
                )
                lines.append(
                    f"content_slice={event.content_start}:{event.content_end}"
                    f"/{event.content_total} next_offset={next_offset}"
                )
            lines.append("")
        lines.append(
            f"has_before={str(window.has_before).lower()} "
            f"has_after={str(window.has_after).lower()}"
        )
        hints = []
        if window.events and window.has_before:
            hints.append(
                "Earlier visible context if needed: "
                f'search_memory(anchor="{window.events[0].anchor}", '
                "before=3, after=0)."
            )
        if window.events and window.has_after:
            hints.append(
                "Later visible context if needed: "
                f'search_memory(anchor="{window.events[-1].anchor}", '
                "before=0, after=3)."
            )
        if any(event.truncated for event in window.events):
            hints.append(
                "For truncated event text, use its anchor and next_offset "
                "with before=0, after=0 to read the next slice if needed; "
                "offset=0 reads an omitted beginning."
            )
        if hints:
            lines.extend(("", "Navigation (tool-generated, optional):", *hints))
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

    def _load_embed_config(self, store: Any, agent: Any) -> dict[str, Any]:
        """Keep the persisted model while using current authentication."""
        return resolve_session_embedding_config(store, agent)
