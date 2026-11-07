"""The ktui shell: one kohakutui app driving every creature of a running engine.

The shell owns the app, the composer, the active-items row and the footer, and consumes the
app's events on KT's own asyncio loop, so outputs, input and dialogs never need locks. Every
creature gets a :class:`KtuiOutput` and a :class:`CreatureView`; one creature holds the inline
view at a time (the others buffer, see ``view.py``). Messages typed while the agent works are
queued with a ``pending_id`` (ctrl+enter or ctrl+s interrupts first); ``@name`` sends one message to
another creature; slash commands run through the agent's registry with the engine's trusted
context for ``needs_engine`` commands.
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from kohakutui import App, Event
from kohakutui.ui import Button, Component, Composer, Footer, Node

from kohakuterrarium.builtins.ktui.attach import ComposerHelper
from kohakuterrarium.builtins.ktui.board import Board
from kohakuterrarium.builtins.ktui.chrome import Chrome
from kohakuterrarium.builtins.ktui.commands import command_list, run_command
from kohakuterrarium.builtins.ktui.config import KtuiConfig
from kohakuterrarium.builtins.ktui.format import tokens
from kohakuterrarium.builtins.ktui.config_edit import ConfigEdit
from kohakuterrarium.builtins.ktui.forms import Forms
from kohakuterrarium.builtins.ktui.interactions import Interactions
from kohakuterrarium.builtins.ktui.models import model_id
from kohakuterrarium.builtins.ktui.output import KtuiOutput
from kohakuterrarium.builtins.ktui.page_settings import estimate
from kohakuterrarium.builtins.ktui.onboarding import first_run
from kohakuterrarium.builtins.ktui.updates import notice as update_notice
from kohakuterrarium.builtins.ktui.sessions import Sessions
from kohakuterrarium.builtins.ktui.tool_bodies import MediaPlace
from kohakuterrarium.builtins.ktui.settings import Settings
from kohakuterrarium.builtins.ktui.shell_input import ShellInput
from kohakuterrarium.builtins.ktui.shell_extras import Attention
from kohakuterrarium.builtins.ktui.messages import MessageActions
from kohakuterrarium.builtins.ktui.runs import BATCH_SIZE
from kohakuterrarium.builtins.ktui.ui_block import UiContext, UiHost
from kohakuterrarium.builtins.ktui.tool_widgets import ToolWidgets
from kohakuterrarium.builtins.ktui.view import CreatureView
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# a paste longer than this many lines shows as one "[Pasted text …]" token in the input
FOLD_PASTE_LINES = 12

BINDINGS = {
    "ctrl+b": "jobs",
    "alt+j": "next_approval",
    "ctrl+k": "approve",
    "ctrl+x": "unqueue",
    "alt+v": "paste_image",
    "f1": "help",
    # full screen: show or hide the conversation rail and the side panel, pick a page,
    # step through the conversations
    "alt+r": "toggle_rail",
    "alt+s": "toggle_side",
    "ctrl+pagedown": "next_chat",
    "ctrl+pageup": "prev_chat",
    **{f"alt+{n}": f"page_{n}" for n in range(1, 6)},
}


@dataclass
class Entry:
    """One attached creature."""

    creature: Any
    view: CreatureView
    output: KtuiOutput
    previous_output: Any

    @property
    def agent(self) -> Any:
        return self.creature.agent


def _cost(usage: Any) -> str:
    """The estimated spend of this conversation (models with a known price)."""
    cost = estimate(usage.model, usage.tokens_in, usage.cached, usage.tokens_out)
    return f"~${cost:.3f}" if cost else ""


class KtuiShell(ShellInput):
    """Runs until ``/exit``, a double ctrl+c on an idle prompt, or the app stopping."""

    def __init__(
        self, engine: Any, focus_id: str, config: KtuiConfig, app: App | None = None
    ):
        self.engine = engine
        self.config = config
        mode = "fullscreen" if config.renderer == "fullscreen" else "inline"
        self.app = app or App(
            mode=mode, interrupt="event", theme=config.theme, links=config.links
        )
        self.mode = mode
        self.entries: dict[str, Entry] = {}
        self.focus_id = focus_id
        self.interactions = Interactions(self.app, self.refresh)
        self.ui_host = UiHost()
        self.tool_widgets = ToolWidgets(self.app)
        self.board = Board(self)
        self.chrome = Chrome(self)
        self.settings = Settings(self)
        self.messages = MessageActions(self)
        self.composing = ComposerHelper(self)
        self.sessions = Sessions(self)
        self.forms = Forms(self)
        self.config_edit = ConfigEdit(self)
        # the session /resume chose: the launcher resumes it after this one ends
        self.next_session: str | None = None
        self.attention = Attention(self)
        self.composer: Node | None = None
        self.footer: Node | None = None
        self.active: Node | None = None
        self._shown: tuple[str, str, str] = ("", "", "")
        self._tasks: set[asyncio.Task] = set()
        # turns started from here run to completion or interrupt, never get cancelled by us
        self._turns: set[asyncio.Task] = set()
        self._sending: dict[str, int] = {}
        self._picker_open = False
        self._done = asyncio.Event()

    # -- creatures ------------------------------------------------------

    def attach(self, creature: Any) -> Entry:
        """Makes ktui the creature's default output (the previous one is restored on exit)."""
        cid = creature.creature_id
        view = CreatureView(
            self.app,
            cid,
            creature.name,
            self.refresh,
            collapse_tools=self.config.verbosity != "high",
            batch_size=0 if self.config.verbosity == "high" else BATCH_SIZE,
            ui=self._ui_context(creature),
            focused=cid == self.focus_id,
        )
        view.usage.model = model_id(creature.agent)
        store = getattr(creature.agent, "session_store", None)
        remote = getattr(self.engine, "keeps_running", False)
        view.media = MediaPlace(
            session_path=None if remote else getattr(store, "path", None),
            server=self.engine.session.base_url if remote else None,
        )
        router = creature.agent.output_router
        entry = Entry(creature, view, KtuiOutput(self, view), router.default_output)
        router.default_output = entry.output
        self.entries[cid] = entry
        return entry

    def _ui_context(self, creature: Any) -> UiContext:
        cid, agent = creature.creature_id, creature.agent

        def turn() -> tuple[int, int] | None:
            index, branch = getattr(agent, "_turn_index", None), getattr(
                agent, "_branch_id", None
            )
            return (
                (index, branch)
                if isinstance(index, int) and isinstance(branch, int)
                else None
            )

        return UiContext(
            host=self.ui_host,
            send=lambda text: self.send(self.entries[cid], text),
            inline=lambda: self.mode == "inline",
            turn=turn,
            states=getattr(agent.output_router, "inline_ui", None),
            copy=self.app.copy,
        )

    def sync_creatures(self) -> None:
        """Follows the focus creature's graph: creatures that joined it are attached, creatures
        no longer in the engine are let go (the focus moves on when it was one of them).
        """
        try:
            alive = {c.creature_id: c for c in self.engine.list_creatures()}
        except Exception:
            return
        focus = alive.get(self.focus_id)
        for cid, creature in alive.items():
            joined = focus is not None and creature.graph_id == focus.graph_id
            if joined and cid not in self.entries:
                self.attach(creature)
                self.app.notify(
                    f"{creature.name} joined · @{creature.name} or ↓", "info"
                )
        if self.focus_id in self.entries and self.focus_id not in alive:
            # the focus is leaving: back to its chat first (a shown channel or page gives
            # way) while it is still there to show
            self.chrome.to_chat()
            self.chrome.leave_channel()
        for cid in [c for c in self.entries if c not in alive]:
            entry = self.entries.pop(cid)
            entry.view.doc.remove()
            self.app.notify(f"{entry.creature.name} left", "info")
        if self.focus_id not in self.entries and self.entries:
            # the next creature's chat
            self.focus_id = next(iter(self.entries))
            view = self.focused.view
            view.focused = False
            view.set_focused(True, f"── {view.name} ──")
        self._update_commands()
        self.refresh()

    async def _tick(self) -> None:
        """Full screen: the chrome's clocks and log tail move once a second."""
        while True:
            await asyncio.sleep(1.0)
            if self.mode == "fullscreen":
                self.chrome.refresh()

    async def _watch_engine(self) -> None:
        """Re-syncs the creatures on every structural change of the engine."""
        subscribe = getattr(self.engine, "subscribe", None)
        if not callable(subscribe):
            return
        kinds = {"creature_added", "creature_stopped", "topology_changed"}
        async for ev in subscribe():
            if str(getattr(ev.kind, "value", ev.kind)) in kinds:
                self.sync_creatures()

    def detach_all(self) -> None:
        for entry in self.entries.values():
            router = entry.agent.output_router
            if router.default_output is entry.output:
                router.default_output = entry.previous_output

    @property
    def focused(self) -> Entry:
        return self.entries[self.focus_id]

    @property
    def multi(self) -> bool:
        return len(self.entries) > 1

    def focus(self, cid: str) -> None:
        """Shows creature ``cid`` (``""``: the focused one, also back from a channel) on the
        Chat page."""
        cid = cid or self.focus_id
        if cid not in self.entries:
            return
        self.chrome.to_chat()
        if self.chrome.leave_channel() and cid == self.focus_id:
            view = self.focused.view
            view.set_focused(True, f"── {view.name} ──")
            self._update_commands()
            self.refresh()
            return
        if cid == self.focus_id:
            return
        self.focused.view.set_focused(False)
        self.focus_id = cid
        view = self.focused.view
        view.set_focused(True, f"── {view.name} ──")
        self._update_commands()
        self.refresh()

    def turn_finished(self, view: CreatureView) -> None:
        self.attention.turn_done(view.name)
        if not view.focused:
            where = "the rail" if self.mode == "fullscreen" else "↓"
            self.app.notify(f"{view.name} finished · {where} to switch to it", "info")

    # -- lifecycle ------------------------------------------------------

    async def run(self, resume_events: list[dict] | None = None) -> None:
        """Takes the terminal, replays ``resume_events`` into the focus view, serves events."""
        self.app.start()
        try:
            # nothing may print over the UI from here on: it goes to the Logs tab
            self.chrome.capture()
            self._mount()
            if resume_events:
                await self.focused.output.on_resume(resume_events)
            self.spawn(update_notice(self.app))
            self.spawn(first_run(self))
            consumer = asyncio.create_task(self._consume())
            watcher = asyncio.create_task(self._watch_engine())
            ticker = asyncio.create_task(self._tick())
            await self._done.wait()
            consumer.cancel()
            watcher.cancel()
            ticker.cancel()
        finally:
            # a session in a server keeps running when ktui leaves it
            if not getattr(self.engine, "keeps_running", False):
                for entry in self.entries.values():
                    if self.is_busy(entry):
                        entry.agent.interrupt()
            for task in list(self._tasks - self._turns):
                task.cancel()
            self.interactions.hide()
            self.tool_widgets.close_all()
            self.detach_all()
            self.attention.close()
            self.app.stop()
            # last: until the terminal is given back, stray output still goes to the Logs tab
            self.chrome.close()

    def exit(self) -> None:
        self._done.set()

    def _mount(self) -> None:
        app = self.app
        self.composer = app.bottom.add(
            Composer(
                placeholder="message · / commands · @ files and agents · ? keys",
                boxed=True,
                rich_submit=True,
                triggers=["@"],
                fold_paste=FOLD_PASTE_LINES,
                report_draft=True,
                modes=[{"prefix": "!", "label": "shell command"}],
            )
        )
        self.footer = app.bottom.add(Footer())
        for chord, action in BINDINGS.items():
            app.bind(chord, action)
        self.chrome.build()
        self._update_commands()
        self.refresh()
        self.composer.focus()

    def spawn(self, coro: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            logger.warning("ktui task failed", error=str(exc), exc_info=exc)
            self.focused.view.render("error", f"{type(exc).__name__}: {exc}")

    async def _consume(self) -> None:
        async for ev in self.app.aevents():
            try:
                await self._handle(ev)
            except Exception as exc:
                logger.warning(
                    "ktui event failed", kind=ev.kind, error=str(exc), exc_info=True
                )
        self.exit()

    async def _handle(self, ev: Event) -> None:
        if self.forms.handle(ev) or self.ui_host.handle(ev):
            return
        if self.chrome.handle(ev):
            return
        match ev.kind:
            case "submit" if self.composer is not None and ev.id == self.composer.id:
                await self._submit(ev.value)
            case "change" if self.composer is not None and ev.id == self.composer.id:
                self.composing.handle_change(ev.value)
            case "select" if self.active is not None and ev.id == self.active.id:
                self.spawn(self.board.open(section=str(ev.value or "") or None))
            case "action":
                name = str(ev.data.get("name", ""))
                if not self.messages.handle(name, ev.data.get("focus")):
                    self._action(name)
            case "interrupt":
                self._ctrl_c(int(ev.data.get("count", 1) or 1))
            case "mode_changed":
                self.mode = ev.data.get("mode", self.mode)
                if self.mode == "inline":
                    # pages are full screen only: inline is always the conversation
                    self.chrome.to_chat()
                for entry in self.entries.values():
                    entry.view.mode_changed(self.mode == "inline")
                # the chrome is kept up to date only while drawn
                self.refresh()
            case "focus":
                self.attention.focus(bool(ev.data.get("gained", True)))
            case "error":
                logger.debug("ktui engine error", message=ev.data.get("message"))

    def _action(self, name: str) -> None:
        match name:
            case "escape" if self.messages.cancel_edit():
                pass
            case "escape":
                view = self.focused.view
                if self.is_busy(self.focused):
                    self.focused.agent.interrupt()
                elif view.has_live_blocks and self.mode == "inline":
                    # idle: esc lets the live <ui> blocks go to scrollback (D26)
                    view.freeze_blocks()
                    self.refresh()
            case "jobs":
                self.spawn(self.board.open(jobs_only=True))
            case "next_approval":
                self.interactions.show_next()
            case "approve":
                self.interactions.approve_oldest()
            case "unqueue":
                self._unqueue()
            case "paste_image":
                self.composing.paste_image()
            case "help":
                self.spawn(self.show_help())
            case "toggle_rail" | "toggle_side" | "next_chat" | "prev_chat":
                self.chrome.action(name)
            case _ if name.startswith("page_"):
                self.chrome.action(name)

    def _ctrl_c(self, count: int) -> None:
        if self.is_busy(self.focused):
            self.focused.agent.interrupt()
        elif count >= 2:
            self.exit()
        else:
            self.app.notify("press ctrl+c again to exit", "info")

    # -- commands -------------------------------------------------------

    def _update_commands(self) -> None:
        if self.composer is not None:
            self.composer.update(commands=command_list(self.focused))

    async def command(self, text: str) -> None:
        """Runs a slash command for the focused creature and shows its result."""
        await run_command(self, text)

    async def panel(
        self, spec: Component, on_open: Callable[[Node], Any] | None = None
    ) -> Any:
        """A list or picker drawn where the input box is (both modes); one at a time.
        ``on_open`` gets the dialog's node, for dialogs that keep changing (a live trace).
        """
        if self._picker_open:
            return None
        spec.dock = "input"
        self._picker_open = True
        try:
            return await self.app.run_dialog_async(spec, on_open=on_open)
        finally:
            self._picker_open = False

    # -- footer and active row ------------------------------------------

    def refresh(self) -> None:
        """Redraws the footer, the active-items row and the full-screen chrome when changed."""
        if self.footer is None or not self.entries:
            return
        self.composing.follow_conversation()
        self.chrome.refresh()
        self.attention.update()
        view = self.focused.view
        usage = view.usage
        left = f"{view.name} · {usage.model}" if usage.model else view.name
        channel = self.chrome.channel
        if channel is not None:
            left = f"#{channel.name} · you send as human"
        pct = usage.context_pct()
        right = " · ".join(
            part
            for part in (
                f"ctx {pct}%" if pct is not None else "",
                (
                    f"in {tokens(usage.tokens_in)} (cache {tokens(usage.cached)}) out {tokens(usage.tokens_out)}"
                    if usage.tokens_in or usage.tokens_out
                    else ""
                ),
                _cost(usage),
            )
            if part
        )
        segments = self.board.segments()
        active = " · ".join(seg["label"] for seg in segments)
        if (left, right, active) == self._shown:
            return
        self._shown = (left, right, active)
        self.footer.update(left=left, right=right)

        hint = "  ←→ choose · enter opens · ↑ back to the input"
        if segments and self.active is None:
            self.active = self.app.bottom.add(
                Button(segments=segments, focused_hint=hint), index=1
            )
        elif segments and self.active is not None:
            self.active.update(segments=segments)
        elif self.active is not None:
            self.active.remove()
            self.active = None
