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
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.layout import (
    ConditionalContainer,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.app_output import AppOutputMixin
from kohakuterrarium.builtins.cli_rich.commit import ScrollbackCommitter, SessionReplay
from kohakuterrarium.builtins.cli_rich.composer import Composer
from kohakuterrarium.builtins.cli_rich.dialogs.model_picker import ModelPicker
from kohakuterrarium.builtins.cli_rich.dialogs.settings import SettingsOverlay
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
from kohakuterrarium.modules.user_command.base import (
    UserCommandContext,
    parse_slash_command,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_WIDTH = 100


class RichCLIApp(AppOutputMixin):
    """Single-Application orchestrator for ``--mode cli``.

    Output events from the agent's OutputRouter (``on_text_chunk``,
    ``on_tool_start``, …) are provided by ``AppOutputMixin`` — see
    ``app_output.py`` — to keep this file focused on lifecycle + layout.
    """

    def __init__(self, agent: Any):
        self.agent = agent
        self.live_region = LiveRegion()
        self.hint_bar = SlashHintBar()
        self.model_picker = ModelPicker(
            load_presets=self._load_presets_for_picker,
            on_apply=self._apply_model_selector,
        )
        self.settings_overlay = SettingsOverlay()
        self._exit_requested = False
        self._processing = False
        self._command_registry: dict = {}
        self._pending_task: asyncio.Task | None = None

        # Console used only for committing to scrollback (via run_in_terminal).
        self._scroll_console = Console(
            force_terminal=True,
            color_system="truecolor",
            legacy_windows=False,
            soft_wrap=False,
            emoji=False,
        )
        self.committer = ScrollbackCommitter(self)

        # Initialize footer with model info
        model = getattr(agent.llm, "model", "") or ""
        if model:
            self.live_region.update_footer_model(model)
        max_ctx = getattr(agent.llm, "_profile_max_context", 0) or 0
        if max_ctx:
            self.live_region.footer._max_context = max_ctx

        # Composer (built before the Application so we can pass its
        # text_area + key_bindings into the Layout).
        self.composer = Composer(
            creature_name=getattr(agent.config, "name", "creature"),
            on_submit=self._handle_submit,
            on_interrupt=self._on_interrupt,
            on_exit=self._on_exit,
            on_clear_screen=self._on_clear_screen,
            on_backgroundify=self._on_backgroundify,
            on_cancel_bg=self._on_cancel_bg,
            on_toggle_expand=self._on_toggle_expand,
            picker_key_handler=self._picker_handle_key,
            picker_text_handler=self._picker_handle_text,
            picker_captures_input=self._picker_captures_input,
        )

        self.app: Application | None = None

    # ── Public lifecycle ──

    async def run(self) -> None:
        """Run the rich CLI loop until exit."""
        self._wire_command_registry()
        self._print_banner()  # Banner goes to scrollback (no app yet)

        self.app = self._build_application()

        # Capture previous values BEFORE the try block so ``finally``
        # can safely restore them even if we bail out early.
        loop = asyncio.get_running_loop()
        prev_handler = loop.get_exception_handler()
        prev_stderr = sys.stderr

        try:
            # Route asyncio loop exceptions to the file logger so random
            # background-task crashes don't paint garbage on the screen.
            loop.set_exception_handler(self._loop_exception_handler)
            # Capture stderr for the duration of the app — every stray
            # write (asyncio task warnings, prompt_toolkit error prints,
            # library tracebacks) goes to the log file instead of
            # corrupting the live region.
            sys.stderr = StderrToLogger()
            # Ask the terminal to emit Shift+Enter / Ctrl+Enter as
            # distinct keys (xterm modifyOtherKeys=2 + kitty CSI u).
            # Terminals that don't support either silently ignore.
            enable_enhanced_keyboard()

            await self.app.run_async()
        finally:
            disable_enhanced_keyboard()
            sys.stderr = prev_stderr
            loop.set_exception_handler(prev_handler)
            # Cancel any in-flight agent task
            if self._pending_task and not self._pending_task.done():
                self._pending_task.cancel()
                try:
                    await self._pending_task
                except (asyncio.CancelledError, Exception):
                    pass
            self.app = None
            print()  # Trailing newline so the terminal cursor is clean

    def _loop_exception_handler(self, loop, context: dict) -> None:
        """Send asyncio loop exceptions to the file logger only.

        Without this, asyncio's default handler prints the traceback to
        stderr — which corrupts the live region. Sending to the logger
        keeps the screen clean while still leaving a trail in the log file.
        """
        message = context.get("message", "<no message>")
        exc = context.get("exception")
        if exc is not None:
            logger.error("loop exception: %s", message, exc_info=exc)
        else:
            logger.error("loop exception: %s | context=%r", message, context)

    # ── Application + Layout ──

    def _build_application(self) -> Application:
        # Live status window — text comes from LiveRegion.to_ansi().
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
                lambda: self.model_picker.visible
                or self.settings_overlay.visible
                or self.live_region.has_content
            ),
        )

        # Input area — no more Frame(title="message"). User flagged the
        # labelled box as "not what other CLIs look like" and pointed
        # out the bottom separator mattered most. We replace the full
        # Frame with a pair of dim horizontal rules (top + bottom) that
        # bracket the textarea. The bottom rule doubles as the visual
        # boundary between composer and footer, which the Frame used to
        # provide via its lower edge.
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

        # Slash-command hint bar — renders as a single line between the
        # input frame and the footer. Visible only when the buffer starts
        # with "/" and has matches. Think of it as the always-on version
        # of the completion dropdown: even before you type a letter, the
        # bar shows you what commands exist at all.
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

        # Footer (single line).
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

        # Layout order top → bottom:
        #   status (live region)
        #   hint bar (slash-command hints, above the input per user ask)
        #   top rule  ─────────────────────────
        #   textarea
        #   bottom rule  ──────────────────────  (separates input from footer)
        #   footer
        root_container = HSplit(
            [
                status_container,
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
            # 5 fps redraw — drives the spinner animation and elapsed-time
            # updates without burning CPU.
            refresh_interval=0.2,
            output=make_output(),
        )

    # ── FormattedTextControl callbacks ──

    def _status_text(self):
        width = self._terminal_width()
        # When an overlay is open, it owns the status area — the
        # live region's normal content (streaming message, tools) is
        # hidden until the overlay closes, so all user attention is on
        # the overlay.
        if self.model_picker.visible:
            ansi = self.model_picker.render(width)
            return ANSI(ansi) if ansi else ""
        if self.settings_overlay.visible:
            ansi = self.settings_overlay.render(width)
            return ANSI(ansi) if ansi else ""
        ansi = self.live_region.to_ansi(width)
        if not ansi:
            return ""
        return ANSI(ansi)

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
        # Sync the footer's cursor-position indicator from the composer's
        # current Document. Cheap (document access is O(1)) and keeps the
        # footer responsive to every keystroke without a separate hook.
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
            logger.debug("cursor pos update failed", error=str(e))
        ansi = self.live_region.footer_to_ansi(width)
        return ANSI(ansi) if ansi else ""

    def _terminal_width(self) -> int:
        if self.app is None:
            return DEFAULT_WIDTH
        try:
            return self.app.output.get_size().columns
        except Exception as e:
            logger.debug("Could not determine terminal width", error=str(e))
            return DEFAULT_WIDTH

    # ── Submission ──

    def _handle_submit(self, text: str) -> None:
        """Called by the composer when the user hits Enter on a non-empty line."""
        if not text.strip():
            return

        # Cancel any still-running pending task before spawning a new one,
        # so the processing-flag toggles and invalidate calls can't race
        # across two concurrent ``_send`` wrappers. The agent itself
        # queues user inputs sequentially via its input module, so this
        # cancellation is purely about the UI wrapper — the agent turn
        # already in progress will finish normally.
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()

        # Print user message into scrollback (via run_in_terminal so the
        # app area is correctly redrawn below it).
        self._commit_user_message(text)

        # Slash command path
        if text.startswith("/"):
            self._pending_task = spawn(self._handle_slash(text))
            return

        # Send to agent (in a background task so the UI stays responsive)
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
                # Turn is over — close any tool-block sequence whose
                # closing rule was deferred waiting for a next commit.
                # Without this, a turn that ends on a tool call leaves
                # the bottom ``═══`` rule un-emitted until something
                # else commits (next user message, interrupt, etc.).
                # User-visible symptom: "hanging" open tool box while
                # the agent sits idle post-turn.
                self.committer.flush_block_close()
                self._invalidate()

        self._pending_task = spawn(_send())

    # ── Slash command dispatch ──

    def _wire_command_registry(self) -> None:
        registry: dict = {}
        for name in list_builtin_user_commands():
            cmd = get_builtin_user_command(name)
            if cmd:
                registry[name] = cmd
        self.composer.set_command_registry(registry)
        self.composer.set_command_context(agent=self.agent)
        self.hint_bar.set_registry(registry)
        self._command_registry = registry

    async def _handle_slash(self, text: str) -> None:
        name, args = parse_slash_command(text)

        # Special path: `/model` with no args opens the interactive
        # picker. A full selector string is still handled the standard
        # way via the /model command's own execute().
        if name == "model" and not args.strip():
            self.model_picker.open()
            self._invalidate()
            return

        # Special path: `/settings` / `/config` open the settings overlay.
        # Unlike /model there's no text-form equivalent — it's always
        # the interactive surface. The SettingsCommand class still exists
        # so the command shows up in /help and the slash-hint bar.
        if name in ("settings", "config") and not args.strip():
            self.settings_overlay.open()
            self._invalidate()
            return

        cmd = self._command_registry.get(name) or get_builtin_user_command(name)
        if cmd is None:
            self._commit_text(f"[red]Unknown command:[/red] /{name}")
            return

        ctx = UserCommandContext(
            agent=self.agent,
            session=getattr(self.agent, "session", None),
            input_module=getattr(self.agent, "input", None),
            extra={"command_registry": self._command_registry},
        )
        try:
            result = await cmd.execute(args, ctx)
        except Exception as e:
            self._commit_text(f"[red]Command error:[/red] {e}")
            return

        if result.error:
            self._commit_text(f"[red]{result.error}[/red]")
        if result.output:
            self._commit_text(result.output)

        if name in ("exit", "quit"):
            self._exit_requested = True
            if self.app:
                self.app.exit()

    # Output event handlers (on_text_chunk, on_tool_start, etc.) live in
    # ``AppOutputMixin`` (app_output.py). Kept separate so this file stays
    # focused on lifecycle + layout.

    # ── Commit helpers ──

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
        """Replay session events to scrollback. Called during resume,
        after ``agent.start()`` but before ``app.run_async()``.

        Also hydrates the footer's cumulative token counters AND the
        context-window limits from the event stream BEFORE the replay
        renders anything, so ``↑ / ↓`` and ``ctx %`` read correctly
        immediately after resume — not after the first new LLM call.
        Mirrors the TUI's ``on_resume`` approach (summing token_usage
        events) which is more reliable than reading session state
        directly.
        """
        self._restore_footer_from_events(events)
        SessionReplay(self).replay(events)

    def _restore_footer_from_events(self, events: list[dict]) -> None:
        """Seed the footer's cumulative token + context values from events.

        Sums every ``token_usage`` event in the replay stream into
        ``input_total`` / ``output_total`` / ``cached_total``, records
        the most recent prompt size as ``last_prompt``, and pulls the
        latest ``max_context`` / ``compact_threshold`` from any
        ``session_info`` event. All of those fields otherwise stay at 0
        until the first fresh LLM call after resume.
        """
        if not events:
            return
        total_in = 0
        total_out = 0
        total_cached = 0
        last_prompt = 0
        max_ctx = 0
        for evt in events:
            # Events may be wrapped {"type": ..., "data": {...}} or flat.
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

    # ── Misc helpers ──

    def _invalidate(self) -> None:
        if self.app is not None:
            self.app.invalidate()

    def _on_interrupt(self) -> None:
        if self._processing and self.agent:
            try:
                self.agent.interrupt()
            except Exception as e:
                logger.exception("Interrupt failed", error=str(e))

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
        """Apply a selector string chosen from the model picker.

        Dispatches through the same ``/model <selector>`` path that
        text-based invocation uses, so behaviour (validation, error
        surfacing, notice-to-scrollback) is identical.
        """
        if not selector:
            return
        self._pending_task = spawn(self._handle_slash(f"/model {selector}"))

    def _picker_handle_key(self, key: str) -> bool:
        """Forward a named-key event to whichever overlay is open.

        Composer bindings call this on every named key (``up``, ``enter``,
        ``escape``, ``tab``, ``backspace``, …). The first overlay that
        claims to own the keyboard (``visible``) gets the key; if it
        consumes it, the composer skips its own default handling.
        """
        if self.model_picker.visible:
            consumed = self.model_picker.handle_key(key)
            if consumed:
                self._invalidate()
            return consumed
        if self.settings_overlay.visible:
            consumed = self.settings_overlay.handle_key(key)
            if consumed:
                self._invalidate()
            return consumed
        return False

    def _picker_handle_text(self, char: str) -> bool:
        """Forward a printable-character event to whichever overlay wants text.

        Invoked from the composer's ``Keys.Any`` binding which is
        conditionally active only when ``_picker_captures_input`` is
        True — so this runs only for forms inside the settings overlay.
        """
        if self.settings_overlay.visible and self.settings_overlay.is_capturing_text():
            consumed = self.settings_overlay.handle_text(char)
            if consumed:
                self._invalidate()
            return consumed
        return False

    def _picker_captures_input(self) -> bool:
        """True when an overlay is capturing printable characters.

        Drives the ``Condition`` filter on the composer's ``Keys.Any``
        binding — we only intercept text when an overlay genuinely wants
        it (form mode), so list-mode keystrokes still go through the
        normal ``handle_key`` path.
        """
        if self.settings_overlay.visible and self.settings_overlay.is_capturing_text():
            return True
        return False

    def _on_clear_screen(self) -> None:
        # Send the standard "clear scrollback + screen" escape — handled
        # via the committer so it goes through run_in_terminal correctly.
        self.committer.ansi("\x1b[3J\x1b[H\x1b[2J")

    def _print_banner(self) -> None:
        name = getattr(self.agent.config, "name", "agent")
        model = getattr(self.agent.llm, "model", "") or ""
        banner = Text()
        banner.append("KohakuTerrarium", style=COLOR_BANNER)
        banner.append(" · ", style="dim")
        banner.append(name, style="bold")
        if model:
            banner.append(f" ({model})", style="dim")
        self._scroll_console.print(banner)
        # One compact hint line. Full keymap lives behind /help.
        self._scroll_console.print(
            Text("Type /help for shortcuts · Ctrl+D to quit", style="dim")
        )
        self._scroll_console.print()
