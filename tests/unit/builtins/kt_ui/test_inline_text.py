"""Unit tests for :mod:`kohakuterrarium.builtins.kt_ui.inline_text`."""

from kohakuterrarium.builtins.kt_ui.inline_text import PLACEHOLDER, inline_ui_text

BLOCK = (
    '<ui title="Split">\n<state total={120} people={3}/>\n'
    "<let each={total / people}/>\n"
    '<stat label="Each">{fmt(each, 2)}</stat>\n'
    '<button send="ok">Use it</button>\n</ui>'
)


def test_text_without_blocks_is_unchanged():
    text = "plain **markdown** with a < b"
    assert inline_ui_text(text) is text


def test_a_closed_block_becomes_its_text_profile_in_a_fence():
    out = inline_ui_text(f"Before\n{BLOCK}\nAfter")
    assert out.startswith("Before\n")
    assert out.endswith("\nAfter")
    assert "```text\nSplit\nEach: 40.00\n[Use it]\n```" in out
    assert "<ui" not in out and "<stat" not in out


def test_streaming_shows_a_placeholder_for_an_open_block():
    partial = 'Before\n<ui><stat label="Each">4'
    assert inline_ui_text(partial, final=False) == "Before\n" + PLACEHOLDER
    assert "Each: 4" in inline_ui_text(partial, final=True)


def test_a_closed_block_renders_while_streaming():
    out = inline_ui_text(f"{BLOCK}\nmore text", final=False)
    assert "Each: 40.00" in out and PLACEHOLDER not in out


def test_a_broken_block_shows_a_short_reason():
    out = inline_ui_text('<ui><stat label="x"/></ui>')
    assert out.startswith("\n_[UI block could not be shown: ")
    assert "value: is required" in out
    long = "<ui><card>" + "".join(f"<slider bind='g{i}'/>" for i in range(30))
    reason = inline_ui_text(long + "</card></ui>")
    assert reason.endswith("…]_\n") and len(reason) < 260


def test_body_with_backticks_uses_a_tilde_fence():
    out = inline_ui_text("<ui><code>```x```</code></ui>")
    assert out == "\n~~~text\n```x```\n~~~\n"


def test_a_block_with_no_text_form_disappears():
    empty = "<ui><state xs={[]}/><each items={xs}><text>x</text></each></ui>"
    assert inline_ui_text(f"a\n{empty}") == "a\n"
