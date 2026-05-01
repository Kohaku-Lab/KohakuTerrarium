"""
TUI session: shared state between input and output modules.

Standalone mode: single chat area.
Terrarium mode: tabbed chat (root + creatures + channels) + terrarium panel.
"""

import asyncio
from typing import Any

from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from kohakuterrarium.builtins.tui.app import IDLE_STATUS, AgentTUI, _safe_id
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

# Default widget limits (configurable via TUISession)
DEFAULT_MAX_CHAT_WIDGETS = 80  # Cull when exceeding this
DEFAULT_CULL_KEEP = 50  # Keep this many after cull
DEFAULT_LOAD_BATCH = 30  # Load this many when "Load older" clicked
CULL_KEEP = DEFAULT_CULL_KEEP  # Module-level for import by output.py


# ────────────────────────────────────────────────────────────────
# TUISession
# ────────────────────────────────────────────────────────────────


class TUISession:
    """Shared TUI state between input and output modules.

    In terrarium mode, each tab (root, creature, channel) has its own
    chat scroll. Widgets are routed to the correct tab via `target`.
    """

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
        self._streaming_widgets: dict[str, StreamingText] = {}  # target -> widget
        # target -> agent_name -> block (supports parallel sub-agents on same target)
        self._current_subagents: dict[str, dict[str, SubAgentBlock]] = {}
        # Terrarium mode
        self._terrarium_tabs: list[str] | None = None
        self._active_target: str = ""  # which tab output is currently targeting
        # Widget culling config
        self._max_chat_widgets = max_chat_widgets
        self._cull_keep = cull_keep
        self._load_batch = load_batch
        # "Load older" system: stores widgets that were culled or not mounted
        # on resume. Keyed by target. Each is a list of widgets (oldest first).
        self._older_widgets: dict[str, list] = {}  # target -> [widget, ...]
        self._culled_count: dict[str, int] = {}  # target -> count of culled widgets
        # Job cancel callback: Callable[[str, str], None] or None
        # Signature: (job_id, job_name) -> None
        self.on_cancel_job: Any = None
        self.on_promote_job: Any = None
        # Live agent reference, wired by ``TUIInput.set_user_commands``
        # so modal screens (Modules, etc.) can mutate state without
        # round-tripping the slash-command pipeline.
        self.host_agent: Any = None

    def set_terrarium_tabs(self, tabs: list[str]) -> None:
        """Configure terrarium mode before start()."""
        self._terrarium_tabs = tabs
        if tabs:
            self._active_target = tabs[0]

    def set_active_target(self, target: str) -> None:
        """Set which tab receives new output widgets."""
        self._active_target = target

    def get_active_tab(self) -> str:
        """Get the user's currently visible tab."""
        if self._app:
            return self._app.get_active_tab_name()
        return self._active_target

    # ── Safe widget operations ──────────────────────────────────

    def _safe_call(self, fn: Any, *args: Any) -> None:
        if not self._app or not self._app.is_running:
            return
        try:
            self._app.call_later(fn, *args)
        except Exception as e:
            _ = e  # call_later failed, try call_from_thread
            try:
                self._app.call_from_thread(fn, *args)
            except Exception as e:
                logger.debug("TUI safe_call failed", error=str(e), exc_info=True)

    def _get_chat_scroll_id(self, target: str = "") -> str:
        """Get the chat scroll widget ID for a target."""
        if not self._terrarium_tabs:
            return "chat-scroll"
        t = target or self._active_target
        return f"chat-{_safe_id(t)}" if t else "chat-scroll"

    def _safe_mount(self, widget: Any, scroll: bool = True, target: str = "") -> None:
        if not self._app or not self._app.is_running:
            return
        scroll_id = self._get_chat_scroll_id(target)

        def _do():
            try:
                chat = self._app.query_one(f"#{scroll_id}", VerticalScroll)
                chat.mount(widget)
                if scroll:
                    chat.scroll_end(animate=False)
                self._cull_chat_widgets(chat)
            except Exception as e:
                logger.debug("TUI safe_mount failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def _cull_chat_widgets(self, chat: VerticalScroll) -> None:
        """Remove old widgets when chat has too many, keeping recent ones."""
        children = list(chat.children)
        if len(children) <= self._max_chat_widgets:
            return

        remove_count = len(children) - self._cull_keep
        to_remove = children[:remove_count]

        target = self._active_target or "_default"
        self._culled_count[target] = self._culled_count.get(target, 0) + remove_count

        for w in to_remove:
            w.remove()

        # Update or add LoadOlderButton at top
        self._update_load_older_button(chat, target)

    def _update_load_older_button(self, chat: VerticalScroll, target: str) -> None:
        """Add/update the 'Load older' button at the top of chat."""
        # How many widgets can we load from the stored older_widgets?
        available = len(self._older_widgets.get(target, []))
        culled = self._culled_count.get(target, 0)
        total_hidden = available + culled

        if total_hidden <= 0:
            return

        # Remove existing button
        for child in list(chat.children):
            if isinstance(child, LoadOlderButton):
                child.remove()
                break

        # Only show button if there are stored widgets to load
        if available > 0:
            btn = LoadOlderButton(available)
            first = list(chat.children)
            if first:
                chat.mount(btn, before=first[0])
            else:
                chat.mount(btn)
        elif culled > 0:
            # Culled live messages (no stored data to reload)
            header = Static(
                f"[{culled} earlier messages not available]",
                classes="cull-header",
            )
            first = list(chat.children)
            if first:
                chat.mount(header, before=first[0])

    def store_older_widgets(self, target: str, widgets: list) -> None:
        """Store widgets for 'Load older' (from resume truncation)."""
        self._older_widgets[target] = widgets

    def load_older_batch(self, target: str = "") -> None:
        """Load a batch of older widgets into the chat."""
        target = target or self._active_target or "_default"
        stored = self._older_widgets.get(target, [])
        if not stored:
            return

        scroll_id = self._get_chat_scroll_id(target)
        batch_size = self._load_batch
        # Take from the end of stored (most recent of the older messages)
        batch = stored[-batch_size:]
        self._older_widgets[target] = (
            stored[:-batch_size] if batch_size < len(stored) else []
        )

        def _do():
            try:
                chat = self._app.query_one(f"#{scroll_id}", VerticalScroll)
                # Remove the LoadOlderButton
                for child in list(chat.children):
                    if isinstance(child, LoadOlderButton):
                        child.remove()
                        break
                # Prepend the batch
                first = list(chat.children)
                if first:
                    for w in reversed(batch):
                        chat.mount(w, before=first[0])
                else:
                    for w in batch:
                        chat.mount(w)
                # Add new button if more available
                self._update_load_older_button(chat, target)
            except Exception as e:
                logger.debug("TUI load_older_batch failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    # ── Chat area ───────────────────────────────────────────────

    def add_user_message(self, text: str, target: str = "") -> None:
        self._safe_mount(UserMessage(text), target=target)

    def add_system_notice(
        self, text: str, command: str = "", error: bool = False, target: str = ""
    ) -> None:
        """Add a non-collapsible system notice (for command results)."""
        self._safe_mount(
            SystemNotice(text, command=command, error=error), target=target
        )

    def add_error_block(
        self, error_type: str, error_msg: str, target: str = ""
    ) -> None:
        """Add a processing error block (red, prominent)."""
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
        """Mount a Phase B ``card`` event as an inline widget.

        When ``on_action`` is provided and ``payload.actions`` is
        non-empty, the card renders interactive Buttons that call
        ``on_action(event_id, action_id)`` on click.
        """
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
        """Phase B ``progress`` event renderer.

        Mounts a new ``ProgressBlock`` on first call for ``widget_id``;
        on subsequent calls (events with ``update_target=widget_id``)
        mutates the existing widget in place.
        """
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
                logger.debug("ProgressBlock update failed", error=str(e))
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
            # First-emit-also-complete edge case.
            try:
                block.update_progress(
                    label=label,
                    value=value,
                    max_value=max_value,
                    indeterminate=indeterminate,
                    complete=True,
                )
            except Exception:
                pass

    def add_compact_summary(
        self, round_num: int, summary: str, target: str = ""
    ) -> None:
        """Add a compact summary accordion to the chat (shows immediately)."""
        block = CompactSummaryBlock(summary)
        self._last_compact_block = block
        self._safe_mount(block, target=target)

    def update_compact_summary(
        self, round_num: int, summary: str, target: str = ""
    ) -> None:
        """Update the current compact block with final summary (amber -> aquamarine)."""
        block = getattr(self, "_last_compact_block", None)
        if block:

            def _do():
                try:
                    block.mark_done(summary)
                except Exception as e:
                    logger.debug(
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
        """Update session info with per-call token usage (accumulated)."""
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                panel = self._app.query_one("#session-panel", SessionInfoPanel)
                panel.add_usage(prompt_tokens, completion_tokens, total, cached_tokens)
            except Exception as e:
                logger.debug(
                    "TUI update_token_usage failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def restore_token_usage(
        self, total_in: int, total_out: int, last_prompt: int, total_cached: int = 0
    ) -> None:
        """Restore cumulative token totals from session history (on resume)."""
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                panel = self._app.query_one("#session-panel", SessionInfoPanel)
                panel.restore_usage(total_in, total_out, last_prompt, total_cached)
            except Exception as e:
                logger.debug(
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
        # Fallback: if no agent_id given, use the last/only active sub-agent
        if sa is None and sa_dict:
            sa = list(sa_dict.values())[-1]
        if sa:

            def _do():
                try:
                    sa.add_tool_line(tool_name, args_preview)
                except Exception as e:
                    logger.debug(
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
                logger.debug(
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
        # Key by agent_id (job_id) for unique routing, not by name
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
            # Fallback: pop any sub-agent
            _, sa = sa_dict.popitem()
        if not sa:
            return
        if error:
            sa.mark_error(error)
        else:
            sa.mark_done(output, tools_used, turns, duration)
        # Clean up empty dict
        if not sa_dict:
            self._current_subagents.pop(key, None)

    def interrupt_subagent(self, target: str = "") -> None:
        key = target or "_default"
        sa_dict = self._current_subagents.get(key, {})
        for sa in sa_dict.values():
            sa.mark_interrupted()
        self._current_subagents.pop(key, None)

    # ── Streaming text ──────────────────────────────────────────

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
                logger.debug("TUI append_stream failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def end_streaming(self, target: str = "") -> None:
        key = target or "_default"
        widget = self._streaming_widgets.pop(key, None)
        if not widget:
            return
        scroll_id = self._get_chat_scroll_id(target)

        def _do():
            try:
                text = widget.get_text().strip()
                if not text:
                    return
                chat = self._app.query_one(f"#{scroll_id}", VerticalScroll)
                # Check if user is at bottom before replacing
                at_bottom = (
                    chat.max_scroll_y == 0 or chat.scroll_y >= chat.max_scroll_y - 2
                )
                # Replace StreamingText with Textual Markdown (selectable)
                md = Markdown(text)
                chat.mount(md, after=widget)
                widget.remove()
                # Keep scroll at bottom if user was there
                if at_bottom:
                    chat.scroll_end(animate=False)
                # Cull old widgets if too many
                self._cull_chat_widgets(chat)
            except Exception as e:
                logger.debug("TUI end_streaming failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    # ── Right panel ─────────────────────────────────────────────

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
                logger.debug("TUI update_running failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def clear_running(self) -> None:
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                self._app.query_one("#running-panel", RunningPanel).clear()
            except Exception as e:
                logger.debug("TUI clear_running failed", error=str(e), exc_info=True)

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
                logger.debug(
                    "TUI update_scratchpad failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def update_session_info(
        self, session_id: str = "", model: str = "", agent_name: str = ""
    ) -> None:
        # Buffer for deferred apply (session_info fires before TUI app mounts)
        self._pending_session_info = (session_id, model, agent_name)
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                self._app.query_one("#session-panel", SessionInfoPanel).set_info(
                    session_id, model, agent_name
                )
            except Exception as e:
                logger.debug(
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
                logger.debug(
                    "TUI set_context_limits failed", error=str(e), exc_info=True
                )

        self._safe_call(_do)

    def set_compact_threshold(self, threshold_tokens: int) -> None:
        """Backward compat — prefer set_context_limits()."""
        self.set_context_limits(threshold_tokens, threshold_tokens)

    def apply_pending_session_info(self) -> None:
        """Apply buffered session info after TUI app is ready."""
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
                logger.debug("TUI add_tokens failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def update_terrarium(self, creatures: list[dict], channels: list[dict]) -> None:
        """Update the terrarium overview panel."""
        if not self._app or not self._app.is_running:
            return

        def _do():
            try:
                panel = self._app.query_one("#terrarium-panel", TerrariumPanel)
                panel.set_topology(creatures, channels)
            except Exception as e:
                logger.debug("TUI update_terrarium failed", error=str(e), exc_info=True)

        self._safe_call(_do)

    def write_log(self, text: str) -> None:
        pass  # Logs go to session DB

    # ── Processing animation ────────────────────────────────────

    def start_thinking(self) -> None:
        if self._app and self._app.is_running:
            self._app._is_processing = True
            # Move queued messages from queue area into chat (promoted to UserMessage style)
            if self._app._queued_widgets:
                chat = self._app._get_active_chat()
                for qw in self._app._queued_widgets:
                    try:
                        # Remove from queue area, mount as UserMessage in chat
                        text = qw.message_text
                        qw.remove()
                        if chat:
                            chat.mount(UserMessage(text))
                    except Exception as e:
                        logger.debug(
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
                logger.debug(
                    "TUI start_thinking_animation failed", error=str(e), exc_info=True
                )

    def stop_thinking(self) -> None:
        if self._app and self._app.is_running:
            try:
                self._app.stop_thinking_animation()
            except Exception as e:
                logger.debug(
                    "TUI stop_thinking_animation failed", error=str(e), exc_info=True
                )

    def set_idle(self) -> None:
        if self._app and self._app.is_running:
            self._app._is_processing = False
            try:
                self._app.query_one("#quick-status", Static).update(IDLE_STATUS)
            except Exception as e:
                logger.debug("TUI set_idle failed", error=str(e), exc_info=True)

    # ── Lifecycle ───────────────────────────────────────────────

    async def wait_ready(self, timeout: float = 5.0) -> bool:
        if not self._app:
            return False
        try:
            await asyncio.wait_for(self._app._mounted_event.wait(), timeout)
            # Apply any session info buffered before the app was ready
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
        """Relay cancel request from the TUI app to the registered callback."""
        if self.on_cancel_job:
            self.on_cancel_job(job_id, job_name)

    def _handle_promote_job(self, job_id: str) -> None:
        """Relay [→bg] promote request from the TUI app to the registered callback."""
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
            self._app._input_queue.put_nowait("")  # unblock get_input

    async def get_input(self, prompt: str = "You: ") -> str:
        if not self._app:
            return ""
        return await self._app._input_queue.get()

    async def show_selection_modal(
        self, title: str, options: list[dict], current: str = ""
    ) -> str | None:
        """Show a selection modal and return chosen value or None.

        Safe to call from any async context. Uses ``call_later`` to
        delegate ``push_screen`` to the app's message loop where
        ``active_app`` ContextVar is set.
        """
        if not self._app or not self._app.is_running:
            return None

        result_future: asyncio.Future[str | None] = asyncio.Future()
        modal = SelectionModal(title=title, options=options, current=current)

        def _on_dismiss(value: str | None) -> None:
            if not result_future.done():
                result_future.set_result(value)

        # call_later posts to the app's message queue — the callback
        # executes inside the app's _context() where active_app is set.
        self._app.call_later(lambda: self._app.push_screen(modal, callback=_on_dismiss))
        return await result_future

    async def show_confirm_modal(self, message: str) -> bool:
        """Show a confirm modal and return True/False."""
        if not self._app or not self._app.is_running:
            return False

        result_future: asyncio.Future[bool] = asyncio.Future()
        modal = ConfirmModal(message)

        def _on_dismiss(value: bool) -> None:
            if not result_future.done():
                result_future.set_result(value)

        self._app.call_later(lambda: self._app.push_screen(modal, callback=_on_dismiss))
        return await result_future

    async def show_model_picker_modal(self, agent: Any) -> None:
        """Push the model-picker modal screen.

        Used by the F3 keybinding and the ``/model`` (no-args)
        slash-command intercept.
        """
        if not self._app or not self._app.is_running:
            return
        self._app.call_later(lambda: self._app.push_screen(ModelPickerModal(agent)))

    async def show_modules_modal(self, agent: Any) -> None:
        """Push the Modules modal screen.

        Fire-and-forget — the modal manages its own dismiss; we don't
        block on the result. Used by both the F2 keybinding and the
        ``/module`` slash-command intercept in :class:`TUIInput`.
        """
        if not self._app or not self._app.is_running:
            return
        self._app.call_later(lambda: self._app.push_screen(ModulesModal(agent)))

    async def show_module_edit_modal(self, agent: Any, name: str) -> bool:
        """Resolve ``name`` to a module record and push the edit modal.

        Returns ``True`` if the modal was pushed, ``False`` if the
        name didn't resolve (caller may then fall through to the
        text-mode "not found" error from the slash command).
        """
        if not self._app or not self._app.is_running:
            return False
        try:
            inventory = _list_modules(agent)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("show_module_edit_modal inventory failed", error=str(exc))
            return False
        # Accept ``type/name`` or bare ``name``.
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
            self._app._input_queue.put_nowait("")  # unblock get_input
            if self._app.is_running:
                self._app.exit()

    async def wait_for_stop(self) -> None:
        await self._stop_event.wait()
