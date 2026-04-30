"""TUI output module: renders to Textual app with Collapsible tool blocks."""

import asyncio
from typing import Any

from textual.containers import VerticalScroll
from textual.widgets import Markdown

from kohakuterrarium.builtins.tui.session import CULL_KEEP, TUISession
from kohakuterrarium.builtins.tui.widgets import (
    CompactSummaryBlock,
    LoadOlderButton,
    SubAgentBlock,
    ToolBlock,
    TriggerMessage,
    UserMessage,
)
from kohakuterrarium.core.session import get_session
from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.session.history import (
    dedupe_adjacent_duplicate_events,
    select_live_event_ids,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TUIOutput(BaseOutputModule):
    """Output module using Textual full-screen TUI with widget-based chat.

    Tool calls render as Collapsible blocks (accordion).
    Sub-agents render as nested Collapsible with child tool blocks.
    Text streams live into a StreamingText widget.

    Config:
        output:
          type: tui
          session_key: my_agent
    """

    def __init__(self, session_key: str | None = None, **options: Any):
        super().__init__()
        self._session_key = session_key
        self._tui = None  # TUISession, set in _on_start
        self._turn_started = False
        self._default_target: str = ""  # Override target tab (for creature outputs)

    @property
    def _target(self) -> str:
        return self._default_target

    async def _on_start(self) -> None:
        session = get_session(self._session_key)
        if session.tui is None:
            session.tui = TUISession(
                agent_name=session.key if session.key != "__default__" else "agent",
            )
        self._tui = session.tui
        logger.debug("TUI output started", session_key=self._session_key)

    async def _on_stop(self) -> None:
        if self._tui:
            self._tui.end_streaming(target=self._target)
        logger.debug("TUI output stopped")

    # -- Processing lifecycle -----------------------------------------------

    async def on_processing_start(self) -> None:
        self._turn_started = False
        if self._tui:
            self._tui.start_thinking()

    async def on_processing_end(self) -> None:
        if self._tui:
            self._tui.end_streaming(target=self._target)
            self._tui.stop_thinking()
            self._tui.set_idle()
        self._turn_started = False

    # -- User input ---------------------------------------------------------

    async def on_user_input(self, text: str) -> None:
        # User message is already added by AgentTUI.on_input_submitted
        pass

    # -- Text streaming -----------------------------------------------------

    async def write(self, content: str) -> None:
        if self._tui and content:
            self._ensure_turn()
            self._tui.append_stream(content, target=self._target)

    async def write_stream(self, chunk: str) -> None:
        if self._tui and chunk:
            self._ensure_turn()
            self._tui.append_stream(chunk, target=self._target)

    async def flush(self) -> None:
        pass

    def reset(self) -> None:
        if self._tui:
            self._tui.end_streaming(target=self._target)
        self._turn_started = False

    def _ensure_turn(self) -> None:
        if not self._turn_started and self._tui:
            self._tui.begin_streaming(target=self._target)
            self._turn_started = True

    # -- Activity rendering -------------------------------------------------

    def on_activity(self, activity_type: str, detail: str) -> None:
        self._handle_activity(activity_type, detail, {})

    def on_activity_with_metadata(
        self, activity_type: str, detail: str, metadata: dict
    ) -> None:
        self._handle_activity(activity_type, detail, metadata)

    async def emit(self, event: OutputEvent) -> None:
        """Native event consumer.

        Each event type maps to the same widget call the legacy hooks
        invoke, with byte-identical state changes.
        """
        match event.type:
            case "text":
                content = event.content
                if isinstance(content, str):
                    await self.write_stream(content)
            case "processing_start":
                await self.on_processing_start()
            case "processing_end":
                await self.on_processing_end()
            case "user_input":
                content = event.content
                if isinstance(content, str):
                    await self.on_user_input(content)
            case "assistant_image":
                payload = event.payload
                self.on_assistant_image(
                    payload["url"],
                    detail=payload.get("detail", "auto"),
                    source_type=payload.get("source_type"),
                    source_name=payload.get("source_name"),
                    revised_prompt=payload.get("revised_prompt"),
                )
            case "resume_batch":
                await self.on_resume(event.payload.get("events", []))
            case _:
                detail = event.content if isinstance(event.content, str) else ""
                self._handle_activity(event.type, detail, event.payload or {})

    def _handle_activity(
        self, activity_type: str, name_detail: str, metadata: dict
    ) -> None:
        if not self._tui:
            return

        name, rest = _parse_detail(name_detail)
        args = metadata.get("args", {})
        job_id = metadata.get("job_id", "")
        t = self._target  # target tab for this output

        match activity_type:
            case "tool_start":
                self._handle_tool_start(name, rest, args, job_id, t, metadata)
            case "tool_done":
                self._handle_tool_done(name, rest, job_id, t, metadata)
            case "tool_error":
                self._handle_tool_error(name, rest, job_id, t)
            case "subagent_start":
                self._handle_subagent_start(name, rest, job_id, t, metadata)
            case "subagent_done":
                self._handle_subagent_done(name, rest, job_id, t, metadata)
            case "subagent_error":
                self._handle_subagent_error(name, rest, job_id, t)
            case s if s.startswith("subagent_tool_"):
                self._handle_subagent_tool(s, name, rest, t, metadata)
            case "trigger_fired":
                self._handle_trigger_fired(name, t, metadata)
            case "token_usage":
                self._handle_token_usage(metadata)
            case "compact_start" | "compact_complete":
                self._handle_compact_activity(activity_type, t, metadata)
            case "session_info":
                self._handle_session_info(metadata)
            case "job_cancelled":
                self._handle_job_cancelled(t, metadata)
            case "task_promoted":
                # Task promoted to background — keep in running panel (now bg)
                pass
            case "context_cleared":
                msgs_cleared = metadata.get("messages_cleared", 0)
                self._tui.add_system_notice(
                    f"Context cleared ({msgs_cleared} messages removed)",
                    command="Clear",
                    target=t,
                )
            case "processing_error":
                error_type = metadata.get("error_type", "Error")
                error_msg = metadata.get("error", rest)
                self._tui.add_error_block(error_type, error_msg, target=t)
            case "interrupt":
                self._tui.end_streaming(target=self._target)
                # Only end streaming — do NOT clear running panel or
                # interrupt sub-agents. Background jobs have their own
                # lifecycle and are cancelled individually.
                self._turn_started = False
            case "processing_complete":
                # Don't clear running panel — background jobs may still be
                # running. They remove themselves individually when done.
                pass
            case _:
                pass

    # -- Activity handler methods -------------------------------------------

    def _handle_tool_start(
        self, name: str, rest: str, args: dict, job_id: str, t: str, metadata: dict
    ) -> None:
        self._tui.end_streaming(target=self._target)
        self._turn_started = False
        args_preview = _format_args_preview(name, args) or rest[:60]
        self._tui.add_tool_block(name, args_preview, job_id, target=t)
        is_bg = metadata.get("background", False)
        self._tui.update_running(job_id or name, name, promotable=not is_bg)

    def _handle_tool_done(
        self, name: str, rest: str, job_id: str, t: str, metadata: dict
    ) -> None:
        output = metadata.get("output", rest)
        self._tui.update_tool_block(name, output=output, tool_id=job_id, target=t)
        self._tui.update_running(job_id or name, name, remove=True)

    def _handle_tool_error(self, name: str, rest: str, job_id: str, t: str) -> None:
        self._tui.update_tool_block(name, error=rest, tool_id=job_id, target=t)
        self._tui.update_running(job_id or name, name, remove=True)

    def _handle_subagent_start(
        self, name: str, rest: str, job_id: str, t: str, metadata: dict
    ) -> None:
        self._tui.end_streaming(target=self._target)
        self._turn_started = False
        task = metadata.get("task", rest)
        self._tui.add_subagent_block(name, task, job_id, target=t)
        self._tui.update_running(job_id or name, f"[sub] {name}")

    def _handle_subagent_done(
        self, name: str, rest: str, job_id: str, t: str, metadata: dict
    ) -> None:
        self._tui.end_subagent_block(
            output=metadata.get("result", rest),
            tools_used=metadata.get("tools_used"),
            turns=metadata.get("turns", 0),
            duration=metadata.get("duration", 0),
            target=t,
            agent_id=job_id,
        )
        self._tui.update_running(job_id or name, name, remove=True)

    def _handle_subagent_error(self, name: str, rest: str, job_id: str, t: str) -> None:
        self._tui.end_subagent_block(error=rest, target=t, agent_id=job_id)
        self._tui.update_running(job_id or name, name, remove=True)

    def _handle_job_cancelled(self, t: str, metadata: dict) -> None:
        """Remove the cancelled job from the running panel and mark its widget."""
        job_id = metadata.get("job_id", "")
        job_name = metadata.get("job_name", "")
        if job_id:
            self._tui.update_running(job_id, job_name, remove=True)
        # Also mark any tool or sub-agent block as cancelled
        self._tui.update_tool_block(
            job_name,
            error="Background task was cancelled by user.",
            tool_id=job_id,
            target=t,
        )
        self._tui.end_subagent_block(
            error="Background sub-agent was cancelled by user.",
            target=t,
            agent_id=job_id,
        )
        self._tui.add_system_notice(
            f"Cancelled: {job_name}", command="cancel", target=t
        )

    def _handle_subagent_tool(
        self, activity_type: str, name: str, rest: str, t: str, metadata: dict
    ) -> None:
        tool_name = metadata.get("tool", "")
        sa_job_id = metadata.get("job_id", "")
        sub_activity = activity_type.replace("subagent_", "")
        sub_detail = metadata.get("detail", rest)

        if sub_activity == "tool_start":
            sub_args = (
                _format_args_preview(tool_name, metadata.get("args", {}))
                or sub_detail[:60]
            )
            self._tui.add_tool_block(tool_name, sub_args, target=t, agent_id=sa_job_id)
        elif sub_activity == "tool_done":
            self._tui.update_tool_block(
                tool_name, output=sub_detail, target=t, agent_id=sa_job_id
            )
        elif sub_activity == "tool_error":
            self._tui.update_tool_block(
                tool_name, error=sub_detail, target=t, agent_id=sa_job_id
            )

    def _handle_trigger_fired(self, name: str, t: str, metadata: dict) -> None:
        self._tui.end_streaming(target=self._target)
        self._turn_started = False
        channel = metadata.get("channel", "")
        sender = metadata.get("sender", "")
        content = metadata.get("content", "")
        label = f"[{channel}] {sender}" if channel else name
        self._tui.add_trigger_message(label, content, target=t)

    def _handle_token_usage(self, metadata: dict) -> None:
        prompt = metadata.get("prompt_tokens", 0)
        completion = metadata.get("completion_tokens", 0)
        total = metadata.get("total_tokens", 0)
        cached = metadata.get("cached_tokens", 0)
        self._tui.update_token_usage(prompt, completion, total, cached)

    def _handle_compact_activity(
        self, activity_type: str, t: str, metadata: dict
    ) -> None:
        if activity_type == "compact_start":
            self._tui.end_streaming(target=t)
            self._turn_started = False
            round_num = metadata.get("round", 0)
            self._tui.add_compact_summary(round_num, "(compacting...)", target=t)
            self._tui.update_running("compact", "compacting context")
        else:  # compact_complete
            round_num = metadata.get("round", 0)
            summary = metadata.get("summary", "")
            self._tui.update_compact_summary(round_num, summary, target=t)
            self._tui.update_running("compact", "", remove=True)

    def _handle_session_info(self, metadata: dict) -> None:
        session_id = metadata.get("session_id", "")
        model = metadata.get("model", "")
        agent_name = metadata.get("agent_name", "")
        max_context = metadata.get("max_context", 0)
        compact_threshold = metadata.get("compact_threshold", 0)
        self._tui.update_session_info(session_id, model, agent_name)
        if max_context:
            self._tui.set_context_limits(max_context, compact_threshold)

    # -- Resume history -----------------------------------------------------

    async def on_resume(self, events: list[dict]) -> None:
        """Render session history as proper widgets.

        Builds all widgets synchronously, then mounts them in one batch
        to avoid race conditions with deferred _safe_call.
        """
        if not self._tui or not events:
            return

        await self._tui.wait_ready()

        turns = _group_into_turns(events)

        # Restore cumulative token usage from event history
        total_in = 0
        total_out = 0
        total_cached = 0
        last_prompt = 0
        for _, data in _iter_all_steps(turns):
            if isinstance(data, dict) and data.get("type") == "token_usage":
                total_in += data.get("prompt_tokens", 0)
                total_out += data.get("completion_tokens", 0)
                total_cached += data.get("cached_tokens", 0)
                last_prompt = data.get("prompt_tokens", 0)
        if total_in or total_out:
            self._tui.restore_token_usage(
                total_in, total_out, last_prompt, total_cached
            )

        # Build and mount widgets on the Textual thread.
        # Widgets MUST be created inside the app context (Textual requirement).
        if turns and self._tui._app and self._tui._app.is_running:
            app = self._tui._app
            done_event = asyncio.Event()

            target = self._default_target or ""
            scroll_id = self._tui._get_chat_scroll_id(target)

            def _do_build_and_mount():
                async def _inner():
                    try:
                        ws = _build_resume_widgets(turns)
                        chat = app.query_one(f"#{scroll_id}", VerticalScroll)
                        # Only mount the last N widgets; store older for "Load older"
                        if len(ws) > CULL_KEEP:
                            older = ws[: len(ws) - CULL_KEEP]
                            mount_ws = ws[-CULL_KEEP:]
                            # Store older widgets on TUISession for loading later
                            t = self._default_target or "_default"
                            self._tui.store_older_widgets(t, older)
                            # Add "Load older" button
                            btn = LoadOlderButton(len(older))
                            mount_ws = [btn] + mount_ws
                            ws = mount_ws
                        await chat.mount_all(ws)
                        chat.scroll_end(animate=False)
                    except Exception as e:
                        logger.warning("Resume mount failed", error=str(e))
                    finally:
                        done_event.set()

                asyncio.ensure_future(_inner())

            app.call_later(_do_build_and_mount)
            # Wait for mount to complete before returning
            await asyncio.wait_for(done_event.wait(), timeout=10.0)


# -- Helpers ---------------------------------------------------------------


def _parse_detail(detail: str) -> tuple[str, str]:
    """Extract [name] prefix, strip job ID suffix."""
    if detail.startswith("["):
        try:
            end = detail.index("]", 1)
            raw_name = detail[1:end]
            rest = detail[end + 2 :]
            if "[" in raw_name:
                raw_name = raw_name[: raw_name.index("[")]
            return raw_name, rest
        except (ValueError, IndexError):
            pass
    return "unknown", detail


def _format_args_preview(tool_name: str, args: dict) -> str:
    if not args:
        return ""
    match tool_name:
        case "bash":
            return args.get("command", "")[:60]
        case "read":
            return args.get("path", "")[:60]
        case "write" | "edit":
            return args.get("file_path", args.get("path", ""))[:60]
        case "glob":
            return args.get("pattern", "")[:60]
        case "grep":
            p = args.get("pattern", "")
            path = args.get("path", "")
            return f'"{p}" {path}'.strip()[:60]
        case "send_message" | "terrarium_send":
            return f"-> {args.get('channel', '')}"
        case "terrarium_observe":
            return f"<- {args.get('channel', '')}"
        case "info":
            return args.get("name", args.get("topic", ""))[:50]
        case _:
            for k, v in args.items():
                if k == "content" or k.startswith("_"):
                    continue
                return f"{k}={str(v)[:40]}"
            return ""


# -- Resume rendering ------------------------------------------------------


def _group_into_turns(events: list[dict]) -> list[dict]:
    """Group events into turns. Each turn has an ordered list of steps
    that preserves the interleaving of text and tool calls."""
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
                "input": evt.get("content", ""),
                "steps": [],  # ordered list of (type, data) preserving interleaving
            }
        elif etype == "trigger_fired":
            if current:
                turns.append(current)
            ch = evt.get("channel", "")
            sender = evt.get("sender", "")
            content = evt.get("content", "")
            current = {
                "input_type": "trigger",
                "input": f"[{ch}] {sender}",
                "trigger_content": content,
                "steps": [],
            }
        elif etype in ("compact_start", "compact_complete"):
            # Compact events may fire between turns (background task)
            target = current if current else (turns[-1] if turns else None)
            if target:
                target["steps"].append((etype, evt))
        elif current is not None:
            if etype in ("text", "text_chunk"):
                # Merge consecutive text into one step. ``text_chunk``
                # events are Wave C's per-chunk streaming format; replay
                # treats them identically to legacy ``text`` events.
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
    """Iterate all (step_type, data) across all turns."""
    for turn in turns:
        for step in turn.get("steps", []):
            yield step


def _build_resume_widgets(turns: list[dict]) -> list:
    """Build all resume widgets synchronously (no mounting, no deferral)."""
    widgets: list = []
    current_subagent: SubAgentBlock | None = None
    pending_tools: dict[str, str] = {}
    sa_pending_tools: dict[str, str] = {}  # sub-agent internal tools still "running"

    for turn in turns:
        turn_ws, current_subagent, sa_pending_tools = _build_turn_widgets(
            turn, current_subagent, pending_tools, sa_pending_tools
        )
        widgets.extend(turn_ws)

    # Mark interrupted sub-agents
    if current_subagent:
        current_subagent.mark_interrupted()

    # Mark any remaining "running" tools as done (session ended, they can't
    # still be running; their completion events were likely lost or missing)
    for w in widgets:
        if isinstance(w, ToolBlock) and w.state == "running":
            w.mark_done("")

    return widgets


def _find_matching_block(
    widgets: list, tool_name: str, call_id: str
) -> "ToolBlock | None":
    """Find a ToolBlock matching the given call_id or tool name.

    Searches in reverse order. Tries call_id first, then falls back
    to matching by name among still-running tools.
    """
    if call_id:
        for w in reversed(widgets):
            if isinstance(w, ToolBlock) and w.tool_id == call_id:
                return w
    # Fallback: match by name among still-running tools
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
    """Build widgets for a single turn.

    Returns (widgets, current_subagent, sa_pending_tools) so the caller
    can carry sub-agent state across turns.
    """
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
                # Use Textual Markdown widget (selectable text)
                widgets.append(Markdown(text))

        elif step_type == "tool_call":
            raw_name = data.get("name", "tool")
            name = _clean_name(raw_name)
            call_id = data.get("call_id", "")
            args = data.get("args", {})
            preview = _format_args_preview(name, args)

            if current_subagent:
                current_subagent.add_tool_line(name, preview)
            else:
                block = ToolBlock(name, preview, call_id)
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
                    # Build the line with its final state directly
                    # (don't add then update, since we're pre-mount)
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

    return widgets, current_subagent, sa_pending_tools


def _clean_name(raw: str) -> str:
    """Strip '[job_id' suffix from stored names. 'info[6f887a' -> 'info'."""
    if "[" in raw:
        return raw[: raw.index("[")]
    # Also strip 'agent_' prefix from sub-agent names: 'agent_explore[...' -> 'explore'
    if raw.startswith("agent_"):
        return raw[6:]
    return raw


def _render_turn_to_tui(tui, turn: dict) -> None:
    """Render one historical turn as TUI widgets, preserving interleaving."""
    if turn["input_type"] == "user_input":
        tui.add_user_message(turn["input"])
    else:
        tui.add_trigger_message(turn["input"], turn.get("trigger_content", ""))

    pending_tools: dict[str, str] = {}  # call_id -> clean_name

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
            preview = _format_args_preview(name, args)
            tui.add_tool_block(name, preview, call_id)
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
