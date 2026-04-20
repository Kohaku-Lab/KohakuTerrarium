"""Regression tests for the enhanced-keyboard ANSI_SEQUENCES patches.

The rich CLI enables the Kitty keyboard protocol + xterm modifyOtherKeys
at startup (see ``cli_rich/runtime.py``). Once that's active, terminals
emit escape sequences that prompt_toolkit's built-in parser does not
know how to decode, so ``cli_rich/composer.py`` patches the global
``ANSI_SEQUENCES`` table at import time.

Missing entries here silently break interactive keybindings — the
affected key gets its raw escape bytes dumped into the composer
instead of firing the bound action. Pin the full set of mappings so
any accidental removal is caught immediately.
"""

import pytest

pytest.importorskip("prompt_toolkit")

from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.keys import Keys

# Side-effect import: registers the patches. Must come after the
# importorskip so environments without prompt_toolkit skip cleanly.
import kohakuterrarium.builtins.cli_rich.composer as composer  # noqa: F401,E402


def test_kitty_csi_u_esc_decodes_to_escape():
    """Regression: Esc under Kitty CSI u must decode to Keys.Escape.

    Before the fix, pressing Esc on macOS terminals that honor the
    Kitty keyboard protocol (Ghostty / Kitty / WezTerm / recent iTerm2)
    leaked the bytes ``[27u`` into the composer buffer instead of
    interrupting the agent.
    """
    assert ANSI_SEQUENCES["\x1b[27u"] == Keys.Escape


def test_kitty_csi_u_enter_tab_backspace_decoded():
    """Enter / Tab / Backspace disambiguated forms are covered."""
    assert ANSI_SEQUENCES["\x1b[13u"] == Keys.ControlM
    assert ANSI_SEQUENCES["\x1b[9u"] == Keys.ControlI
    assert ANSI_SEQUENCES["\x1b[127u"] == Keys.ControlH


def test_modifier_enter_proxies_registered():
    """Shift/Ctrl/Ctrl+Shift + Enter hit the F19/F20/F21 proxy slots.

    Both the xterm modifyOtherKeys=2 (`ESC [ 27 ; mod ; 13 ~`) and
    Kitty CSI u (`ESC [ 13 ; mod u`) forms must land on the same
    proxy keys or the "insert newline" key bindings stop firing.
    """
    assert ANSI_SEQUENCES["\x1b[27;2;13~"] == composer.SHIFT_ENTER_KEY
    assert ANSI_SEQUENCES["\x1b[27;5;13~"] == composer.CTRL_ENTER_KEY
    assert ANSI_SEQUENCES["\x1b[27;6;13~"] == composer.CTRL_SHIFT_ENTER_KEY
    assert ANSI_SEQUENCES["\x1b[13;2u"] == composer.SHIFT_ENTER_KEY
    assert ANSI_SEQUENCES["\x1b[13;5u"] == composer.CTRL_ENTER_KEY
    assert ANSI_SEQUENCES["\x1b[13;6u"] == composer.CTRL_SHIFT_ENTER_KEY


@pytest.mark.parametrize("letter", list("abcdefghijklmnopqrstuvwxyz"))
def test_ctrl_letter_decoded_under_both_encodings(letter):
    """Ctrl+a..z must decode identically under Kitty CSI u and
    modifyOtherKeys=2 — otherwise Ctrl+C, Ctrl+D, Ctrl+L etc. silently
    stop firing on terminals that enable the disambiguation flag.
    """
    codepoint = ord(letter)
    expected = getattr(Keys, f"Control{letter.upper()}")
    assert ANSI_SEQUENCES[f"\x1b[{codepoint};5u"] == expected
    assert ANSI_SEQUENCES[f"\x1b[27;5;{codepoint}~"] == expected


def test_registration_is_idempotent():
    """Re-running the registration must not raise or mutate entries.

    The module-level guard (`_ENHANCED_KEYS_REGISTERED`) exists so
    test harnesses that reload the module don't repeatedly rewrite
    the shared ANSI_SEQUENCES dict.
    """
    before = ANSI_SEQUENCES["\x1b[27u"]
    composer._register_enhanced_keyboard_keys()
    composer._register_enhanced_keyboard_keys()
    assert ANSI_SEQUENCES["\x1b[27u"] == before
