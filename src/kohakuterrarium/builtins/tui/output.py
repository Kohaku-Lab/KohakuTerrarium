"""TUI output module: renders to Textual app with Collapsible tool blocks."""

import asyncio
from typing import Any

from textual.containers import VerticalScroll
from textual.widgets import Markdown

from kohakuterrarium.builtins.tui._injection import handle_user_input_injected
from kohakuterrarium.builtins.tui.model_info import handle_session_info
from kohakuterrarium.builtins.tui.reply_submit import submit_reply
from kohakuterrarium.builtins.tui.session import CULL_KEEP, TUISession
from kohakuterrarium.builtins.tui.tool_args import (
    format_args_detail,
    format_args_preview,
)
from kohakuterrarium.builtins.tui.widgets import (
    CompactSummaryBlock,
    LoadOlderButton,
    SubAgentBlock,
    ToolBlock,
    TriggerMessage,
    UserMessage,
)
from kohakuterrarium.builtins.tui.widgets.ui_event_modals import (
    BusAskTextModal,
    BusConfirmModal,
    BusSelectionModal,
)
from kohakuterrarium.core.session import get_session
from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.session.history import (
    dedupe_adjacent_duplicate_events,
    select_live_event_ids,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TUIOutput(BaseOutputModule):
    """Render streamed output, activities, history, and interactive events in Textual."""

    def __init__(self, session_key: str | None = None, **options: Any):
        super().__init__()
        self._session_key = session_key
        self._tui = None
        self._turn_started = False
        self._default_target: str = ""
        self._interactive_screens: dict[str, Any] = {}

    @property
    def _target(self) -> str:
        return self._default_target

    async def _on_start(self) -> None:
        # Engine-managed sessions are wired before startup and must remain the
        # same object used by the mounted application.
        if self._tui is not None:
            logger.debug(
                "TUI output reusing externally-wired session",
                session_key=self._session_key,
            )
            return
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
            self._tui._attention_state(self._target)["processing"] = False
            self._tui._refresh_attention()
        logger.debug("TUI output stopped")

    # -- Processing lifecycle -----------------------------------------------

    async def on_processing_start(self) -> None:
        self._turn_started = False
        if self._tui:
            self._tui.attention_processing_start(self._target)
            self._tui.start_thinking()

    async def on_processing_end(self) -> None:
        if self._tui:
            self._tui.attention_processing_end(self._target)
            self._tui.end_streaming(target=self._target)
            self._tui.stop_thinking()
            self._tui.set_idle()
        self._turn_started = False

    # -- User input ---------------------------------------------------------

    async def on_user_input(self, text: str) -> None:
        # The input widget renders the message before dispatching it here.
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
        """Render an output event or collect its interactive reply."""
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
            case "confirm":
                await self._handle_confirm_event(event)
            case "ask_text":
                await self._handle_ask_text_event(event)
            case "selection":
                await self._handle_selection_event(event)
            case "progress":
                self._handle_progress_event(event)
            case "notification":
                self._handle_notification_event(event)
            case "card":
                self._handle_card_event(event)
            case "ui_supersede":
                event_id = event.id or event.payload.get("event_id")
                self.on_supersede(event_id)
            case _:
                detail = event.content if isinstance(event.content, str) else ""
                self._handle_activity(event.type, detail, event.payload or {})

    async def _handle_confirm_event(self, event: OutputEvent) -> None:
        if self._tui is None or self._tui._app is None:
            return
        await self._tui.wait_ready()
        state = self._tui._attention_state(self._target)
        if event.id in state["pending"]:
            return
        self._tui.attention_pending(event.id, self._target)
        payload = event.payload or {}

        def _build_and_push() -> None:
            # Modal construction requires Textual's active_app context.
            modal = BusConfirmModal(
                prompt=payload.get("prompt", ""),
                detail=payload.get("detail", ""),
                options=payload.get("options", []),
            )

            def _on_dismissed(result: dict | None) -> None:
                if event.id:
                    self._interactive_screens.pop(event.id, None)
                self._submit_modal_reply(event, result, default_action="cancel")

            if event.id:
                self._interactive_screens[event.id] = modal
            self._tui._app.push_screen(modal, _on_dismissed)

        self._tui._safe_call(_build_and_push)

    async def _handle_ask_text_event(self, event: OutputEvent) -> None:
        if self._tui is None or self._tui._app is None:
            return
        await self._tui.wait_ready()
        state = self._tui._attention_state(self._target)
        if event.id in state["pending"]:
            return
        self._tui.attention_pending(event.id, self._target)
        payload = event.payload or {}

        def _build_and_push() -> None:
            modal = BusAskTextModal(
                prompt=payload.get("prompt", ""),
                placeholder=payload.get("placeholder", ""),
                default=payload.get("default", ""),
                multiline=bool(payload.get("multiline", False)),
            )

            def _on_dismissed(result: dict | None) -> None:
                if event.id:
                    self._interactive_screens.pop(event.id, None)
                self._submit_modal_reply(event, result, default_action="cancel")

            if event.id:
                self._interactive_screens[event.id] = modal
            self._tui._app.push_screen(modal, _on_dismissed)

        self._tui._safe_call(_build_and_push)

    async def _handle_selection_event(self, event: OutputEvent) -> None:
        if self._tui is None or self._tui._app is None:
            return
        await self._tui.wait_ready()
        state = self._tui._attention_state(self._target)
        if event.id in state["pending"]:
            return
        self._tui.attention_pending(event.id, self._target)
        payload = event.payload or {}

        def _build_and_push() -> None:
            modal = BusSelectionModal(
                prompt=payload.get("prompt", ""),
                options=payload.get("options", []),
                multi=bool(payload.get("multi", False)),
                default=payload.get("default"),
            )

            def _on_dismissed(result: dict | None) -> None:
                if event.id:
                    self._interactive_screens.pop(event.id, None)
                self._submit_modal_reply(event, result, default_action="cancel")

            if event.id:
                self._interactive_screens[event.id] = modal
            self._tui._app.push_screen(modal, _on_dismissed)

        self._tui._safe_call(_build_and_push)

    def _submit_modal_reply(
        self,
        event: OutputEvent,
        result: dict | None,
        default_action: str,
    ) -> None:
        """Convert a modal result to a reply and submit it to the router."""
        router = getattr(self, "_router", None)
        if router is None or not event.id:
            return
        if result is None:
            reply = UIReply(event_id=event.id, action_id=default_action, values={})
        else:
            reply = UIReply(
                event_id=event.id,
                action_id=result.get("action_id", default_action),
                values=result.get("values", {}),
            )
        try:
            if submit_reply(router, reply):
                self._tui.attention_clear(event.id, self._target)
        except Exception as e:
            logger.exception("submit_reply failed", error=str(e))

    def _handle_progress_event(self, event: OutputEvent) -> None:
        if self._tui is None:
            return
        payload = event.payload or {}
        widget_id = event.update_target or event.id
        if widget_id is None:
            return
        try:
            self._tui.upsert_progress_block(
                widget_id=widget_id,
                label=payload.get("label", "progress"),
                value=payload.get("value"),
                max_value=payload.get("max"),
                indeterminate=bool(payload.get("indeterminate", False)),
                complete=bool(payload.get("complete", False)),
                target=self._target,
            )
        except Exception as e:
            logger.warning("progress render failed", error=str(e), exc_info=True)

    def _handle_notification_event(self, event: OutputEvent) -> None:
        if self._tui is None:
            return
        payload = event.payload or {}
        try:
            self._tui.add_system_notice(
                payload.get("text", ""),
                command=payload.get("title", payload.get("level", "info")),
                target=self._target,
            )
        except Exception as e:
            logger.warning("notification render failed", error=str(e), exc_info=True)

    def _handle_card_event(self, event: OutputEvent) -> None:
        if self._tui is None:
            return
        payload = event.payload or {}
        actions = payload.get("actions") or []
        replyable = event.interactive and any(
            action.get("style") != "link" for action in actions
        )
        if replyable:
            self._tui.attention_pending(event.id, self._target)
        on_action = self._make_card_action_callback() if replyable else None
        try:
            self._tui.add_card_block(
                payload,
                event_id=event.id,
                on_action=on_action,
                target=self._target,
            )
        except Exception as e:
            logger.warning("card render failed", error=str(e), exc_info=True)

    def _make_card_action_callback(self):
        """Create a callback that submits card button replies."""

        def _on_action(event_id: str, action_id: str) -> None:
            router = getattr(self, "_router", None)
            if router is None or not event_id:
                return
            try:
                reply = UIReply(
                    event_id=event_id,
                    action_id=action_id,
                    values={"action_id": action_id},
                )
                if submit_reply(router, reply):
                    self._tui.attention_clear(event_id, self._target)
            except Exception as e:
                logger.exception("card action submit failed", error=str(e))

        return _on_action

    def on_supersede(self, event_id: str | None) -> None:
        if not self._tui:
            return
        self._tui.attention_clear(event_id, self._target)
        screen = self._interactive_screens.pop(event_id, None) if event_id else None
        if screen is not None:
            self._tui._safe_call(screen.dismiss, None)

    def _handle_activity(
        self, activity_type: str, name_detail: str, metadata: dict
    ) -> None:
        if not self._tui:
            return

        name, rest = _parse_detail(name_detail)
        args = metadata.get("args", {})
        job_id = metadata.get("job_id", "")
        t = self._target

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
            case "compact_start" | "compact_complete" | "compact_skipped":
                self._handle_compact_activity(activity_type, t, metadata)
            case "background_result":
                label = metadata.get("label") or metadata.get("job_id", "")
                kind = metadata.get("kind", "tool")
                self._tui.add_trigger_message(
                    f"background {kind} result delivered: {label}", "", target=t
                )
            case "session_info":
                self._handle_session_info(metadata)
            case "job_cancelled":
                self._handle_job_cancelled(t, metadata)
            case "task_promoted":
                # Promotion preserves the existing running-panel entry.
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
            case "command_result":
                self._tui.add_system_notice(
                    name_detail,
                    command=_command_name(metadata),
                    target=t,
                )
            case "command_error":
                self._tui.add_system_notice(
                    name_detail,
                    command=_command_name(metadata),
                    error=True,
                    target=t,
                )
            case "interrupt":
                # Background jobs and sub-agents own their cancellation lifecycle.
                self._tui.end_streaming(target=self._target)
                self._turn_started = False
            case "processing_complete":
                # Background jobs remove their entries when they finish.
                pass
            case "user_input_injected":
                self._handle_user_input_injected(metadata, t)
            case _:
                pass

    # -- Activity handler methods -------------------------------------------

    def _handle_tool_start(
        self, name: str, rest: str, args: dict, job_id: str, t: str, metadata: dict
    ) -> None:
        self._tui.end_streaming(target=self._target)
        self._turn_started = False
        args_preview = format_args_preview(name, args) or rest[:60]
        self._tui.add_tool_block(
            name,
            args_preview,
            job_id,
            target=t,
            args_detail=format_args_detail(name, args),
        )
        is_bg = metadata.get("background", False)
        self._tui.update_running(job_id or name, name, promotable=not is_bg)

    def _handle_tool_done(
        self, name: str, rest: str, job_id: str, t: str, metadata: dict
    ) -> None:
        output = metadata.get("output_preview") or metadata.get("output", rest)
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
                format_args_preview(tool_name, metadata.get("args", {}))
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
        else:
            round_num = metadata.get("round", 0)
            if activity_type == "compact_skipped":
                summary = f"(skipped: {metadata.get('reason', 'skipped')})"
            else:
                summary = metadata.get("summary", "")
            self._tui.update_compact_summary(round_num, summary, target=t)
            self._tui.update_running("compact", "", remove=True)

    def _handle_user_input_injected(self, metadata: dict, target: str) -> None:
        handle_user_input_injected(self._tui, metadata, target)

    def _handle_session_info(self, metadata: dict) -> None:
        handle_session_info(self._tui, self, metadata)

    # -- Resume history -----------------------------------------------------

    async def on_resume(self, events: list[dict]) -> None:
        """Render session history in one race-free widget batch."""
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

        # Textual widgets must be constructed within the application context.
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
                        # Bound initial mount cost while retaining older widgets for
                        # explicit history loading.
                        if len(ws) > CULL_KEEP:
                            older = ws[: len(ws) - CULL_KEEP]
                            mount_ws = ws[-CULL_KEEP:]
                            t = self._default_target or "_default"
                            self._tui.store_older_widgets(t, older)
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
            # Resume must not race subsequent output against an incomplete mount.
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


def _command_name(metadata: dict) -> str:
    """Return a compact slash-command label for command activity notices."""
    raw = str(metadata.get("command", "") or "").strip()
    if not raw:
        return "command"
    name = raw.lstrip("/").split(None, 1)[0]
    return name or "command"


# -- Resume rendering ------------------------------------------------------


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
                "input": evt.get("content", ""),
                "steps": [],
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
