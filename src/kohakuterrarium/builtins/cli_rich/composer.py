"""Composer — input TextArea + key bindings for the rich CLI Application.

This is NOT a standalone PromptSession (those fight with concurrent
renderers). It produces a ``prompt_toolkit.widgets.TextArea`` that the
``RichCLIApp`` embeds inside a single Application Layout — one render
loop, one bordered input box, no flicker.
"""

from pathlib import Path
from typing import Callable

from prompt_toolkit.history import FileHistory
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.widgets import TextArea

from kohakuterrarium.builtins.cli_rich.completer import SlashCommandCompleter
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

HISTORY_DIR = Path.home() / ".kohakuterrarium" / "history"


# Proxy keys: prompt_toolkit's `Keys` enum is closed and doesn't have
# slots for Shift+Enter / Ctrl+Enter — but it DOES have F19/F20/F21,
# which are rarely used on modern keyboards. We hijack those slots and
# redirect the relevant escape sequences (xterm modifyOtherKeys + kitty
# CSI u) to land on them, then bind F19/F20/F21 to "insert newline".
#
# Trade-off: pressing actual F19/F20/F21 will also insert a newline,
# which is fine — virtually no one has those keys on a real keyboard.
SHIFT_ENTER_KEY = Keys.F19
CTRL_ENTER_KEY = Keys.F20
CTRL_SHIFT_ENTER_KEY = Keys.F21


_MODIFIER_ENTER_REGISTERED = False


def _register_modifier_enter_keys() -> None:
    """Teach prompt_toolkit to recognise Shift+Enter / Ctrl+Enter as
    distinct keys.

    By default, prompt_toolkit collapses every "modifier + Enter" escape
    sequence back to plain ``ControlM``. We override the ``ANSI_SEQUENCES``
    map so the relevant escapes decode to ``F19/F20/F21`` instead, which
    we then bind to insert-newline.

    Two encoding families are covered:

    - **xterm ``modifyOtherKeys=2``** — emits ``ESC [ 27 ; <mods> ; 13 ~``.
      This is what Windows Terminal sends for Ctrl/Shift+Enter once
      ``modifyOtherKeys`` is enabled.
    - **Kitty keyboard / CSI u** — emits ``ESC [ 13 ; <mods> u``. Used
      by kitty, foot, alacritty (with the protocol enabled), and recent
      Windows Terminal builds with progressive keyboard enhancement.

    ``mods`` field: 2 = shift, 5 = ctrl, 6 = ctrl+shift.

    Idempotent — guarded by a module-level flag so that re-importing
    this module (e.g. inside tests) doesn't repeatedly mutate the
    global ``ANSI_SEQUENCES`` table.
    """
    global _MODIFIER_ENTER_REGISTERED
    if _MODIFIER_ENTER_REGISTERED:
        return
    _MODIFIER_ENTER_REGISTERED = True

    # xterm modifyOtherKeys=2 — `ESC [ 27 ; mod ; 13 ~`
    ANSI_SEQUENCES["\x1b[27;2;13~"] = SHIFT_ENTER_KEY
    ANSI_SEQUENCES["\x1b[27;5;13~"] = CTRL_ENTER_KEY
    ANSI_SEQUENCES["\x1b[27;6;13~"] = CTRL_SHIFT_ENTER_KEY

    # Kitty / CSI u — `ESC [ 13 ; mod u`
    ANSI_SEQUENCES["\x1b[13;2u"] = SHIFT_ENTER_KEY
    ANSI_SEQUENCES["\x1b[13;5u"] = CTRL_ENTER_KEY
    ANSI_SEQUENCES["\x1b[13;6u"] = CTRL_SHIFT_ENTER_KEY


_register_modifier_enter_keys()


class Composer:
    """Builds the input TextArea + key bindings for RichCLIApp."""

    def __init__(
        self,
        creature_name: str = "creature",
        on_submit: Callable[[str], None] | None = None,
        on_interrupt: Callable[[], None] | None = None,
        on_exit: Callable[[], None] | None = None,
        on_clear_screen: Callable[[], None] | None = None,
        on_backgroundify: Callable[[], None] | None = None,
        on_cancel_bg: Callable[[], None] | None = None,
    ):
        self.creature_name = creature_name
        self._on_submit = on_submit
        self._on_interrupt = on_interrupt
        self._on_exit = on_exit
        self._on_clear_screen = on_clear_screen
        self._on_backgroundify = on_backgroundify
        self._on_cancel_bg = on_cancel_bg

        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self._history = FileHistory(str(HISTORY_DIR / f"{creature_name}.txt"))
        self._completer = SlashCommandCompleter()

        # The bordered input box. Frame is added by RichCLIApp around this.
        #
        # ``dont_extend_height=True`` with ``height=None`` — the Window
        # inside the TextArea shrinks exactly to the content's line count.
        # Empty buffer → 1 line. Type "line1\nline2\nline3" → 3 lines.
        # Without ``dont_extend_height``, the TextArea greedily fills the
        # remaining vertical space in its HSplit parent (eats the screen).
        self.text_area = TextArea(
            multiline=True,
            wrap_lines=True,
            history=self._history,
            completer=self._completer,
            complete_while_typing=True,
            prompt="▶ ",
            scrollbar=False,
            focus_on_click=True,
            dont_extend_height=True,
        )

        self.key_bindings = self._build_key_bindings()

    def set_command_registry(self, registry: dict) -> None:
        """Wire the slash command completer to the user command registry."""
        self._completer.set_registry(registry)

    def set_command_context(self, *, agent=None) -> None:
        """Provide live runtime context for argument completion."""
        self._completer.set_agent(agent)

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _enter(event):
            buf = event.current_buffer
            text = buf.text
            if not text.strip():
                return
            if text.rstrip().endswith("\\"):
                # Line continuation: drop trailing backslash, insert newline
                buf.delete_before_cursor()
                buf.insert_text("\n")
                return
            buf.reset()
            if self._on_submit:
                self._on_submit(text)

        @kb.add("escape", "enter")  # Alt+Enter
        def _alt_enter(event):
            event.current_buffer.insert_text("\n")

        @kb.add(SHIFT_ENTER_KEY)
        def _shift_enter(event):
            # Shift+Enter — works in terminals that emit modifyOtherKeys
            # (Windows Terminal, xterm) or kitty CSI u (kitty, foot,
            # alacritty, modern WT). See _register_modifier_enter_keys.
            event.current_buffer.insert_text("\n")

        @kb.add(CTRL_ENTER_KEY)
        def _ctrl_enter(event):
            # Ctrl+Enter — same protocol notes as Shift+Enter.
            event.current_buffer.insert_text("\n")

        @kb.add(CTRL_SHIFT_ENTER_KEY)
        def _ctrl_shift_enter(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-j")
        def _ctrl_j(event):
            # Ctrl+J literally sends \n in a PTY — universal fallback
            # for terminals without modifyOtherKeys / CSI u protocol.
            event.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def _ctrl_c(event):
            buf = event.current_buffer
            if buf.text:
                buf.reset()
            elif self._on_interrupt:
                self._on_interrupt()

        @kb.add("escape", eager=True)
        def _esc(event):
            # Esc is the dedicated "interrupt the agent" hotkey, like
            # Claude Code. Ctrl+C is reserved for clearing the buffer.
            if self._on_interrupt:
                self._on_interrupt()

        @kb.add("c-b")
        def _ctrl_b(event):
            # Backgroundify the most recent direct (blocking) tool /
            # sub-agent. The agent will keep running it but the LLM
            # turn returns immediately with a placeholder result.
            if self._on_backgroundify:
                self._on_backgroundify()

        @kb.add("c-x")
        def _ctrl_x(event):
            # Cancel the most recent backgrounded job. The corresponding
            # block in the live region is finalized as "✗ cancelled".
            if self._on_cancel_bg:
                self._on_cancel_bg()

        @kb.add("c-d")
        def _ctrl_d(event):
            if self._on_exit:
                self._on_exit()
            event.app.exit()

        @kb.add("c-l")
        def _ctrl_l(event):
            if self._on_clear_screen:
                self._on_clear_screen()

        return kb
