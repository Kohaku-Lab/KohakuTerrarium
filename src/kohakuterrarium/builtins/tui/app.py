"""AgentTUI - Textual application for the KohakuTerrarium TUI."""

import asyncio
import threading
import time
from typing import Any

from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
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
    """Run the Textual interface for agent interaction."""

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
        # Priority lets app shortcuts bypass the focused TextArea's key handler.
        Binding("f2", "open_modules", "Modules", show=True, priority=True),
        Binding("f3", "open_model_picker", "Model", show=True, priority=True),
        Binding("f4", "open_drives", "Drives", show=True, priority=True),
    ]

    def __init__(
        self,
        agent_name: str = "agent",
        terrarium_tabs: list[str] | None = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.agent_name = agent_name
        self.tui_session: Any = None
        self._terrarium_tabs = terrarium_tabs
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self._queued_widgets: list[QueuedMessage] = []
        self._is_processing = False
        self._mounted_event = asyncio.Event()
        self._thinking_active = False
        self._thinking_thread: threading.Thread | None = None
        # Callbacks receive no arguments for interrupt, (job_id, job_name) for
        # cancellation, and job_id for background promotion.
        self.on_interrupt: Any = None
        self.on_cancel_job: Any = None
        self.on_promote_job: Any = None

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
        # Slash-command output is rendered by the command system.
        if text.startswith("/"):
            self._input_queue.put_nowait(text)
            return
        if self._is_processing:
            # Busy turns retain new input visibly until processing resumes.
            qw = QueuedMessage(text)
            self._queued_widgets.append(qw)
            try:
                self.query_one("#queued-area", Vertical).mount(qw)
            except Exception as e:
                logger.warning(
                    "Failed to mount queued widget", error=str(e), exc_info=True
                )
        else:
            chat = self._get_active_chat()
            if chat:
                chat.mount(UserMessage(text))
                chat.scroll_end(animate=False)
        self._input_queue.put_nowait(text)

    def on_chat_input_command_hint(self, event: ChatInput.CommandHint) -> None:
        """Display command completion hints in the status line."""
        try:
            status = self.query_one("#quick-status", Static)
            if event.hint:
                status.update(event.hint)
            elif not self._is_processing:
                status.update(IDLE_STATUS)
        except Exception as e:
            logger.warning("Failed to update command hint", error=str(e), exc_info=True)

    def on_chat_input_edit_queued(self, event: ChatInput.EditQueued) -> None:
        """Restore the latest queued message to the input editor."""
        if not self._queued_widgets:
            return
        qw = self._queued_widgets.pop()
        text = qw.message_text
        qw.remove()
        try:
            # Preserve FIFO order while removing the most recently queued item.
            items = []
            while not self._input_queue.empty():
                items.append(self._input_queue.get_nowait())
            if items:
                items.pop()
            for item in items:
                self._input_queue.put_nowait(item)
        except Exception as e:
            logger.warning("Failed to rebuild input queue", error=str(e), exc_info=True)
        try:
            inp = self.query_one("#input-box", ChatInput)
            inp.clear()
            inp.insert(text)
            inp.focus()
        except Exception as e:
            logger.warning(
                "Failed to restore text to input box", error=str(e), exc_info=True
            )

    def on_load_older_button_clicked(self, event: LoadOlderButton.Clicked) -> None:
        """Load older messages into the active chat."""
        if self.tui_session:
            target = self.get_active_tab_name() if self._terrarium_tabs else ""
            self.tui_session.load_older_batch(target)

    def on_running_panel_cancel_requested(
        self, event: RunningPanel.CancelRequested
    ) -> None:
        """Forward a running job's cancellation request."""
        if self.on_cancel_job:
            self.on_cancel_job(event.job_id, event.job_name)

    def on_running_panel_promote_requested(
        self, event: RunningPanel.PromoteRequested
    ) -> None:
        """Forward a running job's background-promotion request."""
        if self.on_promote_job:
            self.on_promote_job(event.job_id)

    def _get_active_chat(self) -> VerticalScroll | None:
        """Return the currently visible chat scroll."""
        try:
            if self._terrarium_tabs:
                tabs = self.query_one("#chat-tabs", TabbedContent)
                active = tabs.active
                if active:
                    scroll_id = active.replace("tab-", "chat-")
                    return self.query_one(f"#{scroll_id}", VerticalScroll)
            return self.query_one("#chat-scroll", VerticalScroll)
        except Exception as e:
            logger.warning(
                "Failed to get active chat scroll", error=str(e), exc_info=True
            )
            return None

    def get_active_tab_name(self) -> str:
        """Return the active tab name."""
        if not self._terrarium_tabs:
            return ""
        try:
            tabs = self.query_one("#chat-tabs", TabbedContent)
            active_id = tabs.active
            if active_id:
                return _id_to_name(active_id.replace("tab-", ""))
        except Exception as e:
            logger.warning("Failed to get active tab name", error=str(e), exc_info=True)
        return self._terrarium_tabs[0] if self._terrarium_tabs else ""

    async def reconcile_terrarium_tabs(self, tab_names: list[str]) -> None:
        """Reconcile mounted chat panes with the current terrarium topology."""
        desired = list(dict.fromkeys(tab_names))
        active_name = self.get_active_tab_name()
        try:
            tabs = self.query_one("#chat-tabs", TabbedContent)
        except Exception as e:
            logger.warning("Failed to find terrarium tabs", error=str(e), exc_info=True)
            return

        mounted: dict[str, TabPane] = {}
        for pane in tabs.query(TabPane):
            if pane.id:
                mounted[_id_to_name(pane.id.removeprefix("tab-"))] = pane

        for name, pane in list(mounted.items()):
            if name not in desired and pane.id:
                await tabs.remove_pane(pane.id)
                mounted.pop(name)

        for index, name in enumerate(desired):
            if name in mounted:
                continue
            pane = TabPane(
                name,
                VerticalScroll(
                    id=f"chat-{_safe_id(name)}",
                    classes="chat-tab-scroll",
                ),
                id=f"tab-{_safe_id(name)}",
            )
            next_name = next(
                (
                    candidate
                    for candidate in desired[index + 1 :]
                    if candidate in mounted
                ),
                None,
            )
            before = mounted[next_name].id if next_name else None
            await tabs.add_pane(pane, before=before)
            mounted[name] = pane

        self._terrarium_tabs = desired
        if desired:
            selected = active_name if active_name in desired else desired[0]
            tabs.active = f"tab-{_safe_id(selected)}"

    def action_interrupt(self) -> None:
        if self.on_interrupt:
            self.on_interrupt()

    def action_clear_output(self) -> None:
        chat = self._get_active_chat()
        if chat:
            chat.remove_children()

    def action_quit(self) -> None:
        self._stop_event.set()
        # Empty input wakes the consumer and is its shutdown sentinel.
        self._input_queue.put_nowait("")
        self.exit()

    def _active_tab_agent(self) -> Any:
        """Resolve the active tab's agent, falling back to the host agent."""
        if not self.tui_session:
            return None
        resolver = getattr(self.tui_session, "agent_for_tab", None)
        if callable(resolver):
            try:
                agent = resolver()
            except Exception as e:
                logger.warning("active-tab agent resolve failed", error=str(e))
                agent = None
            if agent is not None:
                return agent
        return getattr(self.tui_session, "host_agent", None)

    def action_open_modules(self) -> None:
        """Open the module manager for the active agent."""
        agent = self._active_tab_agent()
        if agent is None:
            return
        self.push_screen(ModulesModal(agent))

    def action_open_model_picker(self) -> None:
        """Open the model picker for the active agent."""
        agent = self._active_tab_agent()
        if agent is None:
            return
        self.push_screen(ModelPickerModal(agent))

    def action_open_drives(self) -> None:
        """Open the drive panel scoped to the focused creature."""
        # A module-level import would create a cycle through TUI session imports.
        from kohakuterrarium.builtins.tui.widgets.drive_panel import DriveScreen

        session = self.tui_session
        service = getattr(session, "drive_service", None) if session else None
        engine = getattr(session, "engine", None) if session else None
        self.push_screen(
            DriveScreen(service, engine, creature_id=self._focused_creature_id())
        )

    def _focused_creature_id(self) -> str:
        """Return the active creature ID, falling back from channel tabs."""
        active = self.get_active_tab_name() if self._terrarium_tabs else ""
        if active and not active.startswith("#"):
            return active
        return self._terrarium_tabs[0] if self._terrarium_tabs else ""

    def get_system_commands(self, screen: Screen):
        """Add the drive panel to the command palette."""
        yield from super().get_system_commands(screen)
        yield SystemCommand(
            "Drives", "Open the Drive record + settings panel", self.action_open_drives
        )

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        """Refresh session details for the newly active tab."""
        if not self.tui_session:
            return
        refresh = getattr(self.tui_session, "refresh_model_for_tab", None)
        if callable(refresh):
            refresh(self.get_active_tab_name())
        refresh_hints = getattr(self.tui_session, "refresh_command_hints_for_tab", None)
        if callable(refresh_hints):
            refresh_hints(self.get_active_tab_name())

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
            logger.warning(
                "Failed to clear status on stop thinking", error=str(e), exc_info=True
            )

    def _thinking_loop(self) -> None:
        idx = 0
        while self._thinking_active:
            frame = THINKING_FRAMES[idx % len(THINKING_FRAMES)]
            try:
                self.call_from_thread(self._set_status_text, frame)
            except Exception as e:
                logger.warning(
                    "Thinking animation loop ended", error=str(e), exc_info=True
                )
                break
            idx += 1
            time.sleep(0.3)

    def _set_status_text(self, text: str) -> None:
        try:
            self.query_one("#quick-status", Static).update(text)
        except Exception as e:
            logger.warning("Failed to set status text", error=str(e), exc_info=True)

    def _clear_status(self) -> None:
        try:
            self.query_one("#quick-status", Static).update("")
        except Exception as e:
            logger.warning("Failed to clear status", error=str(e), exc_info=True)


def _safe_id(name: str) -> str:
    """Encode a tab name as a reversible CSS-safe ID component."""
    return f"t_{name.encode('utf-8').hex()}"


def _id_to_name(safe: str) -> str:
    """Decode a CSS-safe tab ID component back to its original name."""
    if not safe.startswith("t_"):
        return safe
    try:
        return bytes.fromhex(safe[2:]).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return safe
