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
from prompt_toolkit.widgets import Frame
from rich.console import Console
from rich.text import Text

from kohakuterrarium.builtins.cli_rich.commit import ScrollbackCommitter, SessionReplay
from kohakuterrarium.builtins.cli_rich.composer import Composer
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
from kohakuterrarium.modules.user_command.base import (
    UserCommandContext,
    parse_slash_command,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_WIDTH = 100


class RichCLIApp:
    """Single-Application orchestrator for ``--mode cli``."""

    def __init__(self, agent: Any):
        self.agent = agent
        self.live_region = LiveRegion()
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
            filter=Condition(lambda: self.live_region.has_content),
        )

        # Bordered input box.
        input_frame = Frame(
            self.composer.text_area,
            title="message",
            style="class:input.frame",
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

        root_container = HSplit(
            [
                status_container,
                input_frame,
                footer_window,
            ]
        )

        layout = Layout(
            container=root_container, focused_element=self.composer.text_area
        )

        style = Style.from_dict(
            {
                "input.frame": "ansicyan",
                "input.frame.label": "ansicyan bold",
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
        ansi = self.live_region.to_ansi(width)
        if not ansi:
            return ""
        return ANSI(ansi)

    def _footer_text(self):
        width = self._terminal_width()
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
        self._command_registry = registry

    async def _handle_slash(self, text: str) -> None:
        name, args = parse_slash_command(text)
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

    # ── Output module callbacks (called by RichCLIOutput) ──

    def on_text_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        self.live_region.append_chunk(chunk)
        self._invalidate()

    def on_processing_start(self) -> None:
        # Spacer line before the model's response. Tool calls / text
        # commits inside the turn add no extra blank lines, so the whole
        # turn reads as one block surrounded by exactly one blank line
        # before and one after.
        self._commit_blank_line()
        self.live_region.start_message()
        self._invalidate()

    def on_processing_end(self) -> None:
        committed = self.live_region.finish_message()
        if committed is not None:
            self._commit_renderable(committed)
        # Spacer line after the model's response.
        self._commit_blank_line()
        self._invalidate()

    def on_tool_start(
        self,
        job_id: str,
        name: str,
        args_preview: str = "",
        kind: str = "tool",
        parent_job_id: str = "",
        background: bool = False,
    ) -> None:
        # Ordering rule:
        #
        # - **Direct (blocking) tools** — the controller WAITS for the tool
        #   inside the same turn, then the model continues with post-tool
        #   text. We flush the in-flight assistant message NOW so the
        #   commit order in scrollback is: pre-text → tool → post-text.
        #
        # - **Background tools** — the controller does NOT wait. It
        #   immediately feeds a "task promoted" placeholder back to the
        #   LLM, which generates interim text in the same cycle. If we
        #   flushed here, the pre-tool text and the interim text would
        #   end up as TWO separate ◆ blocks. By keeping the assistant
        #   message intact across a bg dispatch, the whole cycle commits
        #   as one coherent ◆.
        if not background:
            self._flush_assistant_message()
        self.live_region.add_tool(
            job_id, name, args_preview, kind, parent_job_id=parent_job_id
        )
        if background:
            # The block opens straight into the bg state. Commit a
            # one-line "dispatched in background" notice immediately so
            # the user has a clear marker of WHEN the bg job started —
            # the matching result panel will commit later when the task
            # actually completes.
            self.live_region.promote_tool(job_id)
            block = self.live_region.tool_blocks.get(job_id)
            if block is not None and not parent_job_id:
                self.committer.renderable(block.build_dispatch_notice())
        self._invalidate()

    def _flush_assistant_message(self) -> None:
        """Commit the current assistant message (if any non-empty) to scrollback."""
        msg = self.live_region.assistant_msg
        if msg is None or msg.is_empty:
            return
        committed = self.live_region.finish_message()
        if committed is not None:
            self._commit_renderable(committed)

    def on_tool_done(self, job_id: str, output: str = "", **metadata) -> None:
        committed = self.live_region.update_tool_done(job_id, output, **metadata)
        if committed is not None:
            self._commit_renderable(committed)
        self._invalidate()

    def on_tool_error(self, job_id: str, error: str = "") -> None:
        committed = self.live_region.update_tool_error(job_id, error)
        if committed is not None:
            self._commit_renderable(committed)
        self._invalidate()

    def on_tool_promoted(self, job_id: str) -> None:
        self.live_region.promote_tool(job_id)
        self._invalidate()

    def on_job_cancelled(self, job_id: str, job_name: str = "") -> None:
        committed = self.live_region.cancel_tool(job_id)
        if committed is not None:
            self._commit_renderable(committed)
        self._invalidate()

    def on_subagent_tool_start(
        self, parent_id: str, tool_name: str, args_preview: str = ""
    ) -> None:
        self.live_region.add_subagent_tool(parent_id, tool_name, args_preview)
        self._invalidate()

    def on_subagent_tool_done(
        self, parent_id: str, tool_name: str, output: str = ""
    ) -> None:
        self.live_region.update_subagent_tool_done(parent_id, tool_name, output)
        self._invalidate()

    def on_subagent_tool_error(
        self, parent_id: str, tool_name: str, error: str = ""
    ) -> None:
        self.live_region.update_subagent_tool_error(parent_id, tool_name, error)
        self._invalidate()

    def on_subagent_tokens(
        self, parent_id: str, prompt: int, completion: int, total: int
    ) -> None:
        self.live_region.update_subagent_tokens(parent_id, prompt, completion, total)
        self._invalidate()

    def on_token_update(self, prompt: int, completion: int, max_ctx: int = 0) -> None:
        self.live_region.update_footer_tokens(prompt, completion, max_ctx)
        self._invalidate()

    def on_compact_start(self) -> None:
        self.live_region.set_compacting(True)
        self._invalidate()

    def on_compact_end(self) -> None:
        self.live_region.set_compacting(False)
        self._invalidate()

    def on_session_info(self, model: str = "", max_ctx: int = 0) -> None:
        if model:
            self.live_region.update_footer_model(model)
        if max_ctx:
            self.live_region.footer._max_context = max_ctx
        self._invalidate()

    def on_processing_error(self, error_type: str, error: str) -> None:
        """Surface a processing error as a red notice in scrollback."""
        self._flush_assistant_message()
        self.committer.text(f"[red]✗ {error_type}:[/red] {error}")
        self._invalidate()

    def on_interrupt_notice(self, detail: str = "") -> None:
        """Commit an 'interrupted' notice to scrollback."""
        self._flush_assistant_message()
        self.committer.text("[yellow]⚠ interrupted[/yellow]")
        self._invalidate()

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
        after ``agent.start()`` but before ``app.run_async()``."""
        SessionReplay(self).replay(events)

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
        self._scroll_console.print(
            Text(
                "Type a message · /help · /exit · "
                "Esc=interrupt · Ctrl+B=backgroundify · "
                "Ctrl+X=cancel-bg · Ctrl+L=clear · Ctrl+D=quit · "
                "Shift+Enter / Ctrl+Enter / Alt+Enter / Ctrl+J = newline",
                style="dim",
            )
        )
        self._scroll_console.print()
