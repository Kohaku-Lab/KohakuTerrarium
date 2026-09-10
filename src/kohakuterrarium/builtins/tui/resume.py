"""Resume-history replay helpers: turn grouping and widget construction.

Extracted from ``output.py`` so the TUI output module stays focused on
event routing; these functions are pure building blocks over session
events and Textual widgets.
"""

from textual.widgets import Markdown

from kohakuterrarium.builtins.tui.tool_args import (
    format_args_detail,
    format_args_preview,
)
from kohakuterrarium.builtins.tui.widgets import (
    CompactSummaryBlock,
    SubAgentBlock,
    ToolBlock,
    TriggerMessage,
    UserMessage,
)
from kohakuterrarium.llm.message import content_display_text
from kohakuterrarium.session.history import (
    dedupe_adjacent_duplicate_events,
    select_live_event_ids,
)


def _group_into_turns(events: list[dict]) -> list[dict]:
    """Group events into turns while preserving step order."""
    events = dedupe_adjacent_duplicate_events(events)
    live_ids = select_live_event_ids(events)
    turns: list[dict] = []
    current: dict | None = None

    for evt in events:
        etype = evt.get("type", "")
        eid = evt.get("event_id")
        if isinstance(eid, int) and eid not in live_ids:
            continue
        if etype == "user_input":
            if current:
                turns.append(current)
            current = {
                "input_type": "user_input",
                # Web sessions persist multimodal content-part lists; widgets
                # require display text.
                "input": content_display_text(evt.get("content", "")),
                "steps": [],
            }
        elif etype == "trigger_fired":
            if current:
                turns.append(current)
            ch = evt.get("channel", "")
            sender = evt.get("sender", "")
            current = {
                "input_type": "trigger",
                "input": f"[{ch}] {sender}",
                "trigger_content": content_display_text(evt.get("content", "")),
                "steps": [],
            }
        elif etype in ("compact_start", "compact_complete", "compact_skipped"):
            # Background compaction belongs to the nearest active or prior turn.
            target = current if current else (turns[-1] if turns else None)
            if target:
                target["steps"].append((etype, evt))
        elif current is not None:
            if etype in ("text", "text_chunk"):
                # Replay treats streamed chunks and complete text identically.
                if current["steps"] and current["steps"][-1][0] == "text":
                    current["steps"][-1] = (
                        "text",
                        current["steps"][-1][1] + evt.get("content", ""),
                    )
                else:
                    current["steps"].append(("text", evt.get("content", "")))
            elif etype in (
                "tool_call",
                "tool_result",
                "subagent_call",
                "subagent_result",
                "subagent_tool",
                "processing_start",
                "processing_end",
                "token_usage",
            ):
                current["steps"].append((etype, evt))

    if current:
        turns.append(current)
    return turns


def _iter_all_steps(turns: list[dict]):
    """Yield each step across all turns."""
    for turn in turns:
        for step in turn.get("steps", []):
            yield step


def _build_resume_widgets(turns: list[dict]) -> list:
    """Build resume widgets synchronously without mounting them."""
    widgets: list = []
    current_subagent: SubAgentBlock | None = None
    pending_tools: dict[str, str] = {}
    sa_pending_tools: dict[str, str] = {}

    for turn in turns:
        turn_ws, current_subagent, sa_pending_tools = _build_turn_widgets(
            turn, current_subagent, pending_tools, sa_pending_tools
        )
        widgets.extend(turn_ws)

    if current_subagent:
        current_subagent.mark_interrupted()

    # A restored session cannot retain live tool executions.
    for w in widgets:
        if isinstance(w, ToolBlock) and w.state == "running":
            w.mark_done("")

    return widgets


def _find_matching_block(
    widgets: list, tool_name: str, call_id: str
) -> "ToolBlock | None":
    """Find the newest matching tool block, preferring its call ID."""
    if call_id:
        for w in reversed(widgets):
            if isinstance(w, ToolBlock) and w.tool_id == call_id:
                return w
    # Older histories may lack call IDs, so fall back to the newest running name.
    for w in reversed(widgets):
        if (
            isinstance(w, ToolBlock)
            and w.tool_name == tool_name
            and w.state == "running"
        ):
            return w
    return None


def _build_turn_widgets(
    turn: dict,
    current_subagent: SubAgentBlock | None,
    pending_tools: dict[str, str],
    sa_pending_tools: dict[str, str],
) -> tuple[list, SubAgentBlock | None, dict[str, str]]:
    """Build one turn's widgets and return its carried sub-agent state."""
    widgets: list = []

    # User/trigger message
    if turn["input_type"] == "user_input":
        widgets.append(UserMessage(turn["input"]))
    else:
        widgets.append(TriggerMessage(turn["input"], turn.get("trigger_content", "")))

    for step_type, data in turn.get("steps", []):
        if step_type == "text":
            text = data if isinstance(data, str) else str(data)
            if text.strip():
                # Markdown preserves selectable rendered history instead of a stream widget.
                widgets.append(Markdown(text))

        elif step_type == "tool_call":
            raw_name = data.get("name", "tool")
            name = _clean_name(raw_name)
            call_id = data.get("call_id", "")
            args = data.get("args", {})
            preview = format_args_preview(name, args)
            detail = format_args_detail(name, args)

            if current_subagent:
                current_subagent.add_tool_line(name, preview)
            else:
                block = ToolBlock(name, preview, call_id, args_detail=detail)
                widgets.append(block)
            if call_id:
                pending_tools[call_id] = name

        elif step_type == "tool_result":
            call_id = data.get("call_id", "")
            name = pending_tools.pop(call_id, _clean_name(data.get("name", "tool")))
            error = data.get("error")
            output = data.get("output", "")
            if output.strip() in ("OK", ""):
                output = ""

            if current_subagent:
                current_subagent.update_tool_line(
                    name, done=not error, error=bool(error)
                )
            else:
                matched = _find_matching_block(widgets, name, call_id)
                if matched is not None:
                    if error:
                        matched.mark_error(str(error))
                    else:
                        matched.mark_done(output)

        elif step_type == "subagent_call":
            # Finalize any leftover sub-agent tools from previous sub-agent
            if current_subagent:
                for tn in list(sa_pending_tools):
                    current_subagent.update_tool_line(tn, done=True)
                sa_pending_tools.clear()
            raw_name = data.get("name", "subagent")
            name = _clean_name(raw_name)
            task = data.get("task", "")
            block = SubAgentBlock(name, sa_task=task)
            current_subagent = block
            widgets.append(block)

        elif step_type == "subagent_result":
            # Mark any remaining sub-agent tools as done
            if current_subagent:
                for tn in list(sa_pending_tools):
                    current_subagent.update_tool_line(tn, done=True)
                sa_pending_tools.clear()
            if current_subagent:
                current_subagent.mark_done(
                    output=data.get("output", ""),
                    tools_used=data.get("tools_used"),
                    turns=data.get("turns", 0),
                    duration=data.get("duration", 0),
                )
                current_subagent = None

        elif step_type == "subagent_tool":
            tool_name = data.get("tool_name", "")
            activity = data.get("activity", "")
            detail = data.get("detail", "")
            if current_subagent:
                if activity == "tool_start":
                    # Pre-mount widgets receive their current state directly.
                    sa_pending_tools[tool_name] = detail[:50]
                    current_subagent.add_tool_line(tool_name, detail[:50])
                elif activity == "tool_done":
                    sa_pending_tools.pop(tool_name, None)
                    current_subagent.update_tool_line(tool_name, done=True)
                elif activity == "tool_error":
                    sa_pending_tools.pop(tool_name, None)
                    current_subagent.update_tool_line(tool_name, done=False, error=True)

        elif step_type == "compact_complete":
            summary = data.get("summary", "") if isinstance(data, dict) else ""
            widgets.append(CompactSummaryBlock(summary, done=True))

        elif step_type == "compact_skipped":
            reason = data.get("reason", "skipped") if isinstance(data, dict) else ""
            widgets.append(
                CompactSummaryBlock(f"(skipped: {reason or 'skipped'})", done=True)
            )

    return widgets, current_subagent, sa_pending_tools


def _clean_name(raw: str) -> str:
    """Remove stored job ID and sub-agent prefixes from a name."""
    if "[" in raw:
        return raw[: raw.index("[")]
    if raw.startswith("agent_"):
        return raw[6:]
    return raw


def _render_turn_to_tui(tui, turn: dict) -> None:
    """Render one historical turn as TUI widgets, preserving interleaving."""
    if turn["input_type"] == "user_input":
        tui.add_user_message(turn["input"])
    else:
        tui.add_trigger_message(turn["input"], turn.get("trigger_content", ""))

    pending_tools: dict[str, str] = {}

    for step_type, data in turn["steps"]:
        if step_type == "text":
            tui.begin_streaming()
            tui.append_stream(data)
            tui.end_streaming()

        elif step_type == "tool_call":
            raw_name = data.get("name", "tool")
            name = _clean_name(raw_name)
            call_id = data.get("call_id", "")
            args = data.get("args", {})
            preview = format_args_preview(name, args)
            tui.add_tool_block(
                name,
                preview,
                call_id,
                args_detail=format_args_detail(name, args),
            )
            if call_id:
                pending_tools[call_id] = name

        elif step_type == "tool_result":
            call_id = data.get("call_id", "")
            name = pending_tools.pop(call_id, _clean_name(data.get("name", "tool")))
            error = data.get("error")
            output = data.get("output", "")
            if output.strip() in ("OK", ""):
                output = ""
            tui.update_tool_block(name, output=output, error=error, tool_id=call_id)

        elif step_type == "subagent_call":
            raw_name = data.get("name", "subagent")
            name = _clean_name(raw_name)
            task = data.get("task", "")
            tui.add_subagent_block(name, task)

        elif step_type == "subagent_result":
            tui.end_subagent_block(
                output=data.get("output", ""),
                tools_used=data.get("tools_used"),
                turns=data.get("turns", 0),
                duration=data.get("duration", 0),
            )

        elif step_type == "subagent_tool":
            tool_name = data.get("tool_name", "")
            activity = data.get("activity", "")
            detail = data.get("detail", "")
            if activity == "tool_start":
                tui.add_tool_block(tool_name, detail[:50])
            elif activity == "tool_done":
                tui.update_tool_block(tool_name)
            elif activity == "tool_error":
                tui.update_tool_block(tool_name, error="error")
        tui.end_streaming()
