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


_ENHANCED_KEYS_REGISTERED = False


def _register_enhanced_keyboard_keys() -> None:
    """Teach prompt_toolkit to decode the escape sequences that terminals
    emit once enhanced keyboard reporting is enabled.

    ``runtime.enable_enhanced_keyboard`` asks the terminal to switch to
    xterm ``modifyOtherKeys=2`` **and** Kitty keyboard protocol flag 1
    ("Disambiguate escape codes"). Under either protocol, keys that used
    to arrive as single-byte control codes (``\\x04`` for Ctrl+D, ``\\r``
    for Enter, …) now arrive as escape sequences prompt_toolkit's
    built-in table has no mapping for — so every ``@kb.add("c-d")``,
    ``@kb.add("enter")`` etc. silently stops firing and the raw bytes
    leak into the buffer.

    We patch ``ANSI_SEQUENCES`` so those forms decode to the same
    ``Keys.Control*`` values as the classic single-byte encoding,
    keeping every existing key binding intact.

    Coverage:

    - **Ctrl+a..z** (modifier = ``5``) under both encodings:

      - Kitty CSI u:        ``ESC [ <codepoint> ; 5 u``
      - modifyOtherKeys=2:  ``ESC [ 27 ; 5 ; <codepoint> ~``

      where ``<codepoint>`` is the lowercase ASCII value (97..122).

    - **Enter / Tab / Backspace** under Kitty CSI u — some terminals
      disambiguate these even without modifiers:

      - Enter     → ``ESC [ 13 u``   → ``Keys.ControlM``
      - Tab       → ``ESC [ 9 u``    → ``Keys.ControlI``
      - Backspace → ``ESC [ 127 u``  → ``Keys.ControlH``

    - **Modifier + Enter** (proxied through F19/F20/F21 so our key
      bindings can treat them as "insert newline"):

      - Kitty CSI u:       ``ESC [ 13 ; <mods> u``
      - modifyOtherKeys=2: ``ESC [ 27 ; <mods> ; 13 ~``

      ``mods`` values: 2 = shift, 5 = ctrl, 6 = ctrl+shift.

    Out of scope (deliberately not registered): Alt+letter, Ctrl+Shift+
    letter, and Cmd/Super+letter. prompt_toolkit has no enum slots for
    most of them, they conflict with classic encodings, and users rarely
    bind those combos in this app. Adding them later is a drop-in.

    Idempotent — guarded by a module-level flag so re-importing this
    module (e.g. inside tests) doesn't repeatedly mutate the global
    ``ANSI_SEQUENCES`` table.
    """
    global _ENHANCED_KEYS_REGISTERED
    if _ENHANCED_KEYS_REGISTERED:
        return
    _ENHANCED_KEYS_REGISTERED = True

    # Modifier + Enter (proxied through F19/F20/F21).
    # xterm modifyOtherKeys=2 — `ESC [ 27 ; mod ; 13 ~`
    ANSI_SEQUENCES["\x1b[27;2;13~"] = SHIFT_ENTER_KEY
    ANSI_SEQUENCES["\x1b[27;5;13~"] = CTRL_ENTER_KEY
    ANSI_SEQUENCES["\x1b[27;6;13~"] = CTRL_SHIFT_ENTER_KEY
    # Kitty CSI u — `ESC [ 13 ; mod u`
    ANSI_SEQUENCES["\x1b[13;2u"] = SHIFT_ENTER_KEY
    ANSI_SEQUENCES["\x1b[13;5u"] = CTRL_ENTER_KEY
    ANSI_SEQUENCES["\x1b[13;6u"] = CTRL_SHIFT_ENTER_KEY

    # Ctrl+a..z under Kitty CSI u + modifyOtherKeys=2.
    # Without these, Ctrl+D (exit), Ctrl+C (interrupt), Ctrl+L (clear),
    # Ctrl+B (bg), Ctrl+X (cancel bg), Ctrl+J (newline fallback) all
    # silently stop working on Ghostty / Kitty / Foot / WezTerm / recent
    # iTerm2 — the bytes `[100;5u` would just get typed into the buffer.
    for letter in "abcdefghijklmnopqrstuvwxyz":
        codepoint = ord(letter)  # 97..122
        key = getattr(Keys, f"Control{letter.upper()}")
        ANSI_SEQUENCES[f"\x1b[{codepoint};5u"] = key
        ANSI_SEQUENCES[f"\x1b[27;5;{codepoint}~"] = key

    # Enter / Tab / Backspace — Kitty CSI u disambiguated forms.
    # Terminals emit these when flag 1 of the protocol is active and
    # they choose to disambiguate all keys (not every terminal does,
    # but it costs us nothing to register defensively).
    ANSI_SEQUENCES["\x1b[13u"] = Keys.ControlM  # Enter
    ANSI_SEQUENCES["\x1b[9u"] = Keys.ControlI  # Tab
    ANSI_SEQUENCES["\x1b[127u"] = Keys.ControlH  # Backspace


_register_enhanced_keyboard_keys()


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
            # append_to_history=True persists the submission to FileHistory
            # so Up/Down can recall it next session. Default bindings handle
            # the arrow keys themselves (auto_up/auto_down).
            buf.reset(append_to_history=True)
            if self._on_submit:
                self._on_submit(text)

        @kb.add("escape", "enter")  # Alt+Enter
        def _alt_enter(event):
            event.current_buffer.insert_text("\n")

        @kb.add(SHIFT_ENTER_KEY)
        def _shift_enter(event):
            # Shift+Enter — works in terminals that emit modifyOtherKeys
            # (Windows Terminal, xterm) or kitty CSI u (kitty, foot,
            # alacritty, modern WT). See _register_enhanced_keyboard_keys.
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
