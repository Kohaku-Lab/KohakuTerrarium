"""Regression coverage for enhanced-keyboard escape decoding."""

from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.keys import Keys

import kohakuterrarium.builtins.cli_rich.composer as composer


def _decode(sequence: str) -> list:
    keys = []
    parser = Vt100Parser(keys.append)
    parser.feed(sequence)
    parser.flush()
    return keys


def test_existing_enhanced_keyboard_families_remain_registered() -> None:
    assert ANSI_SEQUENCES["\x1b[27u"] == Keys.Escape
    assert ANSI_SEQUENCES["\x1b[13u"] == Keys.ControlM
    assert ANSI_SEQUENCES["\x1b[9u"] == Keys.ControlI
    assert ANSI_SEQUENCES["\x1b[127u"] == Keys.ControlH
    assert ANSI_SEQUENCES["\x1b[13;2u"] == composer.SHIFT_ENTER_KEY
    assert ANSI_SEQUENCES["\x1b[13;5u"] == composer.CTRL_ENTER_KEY
    assert ANSI_SEQUENCES["\x1b[13;6u"] == composer.CTRL_SHIFT_ENTER_KEY


def test_kitty_functional_key_press_repeat_and_release_are_consumed() -> None:
    plain = {
        "A": Keys.Up,
        "B": Keys.Down,
        "C": Keys.Right,
        "D": Keys.Left,
        "E": Keys.Ignore,
        "F": Keys.End,
        "H": Keys.Home,
        "P": Keys.F1,
        "Q": Keys.F2,
        "S": Keys.F4,
    }
    for final, expected in plain.items():
        for event in ("", ":1", ":2"):
            decoded = _decode(f"\x1b[1;1{event}{final}")
            assert [key.key for key in decoded] == [expected]
        released = _decode(f"\x1b[1;1:3{final}")
        assert [key.key for key in released] == [Keys.Ignore]


def test_kitty_modified_functional_keys_mirror_prompt_toolkit_mappings() -> None:
    finals = "ABCDEFHPQS"
    for final in finals:
        for modifiers in range(2, 9):
            press = f"\x1b[1;{modifiers}{final}"
            expected = [key.key for key in _decode(press)]
            for event in (":1", ":2"):
                decoded = _decode(f"\x1b[1;{modifiers}{event}{final}")
                assert [key.key for key in decoded] == expected
            released = _decode(f"\x1b[1;{modifiers}:3{final}")
            assert [key.key for key in released] == [Keys.Ignore]


def test_enhanced_keyboard_registration_is_idempotent() -> None:
    before = dict(ANSI_SEQUENCES)
    composer._register_enhanced_keyboard_keys()
    composer._register_enhanced_keyboard_keys()
    assert ANSI_SEQUENCES == before
