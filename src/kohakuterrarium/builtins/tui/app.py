"""AgentTUI - Textual application for the KohakuTerrarium TUI."""

import asyncio
import threading
import time
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from kohakuterrarium.builtins.tui.widgets import (
    ChatInput,
    LoadOlderButton,
    QueuedMessage,
    RunningPanel,
    ScratchpadPanel,
    SessionInfoPanel,
    TerrariumPanel,
    UserMessage,
)
from kohakuterrarium.builtins.tui.widgets.model_picker_modal import ModelPickerModal
from kohakuterrarium.builtins.tui.widgets.modules_modal import ModulesModal
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

IDLE_STATUS = "\u25cf KohakUwU"

THINKING_FRAMES = [
    "\u25d0 KohakUwUing",
    "\u25d3 KohakUwUing.",
    "\u25d1 KohakUwUing..",
    "\u25d2 KohakUwUing...",
]


class AgentTUI(App):
    """Textual app for KohakuTerrarium agent interaction."""

    TITLE = "KohakuTerrarium"
    CSS = """
    $kohaku-iolite: #5A4FCF;
    $kohaku-amber: #D4920A;

    Header { background: $kohaku-iolite; color: white; }
    Footer { background: $kohaku-iolite 15%; }

    #main-container { height: 1fr; }
    #left-panel { width: 2fr; }
    #right-panel { width: 1fr; min-width: 30; }
    #chat-scroll { height: 1fr; border: solid $primary-background; padding: 0 1; }
    #chat-tabs { height: 1fr; }
    #quick-status { height: 1; color: $kohaku-amber; padding: 0 1; }
    #input-box { dock: bottom; }
    #queued-area { height: auto; max-height: 6; padding: 0 1; }
    #right-status-panel { height: 1fr; overflow-y: auto; padding: 1; }

    .chat-tab-scroll { height: 1fr; padding: 0 1; }
    .cull-header { height: 1; color: $text-muted; text-align: center; padding: 0 1; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_output", "Clear", show=True),
        Binding("escape", "interrupt", "Interrupt", show=True),
        # ``priority=True`` makes the app-level handler run BEFORE the
        # focused widget gets the key. ChatInput is a TextArea that
        # otherwise eats function keys via its generic input handler,
        # which is why F2/F3 went nowhere when chat input had focus.
        Binding("f2", "open_modules", "Modules", show=True, priority=True),
        Binding("f3", "open_model_picker", "Model", show=True, priority=True),
    ]

    def __init__(
        self,
        agent_name: str = "agent",
        terrarium_tabs: list[str] | None = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self.tui_session: Any = None  # Set by TUISession.start()
        # Terrarium tabs: ["root", "swe", "reviewer", "#tasks", "#review"]
        self._terrarium_tabs = terrarium_tabs
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._queued_widgets: list[QueuedMessage] = []
        self._is_processing = False
        self._mounted_event = asyncio.Event()
        self._thinking_active = False
        self._thinking_thread: threading.Thread | None = None
        self.on_interrupt: Any = None
        self.on_cancel_job: Any = None  # Callable[[str, str], None] or None
        self.on_promote_job: Any = None  # Callable[[str], bool] or None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="left-panel"):
                if self._terrarium_tabs:
                    with TabbedContent(id="chat-tabs"):
                        for tab_name in self._terrarium_tabs:
                            label = (
                                tab_name if not tab_name.startswith("#") else tab_name
                            )
                            with TabPane(label, id=f"tab-{_safe_id(tab_name)}"):
                                yield VerticalScroll(
                                    id=f"chat-{_safe_id(tab_name)}",
                                    classes="chat-tab-scroll",
                                )
                else:
                    yield VerticalScroll(id="chat-scroll")
                yield Static("", id="quick-status")
                yield Vertical(id="queued-area")
                yield ChatInput(id="input-box")
            with Vertical(id="right-panel"):
                with VerticalScroll(id="right-status-panel"):
                    yield RunningPanel(id="running-panel")
                    yield ScratchpadPanel(id="scratchpad-panel")
                    yield SessionInfoPanel(id="session-panel")
                    if self._terrarium_tabs:
                        yield TerrariumPanel(id="terrarium-panel")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"KohakuTerrarium - {self.agent_name}"
        self._set_status_text(IDLE_STATUS)
        self._mounted_event.set()

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        # Slash commands: don't show in chat — command system handles display
        if text.startswith("/"):
            self._input_queue.put_nowait(text)
            return
        if self._is_processing:
            # Agent is busy: show in queued area (above input, not in chat)
            qw = QueuedMessage(text)
            self._queued_widgets.append(qw)
            try:
                self.query_one("#queued-area", Vertical).mount(qw)
            except Exception as e:
                logger.debug(
                    "Failed to mount queued widget", error=str(e), exc_info=True
                )
        else:
            chat = self._get_active_chat()
            if chat:
                chat.mount(UserMessage(text))
                chat.scroll_end(animate=False)
        self._input_queue.put_nowait(text)

    def on_chat_input_command_hint(self, event: ChatInput.CommandHint) -> None:
        """Show command completion hints in the quick-status line."""
        try:
            status = self.query_one("#quick-status", Static)
            if event.hint:
                status.update(event.hint)
            elif not self._is_processing:
                status.update(IDLE_STATUS)
        except Exception as e:
            logger.debug("Failed to update command hint", error=str(e), exc_info=True)

    def on_chat_input_edit_queued(self, event: ChatInput.EditQueued) -> None:
        """Pull the last queued message back into the input box for editing."""
        if not self._queued_widgets:
            return
        qw = self._queued_widgets.pop()
        text = qw.message_text
        # Remove from chat and queue
        qw.remove()
        # Drain this message from the asyncio queue
        try:
            # Queue is FIFO; the message we want is the last one.
            # Rebuild queue without the last item.
            items = []
            while not self._input_queue.empty():
                items.append(self._input_queue.get_nowait())
            if items:
                items.pop()  # remove the last (most recent queued message)
            for item in items:
                self._input_queue.put_nowait(item)
        except Exception as e:
            logger.debug("Failed to rebuild input queue", error=str(e), exc_info=True)
        # Put text back in input box
        try:
            inp = self.query_one("#input-box", ChatInput)
            inp.clear()
            inp.insert(text)
            inp.focus()
        except Exception as e:
            logger.debug(
                "Failed to restore text to input box", error=str(e), exc_info=True
            )

    def on_load_older_button_clicked(self, event: LoadOlderButton.Clicked) -> None:
        """Handle 'Load older' button click."""
        if self.tui_session:
            target = self.get_active_tab_name() if self._terrarium_tabs else ""
            self.tui_session.load_older_batch(target)

    def on_running_panel_cancel_requested(
        self, event: RunningPanel.CancelRequested
    ) -> None:
        """Handle click-to-cancel on a running job."""
        if self.on_cancel_job:
            self.on_cancel_job(event.job_id, event.job_name)

    def on_running_panel_promote_requested(
        self, event: RunningPanel.PromoteRequested
    ) -> None:
        """Handle click-to-promote on a running direct job."""
        if self.on_promote_job:
            self.on_promote_job(event.job_id)

    def _get_active_chat(self) -> VerticalScroll | None:
        """Get the currently visible chat scroll widget."""
        try:
            if self._terrarium_tabs:
                tabs = self.query_one("#chat-tabs", TabbedContent)
                active = tabs.active
                # active is like "tab-root", extract the id suffix
                if active:
                    scroll_id = active.replace("tab-", "chat-")
                    return self.query_one(f"#{scroll_id}", VerticalScroll)
            return self.query_one("#chat-scroll", VerticalScroll)
        except Exception as e:
            logger.debug(
                "Failed to get active chat scroll", error=str(e), exc_info=True
            )
            return None

    def get_active_tab_name(self) -> str:
        """Get the active tab name (e.g. 'root', 'swe', '#tasks')."""
        if not self._terrarium_tabs:
            return ""
        try:
            tabs = self.query_one("#chat-tabs", TabbedContent)
            active_id = tabs.active  # "tab-root"
            if active_id:
                return _id_to_name(active_id.replace("tab-", ""))
        except Exception as e:
            logger.debug("Failed to get active tab name", error=str(e), exc_info=True)
        return self._terrarium_tabs[0] if self._terrarium_tabs else ""

    def action_interrupt(self) -> None:
        if self.on_interrupt:
            self.on_interrupt()

    def action_clear_output(self) -> None:
        chat = self._get_active_chat()
        if chat:
            chat.remove_children()

    def action_quit(self) -> None:
        self._stop_event.set()
        self._input_queue.put_nowait("")  # empty string signals exit
        self.exit()

    def action_open_modules(self) -> None:
        """Push the Modules modal screen (F2 keybinding)."""
        agent = (
            getattr(self.tui_session, "host_agent", None) if self.tui_session else None
        )
        if agent is None:
            return
        self.push_screen(ModulesModal(agent))

    def action_open_model_picker(self) -> None:
        """Push the model-picker modal (F3 keybinding)."""
        agent = (
            getattr(self.tui_session, "host_agent", None) if self.tui_session else None
        )
        if agent is None:
            return
        self.push_screen(ModelPickerModal(agent))

    # ── Thinking animation ──────────────────────────────────────

    def start_thinking_animation(self) -> None:
        self._thinking_active = True
        self._thinking_thread = threading.Thread(
            target=self._thinking_loop, daemon=True
        )
        self._thinking_thread.start()

    def stop_thinking_animation(self) -> None:
        self._thinking_active = False
        try:
            self.call_from_thread(self._clear_status)
        except Exception as e:
            logger.debug(
                "Failed to clear status on stop thinking", error=str(e), exc_info=True
            )

    def _thinking_loop(self) -> None:
        idx = 0
        while self._thinking_active:
            frame = THINKING_FRAMES[idx % len(THINKING_FRAMES)]
            try:
                self.call_from_thread(self._set_status_text, frame)
            except Exception as e:
                logger.debug(
                    "Thinking animation loop ended", error=str(e), exc_info=True
                )
                break
            idx += 1
            time.sleep(0.3)

    def _set_status_text(self, text: str) -> None:
        try:
            self.query_one("#quick-status", Static).update(text)
        except Exception as e:
            logger.debug("Failed to set status text", error=str(e), exc_info=True)

    def _clear_status(self) -> None:
        try:
            self.query_one("#quick-status", Static).update("")
        except Exception as e:
            logger.debug("Failed to clear status", error=str(e), exc_info=True)


# ── Helpers ─────────────────────────────────────────────────────


def _safe_id(name: str) -> str:
    """Convert tab name to CSS-safe ID. '#tasks' -> 'ch_tasks'."""
    if name.startswith("#"):
        return "ch_" + name[1:].replace("-", "_")
    return name.replace("-", "_")


def _id_to_name(safe: str) -> str:
    """Reverse of _safe_id. 'ch_tasks' -> '#tasks'."""
    if safe.startswith("ch_"):
        return "#" + safe[3:]
    return safe
