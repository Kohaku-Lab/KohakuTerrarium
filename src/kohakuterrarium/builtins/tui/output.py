"""TUI output module: renders to Textual app with Collapsible tool blocks."""

import asyncio
from typing import Any

from textual.containers import VerticalScroll

from kohakuterrarium.builtins.tui import attention
from kohakuterrarium.builtins.tui._injection import handle_user_input_injected
from kohakuterrarium.builtins.tui.model_info import handle_session_info
from kohakuterrarium.builtins.tui.reply_submit import submit_reply
from kohakuterrarium.builtins.tui.resume import (
    _build_resume_widgets,
    _group_into_turns,
    _iter_all_steps,
)
from kohakuterrarium.builtins.tui.session import CULL_KEEP, TUISession
from kohakuterrarium.builtins.tui.tool_args import (
    format_args_detail,
    format_args_preview,
)
from kohakuterrarium.builtins.tui.widgets import LoadOlderButton
from kohakuterrarium.builtins.tui.widgets.ui_event_modals import (
    BusAskTextModal,
    BusConfirmModal,
    BusSelectionModal,
)
from kohakuterrarium.core.session import get_session
from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Bounded wait for the resume widget mount; the mount itself finishes in the
# background when this fires so a slow mount cannot kill the input loop.
RESUME_MOUNT_TIMEOUT = 10.0


class TUIOutput(BaseOutputModule):
    """Render streamed output, activities, history, and interactive events in Textual."""

    def __init__(self, session_key: str | None = None, **options: Any):
        super().__init__()
        self._session_key = session_key
        self._tui_session: TUISession | None = None
        # Resume events that arrived before the TUI session was wired; the
        # agent clears its pending copy right after emitting, so losing this
        # buffer loses the history permanently.
        self._buffered_resume_events: list[dict] = []
        # In-flight replay of the buffer, scheduled by the wiring flush;
        # live rendering awaits it so history mounts before newer output.
        self._pending_resume_task: asyncio.Task | None = None
        self._turn_started = False
        self._default_target: str = ""
        self._interactive_screens: dict[str, Any] = {}

    @property
    def _tui(self) -> TUISession | None:
        return self._tui_session

    @_tui.setter
    def _tui(self, session: TUISession | None) -> None:
        # Engine surfaces (terrarium.engine_cli*) bind the session directly
        # onto started outputs; replay any resume batch buffered until now.
        self._tui_session = session
        if session is not None:
            self._flush_buffered_resume()

    async def _on_start(self) -> None:
        # Engine-managed sessions are wired before startup and must remain the
        # same object used by the mounted application.
        if self._tui is not None:
            logger.debug(
                "TUI output reusing externally-wired session",
                session_key=self._session_key,
            )
            self._flush_buffered_resume()
            return
        session = get_session(self._session_key)
        if session.tui is None:
            session.tui = TUISession(
                agent_name=session.key if session.key != "__default__" else "agent",
            )
        self._tui = session.tui
        logger.debug("TUI output started", session_key=self._session_key)

    def _flush_buffered_resume(self) -> None:
        """Replay resume events buffered before the TUI session was wired."""
        buffered, self._buffered_resume_events = self._buffered_resume_events, []
        if not buffered or self._tui_session is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Binding outside a running loop cannot render yet; restore so a
            # later bind or module start replays the history.
            self._buffered_resume_events = buffered
            return
        self._pending_resume_task = asyncio.ensure_future(self.on_resume(buffered))

    async def _await_resume_replay(self) -> None:
        """Serialize rendering behind an in-flight buffered resume replay.

        The wiring flush cannot await (a property setter is sync), so the
        replay runs as a task. Live events must not interleave with it —
        widgets mounted after newer output would render the transcript out
        of order — so every rendering entry point except ``resume_batch``
        itself drains the pending task first.
        """
        task = self._pending_resume_task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Buffered resume replay failed", exc_info=True)
        finally:
            if self._pending_resume_task is task:
                self._pending_resume_task = None

    @property
    def _target(self) -> str:
        return self._default_target

    async def _on_stop(self) -> None:
        if self._tui:
            self._tui.end_streaming(target=self._target)
            attention.processing_cancel(self._tui, self._target)
        logger.debug("TUI output stopped")

    # -- Processing lifecycle -----------------------------------------------

    async def on_processing_start(self) -> None:
        self._turn_started = False
        if self._tui:
            attention.processing_start(self._tui, self._target)
            self._tui.start_thinking()

    async def on_processing_end(self) -> None:
        if self._tui:
            attention.processing_end(self._tui, self._target)
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
        # ``resume_batch`` IS the replay path; everything else must wait for
        # an in-flight buffered replay so history mounts before newer output.
        if event.type != "resume_batch":
            await self._await_resume_replay()
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
        if attention.is_pending(self._tui, event.id, self._target):
            return
        attention.add_pending(self._tui, event.id, self._target)
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
        if attention.is_pending(self._tui, event.id, self._target):
            return
        attention.add_pending(self._tui, event.id, self._target)
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
        if attention.is_pending(self._tui, event.id, self._target):
            return
        attention.add_pending(self._tui, event.id, self._target)
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
                attention.clear(self._tui, event.id, self._target)
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
            attention.add_pending(self._tui, event.id, self._target)
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
                    attention.clear(self._tui, event_id, self._target)
            except Exception as e:
                logger.exception("card action submit failed", error=str(e))

        return _on_action

    def on_supersede(self, event_id: str | None) -> None:
        if not self._tui:
            return
        attention.clear(self._tui, event_id, self._target)
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
            case "drive_turn":
                self._handle_drive_turn(t, metadata)
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

    def _handle_drive_turn(self, t: str, metadata: dict) -> None:
        kind = metadata.get("drive_kind") or "drive"
        label = (
            f"drive turn · {kind} {metadata.get('drive_id', '')} "
            f"({metadata.get('delivery_reason', '')})"
        )
        self._tui.add_trigger_message(label, metadata.get("objective", ""), target=t)

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
        if not events:
            return
        if self._tui is None:
            # The resume_batch can race creature startup before the engine
            # wires this output; the agent clears its pending copy right
            # after emitting, so buffer for replay on bind instead of
            # silently dropping the history.
            logger.debug("TUI not wired; buffering resume events", count=len(events))
            self._buffered_resume_events.extend(events)
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
            try:
                # Resume must not race subsequent output against an incomplete
                # mount, but a slow mount must not kill the input loop either.
                await asyncio.wait_for(done_event.wait(), timeout=RESUME_MOUNT_TIMEOUT)
            except asyncio.TimeoutError:
                # The mount task finishes in the background regardless.
                logger.debug(
                    "Resume mount wait timed out",
                    session_key=self._session_key,
                    scroll_id=scroll_id,
                )


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
