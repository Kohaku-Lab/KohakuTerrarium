"""Share Textual UI state between input and output modules."""

import asyncio
from typing import Any, TypeVar

from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from kohakuterrarium.builtins.tui.app import IDLE_STATUS, AgentTUI, _safe_id
from kohakuterrarium.builtins.tui.model_info import TabModelRegistryMixin
from kohakuterrarium.builtins.tui.widgets import (
    CompactSummaryBlock,
    ConfirmModal,
    LoadOlderButton,
    RunningPanel,
    ScratchpadPanel,
    SelectionModal,
    SessionInfoPanel,
    StreamingText,
    SubAgentBlock,
    SystemNotice,
    TerrariumPanel,
    ToolBlock,
    TriggerMessage,
    UserMessage,
)
from kohakuterrarium.builtins.tui.widgets.bus_blocks import CardBlock, ProgressBlock
from kohakuterrarium.builtins.tui.widgets.model_picker_modal import ModelPickerModal
from kohakuterrarium.builtins.tui.widgets.modules_modal import (
    ModuleEditModal,
    ModulesModal,
    _list_modules,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_CHAT_WIDGETS = 80
DEFAULT_CULL_KEEP = 50
DEFAULT_LOAD_BATCH = 30
# Shared with output history restore so live and resumed chats keep the same window.
CULL_KEEP = DEFAULT_CULL_KEEP
_T = TypeVar("_T")


class TUISession(TabModelRegistryMixin):
    """Coordinate tab-routed widgets and lifecycle state shared by TUI input/output."""

    def __init__(
        self,
        agent_name: str = "agent",
        max_chat_widgets: int = DEFAULT_MAX_CHAT_WIDGETS,
        cull_keep: int = DEFAULT_CULL_KEEP,
        load_batch: int = DEFAULT_LOAD_BATCH,
    ):
        self.agent_name = agent_name
        self.running = False
        self._app: AgentTUI | None = None
        self._stop_event = asyncio.Event()
        # Streaming widgets are keyed by target because tabs may stream concurrently.
        self._streaming_widgets: dict[str, StreamingText] = {}
        # Target and job IDs disambiguate parallel sub-agents with identical names.
        self._current_subagents: dict[str, dict[str, SubAgentBlock]] = {}
        self._terrarium_tabs: list[str] | None = None
        self._active_target: str = ""
        self._max_chat_widgets = max_chat_widgets
        self._cull_keep = cull_keep
        self._load_batch = load_batch
        # Older widgets remain ordered so batches can be prepended chronologically.
        self._older_widgets: dict[str, list] = {}
        self._culled_count: dict[str, int] = {}
        # Callbacks receive (job_id, job_name) for cancellation and job_id for
        # background promotion.
        self.on_cancel_job: Any = None
        self.on_promote_job: Any = None
        # Modal screens mutate the live agent without re-entering slash commands.
        self.host_agent: Any = None
        # Duck typing keeps this package independent of terrarium.service.
        self.engine: Any = None
        self.drive_service: Any = None
        # Terrarium actions resolve against the active tab; standalone mode falls
        # back to the host agent.
        self.resolve_tab_agent: Any = None
        # Target-keyed state keeps sibling creature events from replacing the
        # active tab's model and context details.
        self._model_by_target: dict[str, str] = {}
        self._context_by_target: dict[str, tuple[int, int]] = {}
        self._command_hint_watches: set[tuple[str, int]] = set()
        self.command_hint_fallback: dict[str, Any] = {}
        # Buffer mutations until Textual mounts so startup events are not lost.
        self._pending_safe_calls: list[tuple[Any, tuple[Any, ...]]] = []
        self._pending_modal_defaults: dict[asyncio.Future[Any], Any] = {}

    def set_terrarium_tabs(self, tabs: list[str]) -> None:
        """Configure terrarium tabs and reconcile a running application."""
        normalized = list(dict.fromkeys(tabs))
        active = self.get_active_tab() if self._app else self._active_target
        self._terrarium_tabs = normalized
        self._active_target = (
            active if active in normalized else normalized[0] if normalized else ""
        )
        if not self._app:
            return
        if not self._app.is_running:
            self._app._terrarium_tabs = normalized
            return
        self._safe_call(self._app.reconcile_terrarium_tabs, normalized)

    def set_active_target(self, target: str) -> None:
        """Set the tab that receives new output widgets."""
        self._active_target = target

    def get_active_tab(self) -> str:
        """Return the currently visible tab."""
        if self._app:
            return self._app.get_active_tab_name()
        return self._active_target

    def _safe_call(self, fn: Any, *args: Any) -> None:
        # Startup callbacks must wait for Textual's mounted application context.
        if not self._app:
            return
        if not self._app.is_running:
            self._pending_safe_calls.append((fn, args))
            return
        if self._pending_safe_calls:
            pending = self._pending_safe_calls[:]
            self._pending_safe_calls.clear()
            for pfn, pargs in pending:
                try:
                    self._app.call_later(pfn, *pargs)
                except Exception:  # pragma: no cover - shutdown race
                    # A pending callback may target a widget removed during shutdown.
                    pass
        try:
            self._app.call_later(fn, *args)
        except Exception as e:
            _ = e
            # Some callers originate outside Textual's loop; use its thread-safe
            # scheduler when message-queue scheduling is unavailable.
            try:
                self._app.call_from_thread(fn, *args)
            except Exception as e:
                logger.warning("TUI safe_call failed", error=str(e), exc_info=True)

    def _get_chat_scroll_id(self, target: str = "") -> str:
        """Return the chat scroll widget ID for a target."""
        if not self._terrarium_tabs:
            return "chat-scroll"
        t = target or self._active_target
        return f"chat-{_safe_id(t)}" if t else "chat-scroll"

    def _safe_mount(self, widget: Any, scroll: bool = True, target: str = "") -> None:
        if not self._app or not self._app.is_running:
            return
        scroll_id = self._get_chat_scroll_id(target)
        target_key = target or self._active_target or "_default"

        def _do():
            try:
                chat = self._app.query_one(f"#{scroll_id}", VerticalScroll)
                chat.mount(widget)
                if scroll:
                    chat.scroll_end(animate=False)
                self._cull_chat_widgets(chat, target_key)
            except Exception as e:
                logger.warning("TUI safe_mount failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def _cull_chat_widgets(self, chat: VerticalScroll, target: str) -> None:
        """Cull old widgets while retaining the configured recent window."""
        children = list(chat.children)
        if len(children) <= self._max_chat_widgets:
            return

        remove_count = len(children) - self._cull_keep
        to_remove = children[:remove_count]

        self._culled_count[target] = self._culled_count.get(target, 0) + remove_count

        for w in to_remove:
            w.remove()

        self._update_load_older_button(chat, target)

    def _update_load_older_button(self, chat: VerticalScroll, target: str) -> None:
        """Update the control that represents hidden chat history."""
        available = len(self._older_widgets.get(target, []))
        culled = self._culled_count.get(target, 0)
        total_hidden = available + culled

        if total_hidden <= 0:
            return

        for child in list(chat.children):
            if isinstance(child, LoadOlderButton):
                child.remove()
                break

        if available > 0:
            btn = LoadOlderButton(available)
            first = list(chat.children)
            if first:
                chat.mount(btn, before=first[0])
            else:
                chat.mount(btn)
        elif culled > 0:
            header = Static(
                f"[{culled} earlier messages not available]",
                classes="cull-header",
            )
            first = list(chat.children)
            if first:
                chat.mount(header, before=first[0])

    def store_older_widgets(self, target: str, widgets: list) -> None:
        """Store truncated history widgets for later loading."""
        self._older_widgets[target] = widgets

    def load_older_batch(self, target: str = "") -> None:
        """Prepend one batch of stored history widgets to the chat."""
        target = target or self._active_target or "_default"
        stored = self._older_widgets.get(target, [])
        if not stored:
            return

        scroll_id = self._get_chat_scroll_id(target)
        batch_size = self._load_batch
        batch = stored[-batch_size:]
        self._older_widgets[target] = (
            stored[:-batch_size] if batch_size < len(stored) else []
        )

        def _do():
            try:
                chat = self._app.query_one(f"#{scroll_id}", VerticalScroll)
                for child in list(chat.children):
                    if isinstance(child, LoadOlderButton):
                        child.remove()
                        break
                first = list(chat.children)
                if first:
                    for w in reversed(batch):
                        chat.mount(w, before=first[0])
                else:
                    for w in batch:
                        chat.mount(w)
                self._update_load_older_button(chat, target)
            except Exception as e:
                logger.warning(
                    "TUI load_older_batch failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def add_user_message(self, text: str, target: str = "") -> None:
        self._safe_mount(UserMessage(text), target=target)

    def add_system_notice(
        self, text: str, command: str = "", error: bool = False, target: str = ""
    ) -> None:
        """Add a non-collapsible system notice."""
        self._safe_mount(
            SystemNotice(text, command=command, error=error), target=target
        )

    def add_error_block(
        self, error_type: str, error_msg: str, target: str = ""
    ) -> None:
        """Add a prominent processing error notice."""
        text = f"{error_type}: {error_msg}"
        self.add_system_notice(
            text, command="Processing Error", error=True, target=target
        )

    def add_trigger_message(
        self, label: str, content: str = "", target: str = ""
    ) -> None:
        self._safe_mount(TriggerMessage(label, content), target=target)

    def add_card_block(
        self,
        payload: dict,
        event_id: str | None = None,
        on_action=None,
        target: str = "",
    ) -> None:
        """Mount a card event with optional interactive actions."""
        self._safe_mount(
            CardBlock(payload, event_id=event_id, on_action=on_action),
            target=target,
        )

    def upsert_progress_block(
        self,
        widget_id: str,
        label: str,
        value: float | int | None = None,
        max_value: float | int | None = None,
        indeterminate: bool = False,
        complete: bool = False,
        target: str = "",
    ) -> None:
        """Create or update the progress block identified by ``widget_id``."""
        progress_map: dict[str, ProgressBlock]
        progress_map = getattr(self, "_progress_blocks", None) or {}
        existing = progress_map.get(widget_id)
        if existing is not None:
            try:
                existing.update_progress(
                    label=label,
                    value=value,
                    max_value=max_value,
                    indeterminate=indeterminate,
                    complete=complete,
                )
            except Exception as e:
                logger.warning(
                    "ProgressBlock update failed", error=str(e), exc_info=True
                )
            return

        block = ProgressBlock(
            widget_id=widget_id,
            label=label,
            value=value,
            max_value=max_value,
            indeterminate=indeterminate,
        )
        progress_map[widget_id] = block
        self._progress_blocks = progress_map
        self._safe_mount(block, target=target)
        if complete:
            try:
                block.update_progress(
                    label=label,
                    value=value,
                    max_value=max_value,
                    indeterminate=indeterminate,
                    complete=True,
                )
            except Exception:
                # Completion may arrive before the newly mounted progress widget.
                pass

    def add_compact_summary(
        self, round_num: int, summary: str, target: str = ""
    ) -> None:
        """Add a compact summary accordion to the chat."""
        block = CompactSummaryBlock(summary)
        self._last_compact_block = block
        self._safe_mount(block, target=target)

    def update_compact_summary(
        self, round_num: int, summary: str, target: str = ""
    ) -> None:
        """Complete the current compact summary block."""
        block = getattr(self, "_last_compact_block", None)
        if block:

            def _do():
                try:
                    block.mark_done(summary)
                except Exception as e:
                    logger.warning(
                        "TUI update_compact_summary failed", error=str(e), exc_info=True
                    )

            self._safe_call(_do)
        else:
            self.add_compact_summary(round_num, summary, target=target)

    def update_token_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        """Accumulate one call's token usage in the session panel."""
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                panel = self._app.query_one("#session-panel", SessionInfoPanel)
                panel.add_usage(prompt_tokens, completion_tokens, total, cached_tokens)
            except Exception as e:
                logger.warning(
                    "TUI update_token_usage failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def restore_token_usage(
        self, total_in: int, total_out: int, last_prompt: int, total_cached: int = 0
    ) -> None:
        """Restore cumulative token totals from session history."""
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                panel = self._app.query_one("#session-panel", SessionInfoPanel)
                panel.restore_usage(total_in, total_out, last_prompt, total_cached)
            except Exception as e:
                logger.warning(
                    "TUI restore_token_usage failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def add_tool_block(
        self,
        tool_name: str,
        args_preview: str = "",
        tool_id: str = "",
        target: str = "",
        agent_id: str = "",
    ) -> ToolBlock | None:
        key = target or "_default"
        sa_dict = self._current_subagents.get(key, {})
        sa = sa_dict.get(agent_id) if agent_id else None
        if sa is None and sa_dict:
            # Legacy activities without a job ID route to the newest active sub-agent.
            sa = list(sa_dict.values())[-1]
        if sa:

            def _do():
                try:
                    sa.add_tool_line(tool_name, args_preview)
                except Exception as e:
                    logger.warning(
                        "TUI add_tool_block failed", error=str(e), exc_info=True
                    )

            self._safe_call(_do)
            return None
        block = ToolBlock(tool_name, args_preview, tool_id)
        self._safe_mount(block, target=target)
        return block

    def update_tool_block(
        self,
        tool_name: str,
        output: str = "",
        error: str | None = None,
        tool_id: str = "",
        target: str = "",
        agent_id: str = "",
    ) -> None:
        if not self._app or not self._app.is_running:
            return
        scroll_id = self._get_chat_scroll_id(target)
        key = target or "_default"
        sa_dict = self._current_subagents.get(key, {})
        sa = sa_dict.get(agent_id) if agent_id else None
        if sa is None and sa_dict:
            # Legacy activities without a job ID route to the newest active sub-agent.
            sa = list(sa_dict.values())[-1]

        def _do():
            try:
                if sa:
                    sa.update_tool_line(tool_name, done=not error, error=bool(error))
                    return
                chat = self._app.query_one(f"#{scroll_id}", VerticalScroll)
                for child in reversed(list(chat.children)):
                    if not isinstance(child, ToolBlock) or child.state != "running":
                        continue
                    if (
                        tool_id and child.tool_id == tool_id
                    ) or child.tool_name == tool_name:
                        if error:
                            child.mark_error(error)
                        else:
                            child.mark_done(output)
                        return
            except Exception as e:
                logger.warning(
                    "TUI update_tool_block failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def add_subagent_block(
        self,
        agent_name: str,
        task: str = "",
        agent_id: str = "",
        target: str = "",
    ) -> SubAgentBlock:
        block = SubAgentBlock(agent_name, sa_task=task, agent_id=agent_id)
        key = target or "_default"
        if key not in self._current_subagents:
            self._current_subagents[key] = {}
        # Job IDs are stable for parallel same-name sub-agents; names support legacy events.
        sa_key = agent_id or agent_name
        self._current_subagents[key][sa_key] = block
        self._safe_mount(block, target=target)
        return block

    def end_subagent_block(
        self,
        output: str = "",
        tools_used: list[str] | None = None,
        turns: int = 0,
        duration: float = 0,
        error: str | None = None,
        target: str = "",
        agent_id: str = "",
    ) -> None:
        key = target or "_default"
        sa_dict = self._current_subagents.get(key, {})
        sa = sa_dict.pop(agent_id, None) if agent_id else None
        if sa is None and sa_dict:
            # Legacy completion events without IDs close the newest active block.
            _, sa = sa_dict.popitem()
        if not sa:
            return
        if error:
            sa.mark_error(error)
        else:
            sa.mark_done(output, tools_used, turns, duration)
        if not sa_dict:
            self._current_subagents.pop(key, None)

    def interrupt_subagent(self, target: str = "") -> None:
        key = target or "_default"
        sa_dict = self._current_subagents.get(key, {})
        for sa in sa_dict.values():
            sa.mark_interrupted()
        self._current_subagents.pop(key, None)

    def begin_streaming(self, target: str = "") -> None:
        key = target or "_default"
        widget = StreamingText()
        self._streaming_widgets[key] = widget
        self._safe_mount(widget, target=target)

    def append_stream(self, chunk: str, target: str = "") -> None:
        key = target or "_default"
        if key not in self._streaming_widgets:
            self.begin_streaming(target=target)
        widget = self._streaming_widgets.get(key)
        scroll_id = self._get_chat_scroll_id(target)

        def _do():
            try:
                if widget:
                    widget.append(chunk)
                    chat = self._app.query_one(f"#{scroll_id}", VerticalScroll)
                    chat.scroll_end(animate=False)
            except Exception as e:
                logger.warning("TUI append_stream failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def end_streaming(self, target: str = "") -> None:
        key = target or "_default"
        widget = self._streaming_widgets.pop(key, None)
        if not widget:
            return
        scroll_id = self._get_chat_scroll_id(target)
        target_key = target or self._active_target or "_default"

        def _do():
            try:
                text = widget.get_text().strip()
                if not text:
                    return
                chat = self._app.query_one(f"#{scroll_id}", VerticalScroll)
                at_bottom = (
                    chat.max_scroll_y == 0 or chat.scroll_y >= chat.max_scroll_y - 2
                )
                md = Markdown(text)
                chat.mount(md, after=widget)
                widget.remove()
                if at_bottom:
                    chat.scroll_end(animate=False)
                self._cull_chat_widgets(chat, target_key)
            except Exception as e:
                logger.warning("TUI end_streaming failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def update_running(
        self,
        item_id: str,
        label: str,
        remove: bool = False,
        promotable: bool = False,
    ) -> None:
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                panel = self._app.query_one("#running-panel", RunningPanel)
                if remove:
                    panel.remove_item(item_id)
                else:
                    panel.add_item(item_id, label, promotable=promotable)
            except Exception as e:
                logger.warning("TUI update_running failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def clear_running(self) -> None:
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                self._app.query_one("#running-panel", RunningPanel).clear()
            except Exception as e:
                logger.warning("TUI clear_running failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def update_scratchpad(self, data: dict) -> None:
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                self._app.query_one("#scratchpad-panel", ScratchpadPanel).update_data(
                    data
                )
            except Exception as e:
                logger.warning(
                    "TUI update_scratchpad failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def update_session_info(
        self, session_id: str = "", model: str = "", agent_name: str = ""
    ) -> None:
        # Session information may arrive before the application mounts.
        self._pending_session_info = (session_id, model, agent_name)
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                self._app.query_one("#session-panel", SessionInfoPanel).set_info(
                    session_id, model, agent_name
                )
            except Exception as e:
                logger.warning(
                    "TUI update_session_info failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def set_context_limits(self, max_context: int, compact_threshold: int = 0) -> None:
        self._pending_context_limits = (max_context, compact_threshold)
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                panel = self._app.query_one("#session-panel", SessionInfoPanel)
                panel.set_context_limits(max_context, compact_threshold)
            except Exception as e:
                logger.warning(
                    "TUI set_context_limits failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def set_compact_threshold(self, threshold_tokens: int) -> None:
        """Set identical context and compaction limits for compatibility."""
        self.set_context_limits(threshold_tokens, threshold_tokens)

    def apply_pending_session_info(self) -> None:
        """Apply session information buffered before application startup."""
        info = getattr(self, "_pending_session_info", None)
        if info:
            self.update_session_info(*info)
        limits = getattr(self, "_pending_context_limits", None)
        if limits:
            self.set_context_limits(*limits)

    def add_tokens(self, count: int) -> None:
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                self._app.query_one("#session-panel", SessionInfoPanel).add_tokens(
                    count
                )
            except Exception as e:
                logger.warning("TUI add_tokens failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def update_terrarium(self, creatures: list[dict], channels: list[dict]) -> None:
        """Update the terrarium overview."""
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                panel = self._app.query_one("#terrarium-panel", TerrariumPanel)
                panel.set_topology(creatures, channels)
            except Exception as e:
                logger.warning(
                    "TUI update_terrarium failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def write_log(self, text: str) -> None:
        # Framework logs are persisted by session output instead of rendered in chat.
        pass

    def start_thinking(self) -> None:
        if self._app and self._app.is_running:
            self._app._is_processing = True
            if self._app._queued_widgets:
                chat = self._app._get_active_chat()
                for qw in self._app._queued_widgets:
                    try:
                        text = qw.message_text
                        qw.remove()
                        if chat:
                            chat.mount(UserMessage(text))
                    except Exception as e:
                        logger.warning(
                            "TUI start_thinking queue promote failed",
                            error=str(e),
                            exc_info=True,
                        )
                if chat:
                    chat.scroll_end(animate=False)
                self._app._queued_widgets.clear()
            try:
                self._app.start_thinking_animation()
            except Exception as e:
                logger.warning(
                    "TUI start_thinking_animation failed", error=str(e), exc_info=True
                )

    def stop_thinking(self) -> None:
        if self._app and self._app.is_running:
            try:
                self._app.stop_thinking_animation()
            except Exception as e:
                logger.warning(
                    "TUI stop_thinking_animation failed", error=str(e), exc_info=True
                )

    def set_idle(self) -> None:
        if self._app and self._app.is_running:
            self._app._is_processing = False
            try:
                self._app.query_one("#quick-status", Static).update(IDLE_STATUS)
            except Exception as e:
                logger.warning("TUI set_idle failed", error=str(e), exc_info=True)

    async def wait_ready(self, timeout: float = 5.0) -> bool:
        if not self._app:
            return False
        try:
            await asyncio.wait_for(self._app._mounted_event.wait(), timeout)
            self.apply_pending_session_info()
            return True
        except asyncio.TimeoutError:
            return False

    async def start(self, prompt: str = "You: ") -> None:
        self._app = AgentTUI(
            agent_name=self.agent_name,
            terrarium_tabs=self._terrarium_tabs,
        )
        self._app.tui_session = self
        self._app.on_cancel_job = self._handle_cancel_job
        self._app.on_promote_job = self._handle_promote_job
        self.running = True
        self._stop_event.clear()

    def _handle_cancel_job(self, job_id: str, job_name: str) -> None:
        """Relay a job cancellation to the registered callback."""
        if self.on_cancel_job:
            self.on_cancel_job(job_id, job_name)

    def _handle_promote_job(self, job_id: str) -> None:
        """Relay a background-promotion request to the registered callback."""
        if self.on_promote_job:
            self.on_promote_job(job_id)

    async def run_app(self) -> None:
        if not self._app:
            return
        try:
            await self._app.run_async()
        except Exception as e:
            logger.error("TUI app error", error=str(e))
        finally:
            self.running = False
            self._stop_event.set()
            self._signal_input_shutdown()

    def _signal_input_shutdown(self) -> None:
        """Discard pending submissions and wake the runner with shutdown."""
        self._settle_pending_modals()
        if not self._app:
            return
        while True:
            try:
                self._app._input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._app._input_queue.put_nowait("")

    async def get_input(self, prompt: str = "You: ") -> str:
        if not self._app:
            return ""
        return await self._app._input_queue.get()

    def _register_modal(self, default: _T) -> asyncio.Future[_T]:
        """Track a modal waiter so application shutdown can settle it."""
        future: asyncio.Future[_T] = asyncio.get_running_loop().create_future()
        self._pending_modal_defaults[future] = default
        return future

    def _resolve_modal(self, future: asyncio.Future[_T], value: _T) -> None:
        """Resolve a modal exactly once and remove its shutdown fallback."""
        self._pending_modal_defaults.pop(future, None)
        if not future.done():
            future.set_result(value)

    def _settle_pending_modals(self) -> None:
        """Resolve every open modal with its cancellation default."""
        pending = tuple(self._pending_modal_defaults.items())
        self._pending_modal_defaults.clear()
        for future, default in pending:
            if not future.done():
                future.set_result(default)

    async def show_selection_modal(
        self, title: str, options: list[dict], current: str = ""
    ) -> str | None:
        """Show a selection modal and return its selected value."""
        if not self._app or not self._app.is_running:
            return None

        result_future = self._register_modal(None)
        modal = SelectionModal(title=title, options=options, current=current)

        def _on_dismiss(value: str | None) -> None:
            self._resolve_modal(result_future, value)

        # Modal screens must be pushed from Textual's application context.
        self._app.call_later(lambda: self._app.push_screen(modal, callback=_on_dismiss))
        try:
            return await result_future
        finally:
            self._pending_modal_defaults.pop(result_future, None)

    async def show_confirm_modal(self, message: str) -> bool:
        """Show a confirmation modal and return its result."""
        if not self._app or not self._app.is_running:
            return False

        result_future = self._register_modal(False)
        modal = ConfirmModal(message)

        def _on_dismiss(value: bool) -> None:
            self._resolve_modal(result_future, value)

        self._app.call_later(lambda: self._app.push_screen(modal, callback=_on_dismiss))
        try:
            return await result_future
        finally:
            self._pending_modal_defaults.pop(result_future, None)

    async def show_model_picker_modal(self, agent: Any) -> None:
        """Open the model picker for an agent."""
        if not self._app or not self._app.is_running:
            return
        self._app.call_later(lambda: self._app.push_screen(ModelPickerModal(agent)))

    async def show_modules_modal(self, agent: Any) -> None:
        """Open the module manager without waiting for dismissal."""
        if not self._app or not self._app.is_running:
            return
        self._app.call_later(lambda: self._app.push_screen(ModulesModal(agent)))

    async def show_drive_panel(self) -> None:
        """Open the drive records and settings panel."""
        if not self._app or not self._app.is_running:
            return
        self._app.call_later(self._app.action_open_drives)

    async def show_module_edit_modal(self, agent: Any, name: str) -> bool:
        """Open a named module's editor if it resolves uniquely."""
        if not self._app or not self._app.is_running:
            return False
        try:
            inventory = _list_modules(agent)
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.warning("show_module_edit_modal inventory failed", error=str(exc))
            return False
        target = None
        if "/" in name:
            type_part, _, name_part = name.partition("/")
            for m in inventory:
                if m["type"] == type_part and m["name"] == name_part:
                    target = m
                    break
        else:
            matches = [m for m in inventory if m["name"] == name]
            if len(matches) == 1:
                target = matches[0]
        if target is None:
            return False
        self._app.call_later(
            lambda: self._app.push_screen(ModuleEditModal(agent, target))
        )
        return True

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()
        if self._app:
            self._signal_input_shutdown()
            if self._app.is_running:
                self._app.exit()

    async def wait_for_stop(self) -> None:
        await self._stop_event.wait()
