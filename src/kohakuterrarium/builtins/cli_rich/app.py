"""RichCLIApp — single prompt_toolkit Application owning the bottom of the terminal.

Architecture (mirroring Ink/ratatui — one render loop, one tree):

  ┌──────────────────────────────────────┐
  │   real terminal scrollback           │  ← committed via app.run_in_terminal()
  │   (banner, user msgs, finished       │     prompt_toolkit moves the cursor
  │    assistant msgs, tool result       │     above the app area, lets us print,
  │    panels, …)                        │     then redraws below.
  ├──────────────────────────────────────┤  ← top of the Application area
  │   live status window                 │  ← FormattedTextControl returning ANSI
  │   (streaming msg + active tools +    │     text rendered from LiveRegion.
  │    bg strip + compaction banner)     │     dont_extend_height=True; hidden
  │                                      │     when LiveRegion has no content.
  ├──────────────────────────────────────┤
  │ ┌─ message ──────────────────────┐   │  ← Frame(TextArea), the bordered box
  │ │ ▶ user types here              │   │     the user explicitly asked for.
  │ │   multiline, history, /complete│   │
  │ └────────────────────────────────┘   │
  │   in 1.2k · out 567 · model · /help  │  ← single-line footer
  └──────────────────────────────────────┘  ← bottom of the terminal

There is exactly ONE renderer (prompt_toolkit). app.invalidate() schedules
a coalesced redraw. Output that should land in scrollback is printed via
app.run_in_terminal(callback) — prompt_toolkit erases the app area, runs
the callback (whose stdout writes go straight to scrollback), then
redraws the app area below the cursor's new position.
"""

import asyncio
import sys
import weakref
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.app_drive import AppDriveMixin
from kohakuterrarium.builtins.cli_rich.app_multi import AppMultiCreatureMixin
from kohakuterrarium.builtins.cli_rich.app_output import AppOutputMixin
from kohakuterrarium.builtins.cli_rich.app_pickers import AppPickersMixin
from kohakuterrarium.builtins.cli_rich.commit import ScrollbackCommitter, SessionReplay
from kohakuterrarium.builtins.cli_rich.composer import Composer
from kohakuterrarium.builtins.cli_rich.dialogs.bus_overlay import BusInteractiveOverlay
from kohakuterrarium.builtins.cli_rich.dialogs.drive_overlay import DriveOverlay
from kohakuterrarium.builtins.cli_rich.dialogs.model_picker import ModelPicker
from kohakuterrarium.builtins.cli_rich.dialogs.module_picker import ModulePicker
from kohakuterrarium.builtins.cli_rich.dialogs.settings import SettingsOverlay
from kohakuterrarium.builtins.cli_rich.focus import FocusController, parse_at_name
from kohakuterrarium.builtins.cli_rich.hint_bar import SlashHintBar
from kohakuterrarium.builtins.cli_rich.live_region import LiveRegion
from kohakuterrarium.builtins.cli_rich.runtime import (
    StderrToLogger,
    disable_enhanced_keyboard,
    enable_enhanced_keyboard,
    make_output,
    spawn,
)
from kohakuterrarium.builtins.cli_rich.theme import COLOR_BANNER
from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)
from kohakuterrarium.llm.profiles import list_all as list_all_presets
from kohakuterrarium.modules.user_command.base import parse_slash_command
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_WIDTH = 100


class RichCLIApp(AppPickersMixin, AppOutputMixin, AppMultiCreatureMixin, AppDriveMixin):
    """Orchestrate the Rich CLI lifecycle, layout, input, and overlays."""

    def __init__(self, agent: Any):
        self.agent = agent
        self.live_region = LiveRegion()
        self.hint_bar = SlashHintBar()
        self.model_picker = ModelPicker(
            load_presets=self._load_presets_for_picker,
            on_apply=self._apply_model_selector,
        )
        self.settings_overlay = SettingsOverlay(get_engine=lambda: self.engine)
        self.drive_overlay = DriveOverlay(
            get_engine=lambda: self.engine,
            get_creature_id=self._drive_focus_id,
            schedule=lambda coro: spawn(coro),
            on_invalidate=self._invalidate,
            on_close=self._stop_drive_watch,
        )
        self._drive_watch_task: asyncio.Task | None = None
        self.module_picker = ModulePicker(get_agent=lambda: self.agent)
        self.bus_overlay = BusInteractiveOverlay(
            get_router=lambda: getattr(self.agent, "output_router", None),
            get_textarea_text=self._get_composer_text,
            clear_textarea=self._set_composer_text,
        )
        self._exit_requested = False
        self._processing = False
        self._command_registry: dict = {}
        self._command_registry_agents: list[weakref.ReferenceType] = []
        self._pending_task: asyncio.Task | None = None
        self._ctrl_c_armed = False
        self._ctrl_c_reset_task: asyncio.Task | None = None
        self._render_ticker_task: asyncio.Task | None = None

        # Scrollback writes must run outside prompt_toolkit's live area.
        self._scroll_console = Console(
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
            soft_wrap=False,
            emoji=False,
        )
        self.committer = ScrollbackCommitter(self)

        # The canonical profile name keeps the footer consistent with /model.
        model = agent.llm_identifier() or getattr(agent.llm, "model", "") or ""
        if model:
            self.live_region.update_footer_model(model)
        max_ctx = getattr(agent.llm, "_profile_max_context", 0) or 0
        if max_ctx:
            self.live_region.footer._max_context = max_ctx

        # The Application layout depends on the composer's controls and bindings.
        self.composer = Composer(
            creature_name=getattr(agent.config, "name", "creature"),
            on_submit=self._handle_submit,
            on_interrupt=self._on_interrupt,
            on_ctrl_c=self._on_ctrl_c,
            on_exit=self._on_exit,
            on_clear_screen=self._on_clear_screen,
            on_backgroundify=self._on_backgroundify,
            on_cancel_bg=self._on_cancel_bg,
            on_toggle_expand=self._on_toggle_expand,
            picker_key_handler=self._picker_handle_key,
            picker_text_handler=self._picker_handle_text,
            picker_captures_input=self._picker_captures_input,
            on_focus_next=self.focus_next,
            on_focus_prev=self.focus_prev,
            on_open_overlay=self.open_agent_overlay,
        )

        self.app: Application | None = None
        self.multi_creature_enabled = False
        self.engine = None
        self.focus_controller = FocusController()
        self.live_regions = {}
        self.draft_by_creature = {}
        self.roster = None
        self.agent_overlay = None
        self.peek_panel = None

    def setup_single_creature(self, engine: Any, focus_creature_id: str) -> None:
        """Bind engine and focus state for a single-creature run."""
        self.engine = engine
        self.focus_controller = FocusController(
            creature_ids=[focus_creature_id], focus_id=focus_creature_id
        )

    async def run(self) -> None:
        """Run the rich CLI loop until exit."""
        self._wire_command_registry()
        self._print_banner()  # The live Application does not exist yet.

        self.app = self._build_application()

        # Capture process-wide state before any setup can fail.
        loop = asyncio.get_running_loop()
        prev_handler = loop.get_exception_handler()
        prev_stderr = sys.stderr

        try:
            # Background tracebacks must not corrupt the live region.
            loop.set_exception_handler(self._loop_exception_handler)
            # Redirect stray library and asyncio diagnostics away from the terminal.
            sys.stderr = StderrToLogger()
            # Unsupported terminals safely ignore these keyboard protocols.
            enable_enhanced_keyboard()

            # Redraw only during animation so idle mouse selections remain intact.
            self._render_ticker_task = spawn(self._render_ticker())

            # Keep prompt_toolkit's default SIGINT handling so Ctrl+C reaches its
            # key binding instead of raising KeyboardInterrupt, especially on Windows.
            await self.app.run_async()
        finally:
            disable_enhanced_keyboard()
            sys.stderr = prev_stderr
            loop.set_exception_handler(prev_handler)
            if self._render_ticker_task and not self._render_ticker_task.done():
                self._render_ticker_task.cancel()
                try:
                    await self._render_ticker_task
                except (asyncio.CancelledError, Exception):
                    pass
            if self._ctrl_c_reset_task and not self._ctrl_c_reset_task.done():
                self._ctrl_c_reset_task.cancel()
                try:
                    await self._ctrl_c_reset_task
                except (asyncio.CancelledError, Exception):
                    pass
            if self._pending_task and not self._pending_task.done():
                self._pending_task.cancel()
                try:
                    await self._pending_task
                except (asyncio.CancelledError, Exception):
                    pass
            self.app = None
            print()  # Leave the shell cursor on a clean line.

    async def _render_ticker(self) -> None:
        """Refresh active animations without disturbing idle text selection."""
        idle_sleep = 0.5
        active_sleep = 0.2
        while True:
            try:
                if self.live_region.needs_animation:
                    self._invalidate()
                    await asyncio.sleep(active_sleep)
                else:
                    await asyncio.sleep(idle_sleep)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "render ticker iteration failed", error=str(e), exc_info=True
                )
                await asyncio.sleep(active_sleep)

    def _loop_exception_handler(self, loop, context: dict) -> None:
        """Log asyncio loop exceptions without writing into the live region."""
        message = context.get("message", "<no message>")
        exc = context.get("exception")
        if exc is not None:
            logger.error("loop exception: %s", message, exc_info=exc)
        else:
            logger.error("loop exception: %s | context=%r", message, context)

    def _build_application(self) -> Application:
        status_control = FormattedTextControl(
            text=self._status_text,
            focusable=False,
            show_cursor=False,
        )
        status_window = Window(
            content=status_control,
            dont_extend_height=True,
            wrap_lines=False,
            always_hide_cursor=True,
        )
        status_container = ConditionalContainer(
            content=status_window,
            filter=Condition(
                lambda: self.bus_overlay.visible
                or self.model_picker.visible
                or self.module_picker.visible
                or self.settings_overlay.visible
                or self.drive_overlay.visible
                or (self.agent_overlay is not None and self.agent_overlay.visible)
                or self.live_region.has_content
            ),
        )

        # Rules delimit the composer without adding a bulky titled frame.
        input_top_rule = Window(
            char="─",
            height=Dimension.exact(1),
            style="class:input.rule",
        )
        input_bottom_rule = Window(
            char="─",
            height=Dimension.exact(1),
            style="class:input.rule",
        )

        # Hints appear only for slash-prefixed input with matching commands.
        hint_control = FormattedTextControl(
            text=self._hint_text,
            focusable=False,
            show_cursor=False,
        )
        hint_window = Window(
            content=hint_control,
            height=Dimension.exact(1),
            wrap_lines=False,
            always_hide_cursor=True,
        )
        hint_container = ConditionalContainer(
            content=hint_window,
            filter=Condition(self._hint_has_content),
        )

        roster_container = ConditionalContainer(
            content=Window(
                content=FormattedTextControl(
                    text=self._roster_text, focusable=False, show_cursor=False
                ),
                height=Dimension.exact(1),
                wrap_lines=False,
                always_hide_cursor=True,
            ),
            filter=Condition(self.roster_visible),
        )

        footer_control = FormattedTextControl(
            text=self._footer_text,
            focusable=False,
            show_cursor=False,
        )
        footer_window = Window(
            content=footer_control,
            height=Dimension.exact(1),
            wrap_lines=False,
            always_hide_cursor=True,
        )

        root_container = HSplit(
            [
                status_container,
                roster_container,
                hint_container,
                input_top_rule,
                self.composer.text_area,
                input_bottom_rule,
                footer_window,
            ]
        )

        layout = Layout(
            container=root_container, focused_element=self.composer.text_area
        )

        style = Style.from_dict(
            {
                "input.rule": "#555555",
            }
        )

        return Application(
            layout=layout,
            key_bindings=self.composer.key_bindings,
            full_screen=False,
            mouse_support=False,
            erase_when_done=False,
            color_depth=ColorDepth.TRUE_COLOR,
            style=style,
            # A fixed refresh interval destroys terminal text selection while idle.
            output=make_output(),
        )

    def _status_text(self):
        width = self._terminal_width()
        # The active overlay exclusively owns the status area.
        if self.bus_overlay.visible:
            ansi = self.bus_overlay.render(width)
            return ANSI(ansi) if ansi else ""
        if self.model_picker.visible:
            ansi = self.model_picker.render(width)
            return ANSI(ansi) if ansi else ""
        if self.module_picker.visible:
            ansi = self.module_picker.render(width)
            return ANSI(ansi) if ansi else ""
        if self.settings_overlay.visible:
            ansi = self.settings_overlay.render(width)
            return ANSI(ansi) if ansi else ""
        if self.drive_overlay.visible:
            ansi = self.drive_overlay.render(width)
            return ANSI(ansi) if ansi else ""
        if self.agent_overlay is not None and self.agent_overlay.visible:
            ansi = self.agent_overlay_ansi(width)
            return ANSI(ansi) if ansi else ""
        ansi = self.live_region.to_ansi(width)
        if not ansi:
            return ""
        return ANSI(ansi)

    def _roster_text(self):
        ansi = self.roster_ansi(self._terminal_width())
        return ANSI(ansi) if ansi else ""

    def _hint_text(self):
        width = self._terminal_width()
        try:
            buffer_text = self.composer.text_area.buffer.document.text
        except Exception:
            return ""
        ansi = self.hint_bar.render(buffer_text, width)
        return ANSI(ansi) if ansi else ""

    def _hint_has_content(self) -> bool:
        try:
            buffer_text = self.composer.text_area.buffer.document.text
        except Exception:
            return False
        if not self.hint_bar.is_active(buffer_text):
            return False
        return bool(self.hint_bar._matches(buffer_text[1:].lower()))

    def _footer_text(self):
        width = self._terminal_width()
        # Reading the current Document avoids a separate cursor-change hook.
        try:
            doc = self.composer.text_area.buffer.document
            total_lines = doc.line_count
            if total_lines >= 2:
                self.live_region.footer.update_cursor(
                    line=doc.cursor_position_row + 1,
                    col=doc.cursor_position_col + 1,
                    total_lines=total_lines,
                )
            else:
                self.live_region.footer.update_cursor(0, 0, 0)
        except Exception as e:
            logger.warning("cursor pos update failed", error=str(e), exc_info=True)
        ansi = self.live_region.footer_to_ansi(width)
        return ANSI(ansi) if ansi else ""

    def _terminal_width(self) -> int:
        if self.app is None:
            return DEFAULT_WIDTH
        try:
            return self.app.output.get_size().columns
        except Exception as e:
            logger.warning(
                "Could not determine terminal width", error=str(e), exc_info=True
            )
            return DEFAULT_WIDTH

    def _handle_submit(self, text: str) -> None:
        """Dispatch a non-empty composer submission."""
        if not text.strip():
            return

        # Mid-turn input remains pending until the agent confirms its injection.
        is_slash = text.startswith("/")
        is_at_name = self.multi_creature_enabled and text.startswith("@")
        if self._processing and not is_slash and not is_at_name:
            self.live_region.add_queued_input(text)
            self._invalidate()
            spawn(self._mid_turn_inject(text))
            return

        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
            # The engine owns the active turn, so cancelling this wrapper is insufficient.
            if self._processing:
                self.agent.interrupt()

        # Resolve @name before slash commands so targeted commands reach that creature.
        if self.multi_creature_enabled:
            redirect = parse_at_name(text)
            if redirect is not None:
                if redirect.is_broadcast:
                    self.commit_user_message_broadcast(text)
                    self._pending_task = spawn(self.broadcast_to_all(redirect.payload))
                else:
                    target = self.resolve_creature_by_name(redirect.name)
                    if target is None:
                        self.on_processing_error(
                            "@name", f"unknown creature: @{redirect.name}"
                        )
                        self._invalidate()
                        return
                    self.commit_user_message_for(target.creature_id, text)
                    self._pending_task = spawn(
                        self.inject_to_creature(target.creature_id, redirect.payload)
                    )
                return

        self._commit_user_message(text)

        if text.startswith("/"):
            self._pending_task = spawn(self._handle_slash(text))
            return

        self._processing = True
        self.live_region.set_processing(True)
        self._invalidate()

        async def _send():
            try:
                await self.agent.inject_input(text, source="cli")
            except Exception as e:
                logger.exception("Error processing input", error=str(e))
            finally:
                self._processing = False
                self.live_region.set_processing(False)
                # Close a deferred rule even when the turn ends without more output.
                self.committer.flush_block_close()
                self._invalidate()

        self._pending_task = spawn(_send())

    async def _mid_turn_inject(self, text: str) -> None:
        try:
            await self.agent.inject_input(text, source="cli")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Mid-turn inject failed", error=str(e))

    def _wire_command_registry(self) -> None:
        # The live registry includes package, constructor, and plugin commands.
        registry = self._agent_command_registry()
        self.composer.set_command_registry(registry)
        self.composer.set_command_context(agent=self.agent)
        self.hint_bar.set_registry(registry)
        self._command_registry = registry
        # Refresh command surfaces when plugins change the registry.
        self._command_registry_agents = [
            ref for ref in self._command_registry_agents if ref() is not None
        ]
        if not any(ref() is self.agent for ref in self._command_registry_agents):
            add_listener = getattr(self.agent, "add_user_command_listener", None)
            if callable(add_listener):
                app_ref = weakref.ref(self)
                agent_ref = weakref.ref(self.agent)

                def on_commands_changed(commands: dict) -> None:
                    app = app_ref()
                    agent = agent_ref()
                    if app is not None and agent is not None:
                        app._on_agent_user_commands_changed(agent, commands)

                add_listener(on_commands_changed)
            self._command_registry_agents.append(weakref.ref(self.agent))

    def _agent_command_registry(self) -> dict:
        lister = getattr(self.agent, "list_user_commands", None)
        if callable(lister):
            live = lister()
            if live:
                return dict(live)
        registry: dict = {}
        for name in list_builtin_user_commands():
            cmd = get_builtin_user_command(name)
            if cmd:
                registry[name] = cmd
        return registry

    def _on_agent_user_commands_changed(self, agent: Any, commands: dict) -> None:
        """Apply command refreshes only for the currently focused agent."""
        if agent is not self.agent:
            return
        self._on_user_commands_changed(commands)

    def _on_user_commands_changed(self, commands: dict) -> None:
        """Re-point the composer / hint bar at a refreshed command registry."""
        registry = dict(commands)
        self._command_registry = registry
        try:
            self.composer.set_command_registry(registry)
            self.hint_bar.set_registry(registry)
        except Exception:  # pragma: no cover - command refresh is best-effort UI wiring
            pass
        self._invalidate()

    async def _handle_slash(self, text: str) -> None:
        name, args = parse_slash_command(text)

        # Bare /model is interactive; selectors use the command implementation.
        if name == "model" and not args.strip():
            self.model_picker.open()
            self._invalidate()
            return

        # Settings commands are interactive but remain registered for discovery.
        if name in ("settings", "config") and not args.strip():
            self.settings_overlay.open()
            self._invalidate()
            return

        # Bare /drives opens the overlay; subcommands retain scriptable text output.
        if name in ("drives", "drive") and not args.strip():
            self.drive_overlay.open()
            self._start_drive_watch()
            self._invalidate()
            return

        # Listing and editing need the picker; one-shot module actions stay textual.
        if name in ("module", "modules", "mod"):
            stripped = args.strip()
            sub, _, rest = stripped.partition(" ")
            if not stripped or sub.lower() == "list":
                self.module_picker.open()
                self._invalidate()
                return
            if sub.lower() == "edit" and rest.strip():
                self.module_picker.open(edit_target=rest.strip())
                self._invalidate()
                return

        # Topology commands require both engine and focused-creature context.
        if await self.dispatch_topology_command(name, args):
            return

        try:
            result = await self.agent._try_slash_command_text(text)
        except Exception as e:
            self._commit_text(f"[red]Command error:[/red] {e}")
            return

        if result is None:
            self._commit_text(f"[red]Unknown command:[/red] /{name}")
            return

        if result.error:
            self._commit_text(f"[red]{result.error}[/red]")
        elif result.output and result.consumed:
            self._commit_text(result.output)
        elif result.output:
            self._processing = True
            self.live_region.set_processing(True)
            self._invalidate()

            async def _send_skill_turn():
                try:
                    await self.agent.inject_input(result.output, source="cli")
                except Exception as e:
                    logger.exception("Error processing skill slash input", error=str(e))
                finally:
                    self._processing = False
                    self.live_region.set_processing(False)
                    self.committer.flush_block_close()
                    self._invalidate()

            self._pending_task = spawn(_send_skill_turn())

        if name in ("exit", "quit"):
            self._exit_requested = True
            if self.app:
                self.app.exit()

    def _commit_renderable(self, renderable: Any) -> None:
        self.committer.renderable(renderable)

    def _commit_text(self, markup: str) -> None:
        self.committer.text(markup)

    def _commit_user_message(self, text: str) -> None:
        self.committer.user_message(text)

    def _commit_blank_line(self) -> None:
        self.committer.blank_line()

    def _commit_ansi(self, ansi: str) -> None:
        self.committer.ansi(ansi)

    def replay_session(self, events: list[dict]) -> None:
        """Restore footer metrics and replay session events to scrollback."""
        self._restore_footer_from_events(events)
        SessionReplay(self).replay(events)

    def _restore_footer_from_events(self, events: list[dict]) -> None:
        """Reconstruct cumulative token and context metrics from replay events."""
        if not events:
            return
        total_in = 0
        total_out = 0
        total_cached = 0
        last_prompt = 0
        max_ctx = 0
        for evt in events:
            # Session readers may return wrapped or flat event records.
            etype = evt.get("type") or evt.get("etype") or ""
            data = evt.get("data") if isinstance(evt.get("data"), dict) else evt
            if etype == "token_usage":
                prompt = int(data.get("prompt_tokens", 0) or 0)
                completion = int(data.get("completion_tokens", 0) or 0)
                cached = int(data.get("cached_tokens", 0) or 0)
                total_in += prompt
                total_out += completion
                total_cached += cached
                if prompt > 0:
                    last_prompt = prompt
            elif etype == "session_info":
                ctx = int(data.get("max_context", 0) or 0)
                if ctx:
                    max_ctx = ctx
        footer = self.live_region.footer
        if total_in or total_out or last_prompt:
            footer.restore_tokens(
                input_total=total_in,
                output_total=total_out,
                cached_total=total_cached,
                last_prompt=last_prompt,
            )
        if max_ctx:
            footer._max_context = max_ctx

    def _invalidate(self) -> None:
        if self.app is not None:
            self.app.invalidate()

    def _on_interrupt(self) -> None:
        self._ctrl_c_armed = False
        if self._ctrl_c_reset_task and not self._ctrl_c_reset_task.done():
            self._ctrl_c_reset_task.cancel()
            self._ctrl_c_reset_task = None
        if self._processing and self.agent:
            try:
                self.agent.interrupt()
            except Exception as e:
                logger.exception("Interrupt failed", error=str(e))

    def _on_ctrl_c(self) -> None:
        if self._processing:
            self._on_interrupt()
            return
        if self._ctrl_c_armed:
            self._exit_requested = True
            if self.app:
                self.app.exit()
            return
        self._ctrl_c_armed = True
        self._commit_text("[dim]Press Ctrl+C again to exit, or Ctrl+D to quit.[/dim]")
        self._invalidate()

        async def _reset_ctrl_c() -> None:
            try:
                await asyncio.sleep(1.5)
                self._ctrl_c_armed = False
            except asyncio.CancelledError:
                raise
            finally:
                self._ctrl_c_reset_task = None

        if self._ctrl_c_reset_task and not self._ctrl_c_reset_task.done():
            self._ctrl_c_reset_task.cancel()
        self._ctrl_c_reset_task = spawn(_reset_ctrl_c())

    def _on_backgroundify(self) -> None:
        """Promote the latest running direct tool/sub-agent to background."""
        job_id = self.live_region.latest_running_direct_job_id()
        if not job_id:
            return
        promote = getattr(self.agent, "_promote_handle", None)
        if promote is None:
            return
        try:
            promote(job_id)
        except Exception as e:
            logger.exception("backgroundify failed", error=str(e))

    def _on_cancel_bg(self) -> None:
        """Cancel the most recent backgrounded job."""
        latest = self.live_region.latest_running_bg_job_id()
        if latest is None:
            return
        job_id, name = latest
        cancel = getattr(self.agent, "_cancel_job", None)
        if cancel is None:
            return
        try:
            cancel(job_id, name)
        except Exception as e:
            logger.exception("cancel-bg failed", error=str(e))

    def _on_exit(self) -> None:
        self._exit_requested = True
        # Wake supported input modules so teardown does not block on pending input.
        request_exit = getattr(self.agent.input, "request_exit", None)
        if callable(request_exit):
            try:
                request_exit()
            except Exception as e:
                logger.warning("input request_exit failed", error=str(e), exc_info=True)

    def _on_toggle_expand(self) -> None:
        """Expand/collapse the most recent top-level tool block."""
        if self.live_region.toggle_latest_tool_expand():
            self._invalidate()

    def _load_presets_for_picker(self) -> list[dict[str, Any]]:
        """Load the list of presets for the model picker."""
        try:
            return list_all_presets()
        except Exception as e:
            logger.warning("Model picker: failed to load presets", error=str(e))
            return []

    def _apply_model_selector(self, selector: str) -> None:
        """Apply a picker selection through the standard /model command path."""
        if not selector:
            return
        self._pending_task = spawn(self._handle_slash(f"/model {selector}"))

    def _get_composer_text(self) -> str:
        """Return composer text for interactive overlay prompts."""
        try:
            return self.composer.text_area.buffer.document.text
        except Exception:
            return ""

    def _set_composer_text(self, text: str = "") -> None:
        """Reset the composer textarea contents (or set to a default)."""
        try:
            buf = self.composer.text_area.buffer
            buf.reset()
            if text:
                buf.insert_text(text)
        except Exception as e:
            logger.warning("set_composer_text failed", error=str(e), exc_info=True)

    def _on_clear_screen(self) -> None:
        # Clearing through the committer preserves prompt_toolkit's live area.
        self.committer.ansi("\x1b[3J\x1b[H\x1b[2J")
        # Invisible paste placeholders no longer need their retained payloads.
        self.composer.paste_store.clear()

    def _print_banner(self) -> None:
        name = getattr(self.agent.config, "name", "agent")
        # The canonical profile name keeps model labels consistent across surfaces.
        model = (
            self.agent.llm_identifier() or getattr(self.agent.llm, "model", "") or ""
        )
        banner = Text()
        banner.append("KohakuTerrarium", style=COLOR_BANNER)
        banner.append(" · ", style="dim")
        banner.append(name, style="bold")
        if model:
            banner.append(f" ({model})", style="dim")
        self._scroll_console.print(banner)
        self._scroll_console.print(
            Text("Type /help for shortcuts · Ctrl+D to quit", style="dim")
        )
        self._scroll_console.print()
