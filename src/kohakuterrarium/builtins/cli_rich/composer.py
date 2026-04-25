"""Composer — input TextArea + key bindings for the rich CLI Application.

This is NOT a standalone PromptSession (those fight with concurrent
renderers). It produces a ``prompt_toolkit.widgets.TextArea`` that the
``RichCLIApp`` embeds inside a single Application Layout — one render
loop, one bordered input box, no flicker.
"""

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

    # Esc / Enter / Tab / Backspace — Kitty CSI u disambiguated forms.
    # Terminals emit these when flag 1 of the protocol is active and
    # they choose to disambiguate all keys (not every terminal does,
    # but it costs us nothing to register defensively).
    # Without the Esc mapping, pressing Esc on macOS (Ghostty / Kitty /
    # WezTerm / recent iTerm2) leaks the bytes `[27u` into the composer
    # instead of firing the interrupt hotkey.
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
        # Returns True if the key was consumed by a visible picker; the
        # composer then skips its default handler so arrow keys etc. flow
        # into the picker instead of moving the text cursor.
        self._picker_key = picker_key_handler
        # Returns True if a printable character was consumed by an overlay
        # form. Drives the ``Keys.Any`` binding below via a Condition so
        # we only steal text when the overlay is genuinely capturing input.
        self._picker_text = picker_text_handler
        self._picker_captures_input = picker_captures_input

        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self._history = FileHistory(str(HISTORY_DIR / f"{creature_name}.txt"))
        self._completer = SlashCommandCompleter()
        self._paste_store = PasteStore()

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
            prompt="› ",
            scrollbar=False,
            focus_on_click=True,
            dont_extend_height=True,
        )

        self.key_bindings = self._build_key_bindings()

    # Public accessor — the app resolves paste placeholders on submit.
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

        # Picker-priority keys — when a dialog (currently model picker)
        # is visible, arrows / tab / enter / esc are routed to it first.
        # If consumed, the composer's normal handler for that key is
        # suppressed. This is the cleanest way we've found to "modal"
        # a prompt_toolkit Application without juggling focus between
        # containers.
        # When the slash-command completion menu is open, up/down must
        # cycle the menu — our original bindings went straight to
        # ``auto_up`` / ``auto_down`` and silently skipped menu
        # navigation. Symptom was "arrow keys randomly not working":
        # they worked fine on an empty buffer but felt dead the moment
        # you typed ``/`` and the completer popped open.
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
            # Default: move to start of buffer (closest analogue).
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
            # Tab accepts the current completion (menu-cycling semantics
            # shared with up/down below). Without this, pressing Tab on
            # ``/mod`` would insert a literal tab character instead of
            # finishing the ``/model`` suggestion — a 90%-of-the-time
            # papercut.
            if buf.complete_state:
                buf.complete_next()
                return
            buf.insert_text("\t")

        @kb.add("s-tab")
        def _stab(event):
            if _picker("s-tab"):
                return
            buf = event.current_buffer
            if buf.complete_state:
                buf.complete_previous()

        @kb.add("enter")
        def _enter(event):
            if _picker("enter"):
                return
            buf = event.current_buffer
            # If the completion menu is open and the user has a row
            # highlighted, accept the completion instead of submitting.
            # The submit-through-menu behaviour (empty menu, or nothing
            # highlighted) still falls through to the normal path.
            if buf.complete_state and buf.complete_state.current_completion:
                buf.apply_completion(buf.complete_state.current_completion)
                return
            text = buf.text
            if not text.strip():
                return
            if text.rstrip().endswith("\\"):
                # Line continuation: drop trailing backslash, insert newline
                buf.delete_before_cursor()
                buf.insert_text("\n")
                return
            # Expand paste placeholders back to their full content before
            # shipping the text to the agent. The visible buffer stayed
            # compact thanks to the placeholder token; the model sees the
            # real paste body.
            submitted = self._paste_store.resolve(text)
            # append_to_history=True persists the submission to FileHistory
            # so history recall can find it next session. We persist the
            # visible text (with placeholders), not the expanded form —
            # otherwise replaying history would dump huge pastes back into
            # the buffer.
            buf.reset(append_to_history=True)
            if self._on_submit:
                self._on_submit(submitted)

        @kb.add(Keys.BracketedPaste)
        def _bracketed_paste(event):
            # Real terminals (Windows Terminal, iTerm2, Kitty, Alacritty,
            # WezTerm, Foot, modern tmux/screen) emit CSI 200~ … CSI 201~
            # around pasted content. prompt_toolkit's Vt100Parser decodes
            # that into a BracketedPaste key whose .data is the full
            # payload — so multiline pastes arrive as ONE event instead of
            # a burst of individual Enter keys. Without this binding, each
            # newline in the paste would trigger our submit handler.
            data = event.data or ""
            if not data:
                return
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
            elif self._on_ctrl_c:
                self._on_ctrl_c()

        @kb.add(Keys.SIGINT, eager=True)
        def _sigint(_event):
            if self._on_ctrl_c:
                self._on_ctrl_c()

        @kb.add("escape", eager=True)
        def _esc(event):
            if _picker("escape"):
                return
            # Close an open completion menu before treating Esc as
            # "interrupt". Without this, once the slash completer
            # popped open it would stay open — every subsequent arrow
            # key would cycle the hidden menu instead of moving the
            # cursor, which is the "arrow keys randomly not working"
            # bug.
            buf = event.current_buffer
            if buf.complete_state:
                buf.cancel_completion()
                return
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

        @kb.add("c-o")
        def _ctrl_o(event):
            # Toggle expand/collapse on the most recent top-level tool
            # block. Useful when a long tool output was truncated to
            # preview size in the live region — press Ctrl+O to see all
            # of it, press again to re-collapse. Mnemonic: "output".
            if self._on_toggle_expand:
                self._on_toggle_expand()

        # Power-user history bindings — Alt+P prev / Alt+N next. The bare
        # Up/Down arrows are already smart via prompt_toolkit's auto_up /
        # auto_down (they move within a multiline buffer first and only
        # fall through to history when the cursor is at the very top/
        # bottom line). Alt+P/N force the history step unconditionally,
        # which is the shell convention — useful when editing a long
        # multiline message and you want to cycle to a prior command
        # without first scrolling the cursor to the top.
        @kb.add("escape", "p")
        def _alt_p(event):
            event.current_buffer.history_backward()

        @kb.add("escape", "n")
        def _alt_n(event):
            event.current_buffer.history_forward()

        # Named keys the settings overlay needs to intercept in form mode.
        # Without these, Backspace would delete from the textarea buffer
        # instead of the form field value, and Home/End would move the
        # composer cursor instead of being swallowed as no-ops.
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

        # Printable-character capture for overlay forms. Only fires when
        # an overlay explicitly asks for it — in list/confirm mode the
        # Condition returns False and Keys.Any falls through to the
        # textarea's default character-insertion handler. Without the
        # filter we'd swallow every keystroke whenever any overlay was
        # open, which would break the composer entirely.
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
            # Reverse-incremental history search. prompt_toolkit wires
            # this up to the buffer's search state — the default search
            # toolbar is not in our layout, but the buffer's internal
            # search still works: start_search puts the buffer into
            # "searching" mode, subsequent chars narrow the match, Enter
            # accepts, Esc cancels. Without any toolbar the user sees
            # the buffer text update to the matched entry as they type —
            # mirrors bash's C-r UX, minimally.
            start_search(direction=SearchDirection.BACKWARD)

        return kb
