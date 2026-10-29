"""Unit tests for :mod:`kohakuterrarium.builtins.cli_rich.blocks.message`."""

from rich.console import Console

from kohakuterrarium.builtins.cli_rich.blocks.message import AssistantMessageBlock

BLOCK = '<ui title="Split"><state n={3}/><stat label="Each">{120 / n}</stat></ui>'


def _render(renderable) -> str:
    console = Console(width=80, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def test_streaming_shows_a_placeholder_for_an_open_block():
    block = AssistantMessageBlock()
    block.append("Here:\n<ui><stat label=")
    out = _render(block)
    assert "building UI" in out and "<stat" not in out


def test_committed_text_draws_blocks_as_their_text_profile():
    block = AssistantMessageBlock()
    block.append(f"Here:\n{BLOCK}\nDone.")
    block.finish()
    live = _render(block)
    committed = _render(block.to_committed())
    for out in (live, committed):
        assert "Each: 40" in out and "<ui" not in out
    assert "Done." in committed


def test_plain_text_is_unchanged():
    block = AssistantMessageBlock()
    block.append("a < b")
    assert "a < b" in _render(block.to_committed())
