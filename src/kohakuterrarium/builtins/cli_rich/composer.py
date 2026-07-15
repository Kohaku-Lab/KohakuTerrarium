"""Composer — input TextArea + key bindings for the rich CLI Application.

This is NOT a standalone PromptSession (those fight with concurrent
renderers). It produces a ``prompt_toolkit.widgets.TextArea`` that the
``RichCLIApp`` embeds inside a single Application Layout — one render
loop, one bordered input box, no flicker.
"""

import time
from pathlib import Path
from typing import Callable

from prompt_toolkit.filters import Condition
from prompt_toolkit.history import FileHistory
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.search import SearchDirection, start_search
from prompt_toolkit.widgets import TextArea

from kohakuterrarium.builtins.cli_rich.completer import SlashCommandCompleter
from kohakuterrarium.builtins.cli_rich.paste_store import (
    PasteStore,
    should_placeholderize,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

HISTORY_DIR = Path.home() / ".kohakuterrarium" / "history"


# Closed Keys enums require rarely used function-key proxies for modified Enter.
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

    - **Esc / Enter / Tab / Backspace** under Kitty CSI u — some
      terminals disambiguate these even without modifiers:

      - Esc       → ``ESC [ 27 u``   → ``Keys.Escape``
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

    # Modified Enter uses function-key proxies under both terminal protocols.
    ANSI_SEQUENCES["\x1b[27;2;13~"] = SHIFT_ENTER_KEY
    ANSI_SEQUENCES["\x1b[27;5;13~"] = CTRL_ENTER_KEY
    ANSI_SEQUENCES["\x1b[27;6;13~"] = CTRL_SHIFT_ENTER_KEY
    ANSI_SEQUENCES["\x1b[13;2u"] = SHIFT_ENTER_KEY
    ANSI_SEQUENCES["\x1b[13;5u"] = CTRL_ENTER_KEY
    ANSI_SEQUENCES["\x1b[13;6u"] = CTRL_SHIFT_ENTER_KEY

    # Enhanced protocols otherwise expose Ctrl+letter sequences as literal text.
    for letter in "abcdefghijklmnopqrstuvwxyz":
        codepoint = ord(letter)
        key = getattr(Keys, f"Control{letter.upper()}")
        ANSI_SEQUENCES[f"\x1b[{codepoint};5u"] = key
        ANSI_SEQUENCES[f"\x1b[27;5;{codepoint}~"] = key

    # Kitty may disambiguate unmodified control keys under protocol flag 1.
    ANSI_SEQUENCES["\x1b[27u"] = Keys.Escape  # Esc
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
        on_ctrl_c: Callable[[], None] | None = None,
        on_exit: Callable[[], None] | None = None,
        on_clear_screen: Callable[[], None] | None = None,
        on_backgroundify: Callable[[], None] | None = None,
        on_cancel_bg: Callable[[], None] | None = None,
        on_toggle_expand: Callable[[], None] | None = None,
        picker_key_handler: Callable[[str], bool] | None = None,
        picker_text_handler: Callable[[str], bool] | None = None,
        picker_captures_input: Callable[[], bool] | None = None,
        on_focus_next: Callable[[], None] | None = None,
        on_focus_prev: Callable[[], None] | None = None,
        on_open_overlay: Callable[[], None] | None = None,
    ):
        self.creature_name = creature_name
        self._on_submit = on_submit
        self._on_interrupt = on_interrupt
        self._on_ctrl_c = on_ctrl_c
        self._on_exit = on_exit
        self._on_clear_screen = on_clear_screen
        self._on_backgroundify = on_backgroundify
        self._on_cancel_bg = on_cancel_bg
        self._on_toggle_expand = on_toggle_expand
        self._picker_key = picker_key_handler
        self._picker_text = picker_text_handler
        self._picker_captures_input = picker_captures_input
        self._on_focus_next = on_focus_next
        self._on_focus_prev = on_focus_prev
        self._on_open_overlay = on_open_overlay

        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self._history = FileHistory(str(HISTORY_DIR / f"{creature_name}.txt"))
        self._completer = SlashCommandCompleter()
        self._paste_store = PasteStore()

        # Win32 paste detection prevents embedded newlines from submitting mid-paste.
        self._last_text_change_ts: float = 0.0
        self._paste_burst_count: int = 0
        self._paste_burst_window: float = 0.005
        self._paste_burst_min_count: int = 3

        # Dynamic height prevents the composer from consuming the remaining layout.
        self.text_area = TextArea(
            multiline=True,
            wrap_lines=True,
            history=self._history,
            completer=self._completer,
            complete_while_typing=True,
            prompt="› ",
            scrollbar=False,
            focus_on_click=True,
            dont_extend_height=True,
        )

        self.text_area.buffer.on_text_changed += self._record_text_change

        self.key_bindings = self._build_key_bindings()

    def _record_text_change(self, _buf) -> None:
        """Update the Win32 paste-burst detector after a buffer mutation."""
        now = time.monotonic()
        if now - self._last_text_change_ts < self._paste_burst_window:
            self._paste_burst_count += 1
        else:
            self._paste_burst_count = 1
        self._last_text_change_ts = now

    @property
    def paste_store(self) -> PasteStore:
        return self._paste_store

    def set_command_registry(self, registry: dict) -> None:
        """Wire the slash command completer to the user command registry."""
        self._completer.set_registry(registry)

    def set_command_context(self, *, agent=None) -> None:
        """Provide live runtime context for argument completion."""
        self._completer.set_agent(agent)

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        def _picker(key: str) -> bool:
            if self._picker_key is None:
                return False
            return self._picker_key(key)

        # Visible overlays take key priority; completion menus take arrow priority.
        @kb.add("up")
        def _up(event):
            if _picker("up"):
                return
            buf = event.current_buffer
            if buf.complete_state:
                buf.complete_previous()
                return
            buf.auto_up()

        @kb.add("down")
        def _down(event):
            if _picker("down"):
                return
            buf = event.current_buffer
            if buf.complete_state:
                buf.complete_next()
                return
            buf.auto_down()

        @kb.add("left")
        def _left(event):
            if _picker("left"):
                return
            event.current_buffer.cursor_left()

        @kb.add("right")
        def _right(event):
            if _picker("right"):
                return
            event.current_buffer.cursor_right()

        @kb.add("pageup")
        def _pgup(event):
            if _picker("pageup"):
                return
            event.current_buffer.cursor_position = 0

        @kb.add("pagedown")
        def _pgdn(event):
            if _picker("pagedown"):
                return
            event.current_buffer.cursor_position = len(event.current_buffer.text)

        @kb.add("tab")
        def _tab(event):
            if _picker("tab"):
                return
            buf = event.current_buffer
            if buf.complete_state:
                buf.complete_next()
                return
            # Empty input makes Tab unambiguous for creature focus cycling.
            if not buf.text and self._on_focus_next is not None:
                self._on_focus_next()
                return
            buf.insert_text("\t")

        @kb.add("s-tab")
        def _stab(event):
            if _picker("s-tab"):
                return
            buf = event.current_buffer
            if buf.complete_state:
                buf.complete_previous()
                return
            if not buf.text and self._on_focus_prev is not None:
                self._on_focus_prev()

        @kb.add("c-a")
        def _open_overlay(event):
            if self._on_open_overlay is not None:
                self._on_open_overlay()

        @kb.add("enter")
        def _enter(event):
            if _picker("enter"):
                return
            buf = event.current_buffer
            if buf.complete_state and buf.complete_state.current_completion:
                buf.apply_completion(buf.complete_state.current_completion)
                return
            # Rapid, sustained changes identify Win32 paste newlines, not human Enter.
            now = time.monotonic()
            if (
                now - self._last_text_change_ts < self._paste_burst_window
                and self._paste_burst_count >= self._paste_burst_min_count
            ):
                buf.insert_text("\n")
                return
            text = buf.text
            if not text.strip():
                return
            if text.rstrip().endswith("\\"):
                buf.delete_before_cursor()
                buf.insert_text("\n")
                return
            # Expand placeholders only for submission; history retains compact tokens.
            submitted = self._paste_store.resolve(text)
            buf.reset(append_to_history=True)
            if self._on_submit:
                self._on_submit(submitted)

        @kb.add(Keys.BracketedPaste)
        def _bracketed_paste(event):
            # Bracketed paste arrives as one event, preserving embedded newlines.
            data = event.data or ""
            if not data:
                return
            # Normalize clipboard line endings before display and submission.
            data = data.replace("\r\n", "\n").replace("\r", "\n")
            buf = event.current_buffer
            if should_placeholderize(data):
                token = self._paste_store.stash(data)
                buf.insert_text(token)
            else:
                buf.insert_text(data)

        @kb.add("escape", "enter")  # Alt+Enter
        def _alt_enter(event):
            event.current_buffer.insert_text("\n")

        @kb.add(SHIFT_ENTER_KEY)
        def _shift_enter(event):
            event.current_buffer.insert_text("\n")

        @kb.add(CTRL_ENTER_KEY)
        def _ctrl_enter(event):
            event.current_buffer.insert_text("\n")

        @kb.add(CTRL_SHIFT_ENTER_KEY)
        def _ctrl_shift_enter(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-j")
        def _ctrl_j(event):
            # Ctrl+J remains the protocol-independent newline fallback.
            event.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def _ctrl_c(event):
            buf = event.current_buffer
            if buf.text:
                buf.reset()
            elif self._on_ctrl_c:
                self._on_ctrl_c()

        @kb.add(Keys.SIGINT, eager=True)
        def _sigint(_event):
            if self._on_ctrl_c:
                self._on_ctrl_c()

        # Esc must not be eager because Alt and CSI sequences share its prefix.
        @kb.add("escape")
        def _esc(event):
            if _picker("escape"):
                return
            # Completion owns the first Esc; a second Esc may interrupt the agent.
            buf = event.current_buffer
            if buf.complete_state:
                buf.cancel_completion()
                return
            if self._on_interrupt:
                self._on_interrupt()

        @kb.add("c-b")
        def _ctrl_b(event):
            if self._on_backgroundify:
                self._on_backgroundify()

        @kb.add("c-x")
        def _ctrl_x(event):
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

        @kb.add("c-o")
        def _ctrl_o(event):
            if self._on_toggle_expand:
                self._on_toggle_expand()

        # Alt+P/N force history movement even within multiline input.
        @kb.add("escape", "p")
        def _alt_p(event):
            event.current_buffer.history_backward()

        @kb.add("escape", "n")
        def _alt_n(event):
            event.current_buffer.history_forward()

        # Modal forms receive editing keys before the composer buffer.
        @kb.add("backspace")
        def _bksp(event):
            if _picker("backspace"):
                return
            event.current_buffer.delete_before_cursor()

        @kb.add("delete")
        def _del(event):
            if _picker("delete"):
                return
            event.current_buffer.delete()

        @kb.add("home")
        def _home(event):
            if _picker("home"):
                return
            event.current_buffer.cursor_position = 0

        @kb.add("end")
        def _end(event):
            if _picker("end"):
                return
            event.current_buffer.cursor_position = len(event.current_buffer.text)

        # Capture printable input only while a modal overlay claims it.
        @kb.add(
            Keys.Any,
            filter=Condition(
                lambda: bool(
                    self._picker_captures_input and self._picker_captures_input()
                )
            ),
        )
        def _overlay_char(event):
            data = event.data or ""
            if not data or not data.isprintable():
                return
            if self._picker_text:
                self._picker_text(data)

        @kb.add("c-r")
        def _ctrl_r(event):
            # Search works through prompt_toolkit's buffer state without a toolbar.
            start_search(direction=SearchDirection.BACKWARD)

        return kb
