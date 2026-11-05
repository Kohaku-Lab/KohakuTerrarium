"""One streamed assistant reply cut into Markdown and inline ``<ui>`` blocks.

``ReplyStream.feed`` takes the reply chunk by chunk. The text is split with
``kt_ui.markup_lex.split_message`` (re-run only when a line completes, because a block can
only start at a line start); each segment gets a view in the reply's cell, in order:

- Markdown streams into a ``Markdown(streaming=True)`` node as before. A line that may still
  turn out to open a block (``<ui``, ``<callout``, …) is held back until that is decided, so
  nothing is ever drawn as text and then taken back.
- A ``<ui>`` block is a ``ui_block.BlockView``: validated and drawn through ``ui_map``. While
  it streams it shows what has arrived plus "building UI…"; a block that fails shows the
  reason, as other terminals do (the ``ui.declarative`` plugin tells the model on its next
  call). Closed blocks with controls take input until they freeze (D26).
"""

import re
from typing import Any

from kohakutui import App
from kohakutui.ui import Markdown, Node, Stack, Text

from kohakuterrarium.builtins.kt_ui.inline_state import block_hash
from kohakuterrarium.builtins.kt_ui.markup_lex import IMPLICIT_COMPONENTS, split_message
from kohakuterrarium.builtins.ktui.ui_block import BlockView, UiContext

# a line that opens a block, or may still grow into one
_TAGS = ("ui", *IMPLICIT_COMPONENTS)
_COMPLETE = re.compile(rf" {{0,3}}<(?:{'|'.join(_TAGS)})(?=[\s>/]|$)")
_PARTIAL = re.compile(r" {0,3}<[a-z]*$")


def _may_open_block(line: str) -> bool:
    if _COMPLETE.match(line):
        return True
    partial = _PARTIAL.match(line)
    if not partial:
        return False
    name = line.strip()[1:]
    return any(tag.startswith(name) for tag in _TAGS)


def _safe_prefix(text: str) -> str:
    """``text`` up to the first line that might still open a block (held until decided)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if _may_open_block(line):
            return "".join(kept + "\n" for kept in lines[:i])
    return text


def _spacer(cell: Node, after_other: bool) -> None:
    # one blank line between a block and the text around it, as between paragraphs
    if after_other:
        cell.add(Text(""))


class _MarkdownView:
    def __init__(self, cell: Node, after_other: bool):
        _spacer(cell, after_other)
        self.node = cell.add(Markdown(streaming=True))
        self.shown = ""

    def update(self, text: str, final: bool) -> None:
        target = text if final else _safe_prefix(text)
        if target.startswith(self.shown):
            if len(target) > len(self.shown):
                self.node.append(target[len(self.shown) :])
        else:  # never expected: the split moved inside drawn text; redraw it whole
            self.node.update(source=target)
        self.shown = target
        if final:
            self.node.update(streaming=False)


class ReplyStream:
    """The views of one assistant reply inside its transcript cell."""

    def __init__(self, app: App, cell: Node, ctx: UiContext | None = None, turn=None):
        self.app = app
        self.cell = cell
        self.ctx = ctx
        # the reply's turn and branch, which key its blocks' saved state
        self.turn = (
            turn if turn is not None else (ctx.turn() if ctx is not None else None)
        )
        self.text = ""
        self.segments: list[dict[str, Any]] = []
        self.views: list[_MarkdownView | BlockView | None] = []

    def feed(self, chunk: str) -> None:
        self.text += chunk
        if "\n" in chunk or not self.segments:
            self.segments = split_message(self.text)
        else:  # the last segment grew within its last line
            last = self.segments[-1]
            last["text" if last["kind"] == "markdown" else "source"] += chunk
        self._sync(final=False)

    def close(self) -> None:
        """The reply is complete: every segment is final."""
        self.segments = split_message(self.text)
        self._sync(final=True)

    @property
    def interactive(self) -> bool:
        """A block of this reply still takes input."""
        return any(isinstance(v, BlockView) and v.interactive for v in self.views)

    def refresh_hints(self) -> None:
        """Redraws the blocks' key hints, which name ctrl+t only inline."""
        for view in self.views:
            if isinstance(view, BlockView):
                view.refresh_hint()

    def freeze(self) -> None:
        with self.app.batch():
            for view in self.views:
                if isinstance(view, BlockView):
                    view.freeze()

    def _sync(self, final: bool) -> None:
        seen: dict[str, int] = {}
        with self.app.batch():
            for i, segment in enumerate(self.segments):
                done = final or i < len(self.segments) - 1
                if i == len(self.views):
                    self.views.append(None)
                view = self.views[i]
                if segment["kind"] == "markdown":
                    shown = segment["text"] if done else _safe_prefix(segment["text"])
                    if view is None and not shown.strip():
                        continue  # nothing visible yet (blank, or held back): no view
                    if not isinstance(view, _MarkdownView):
                        view = self.views[i] = _MarkdownView(self.cell, self._drawn())
                    view.update(segment["text"].lstrip("\n"), done)
                else:
                    closed = segment["closed"] or done
                    digest = block_hash(segment["source"])
                    occurrence = seen.get(digest, 0)
                    if closed:
                        seen[digest] = occurrence + 1
                    if not isinstance(view, BlockView):
                        _spacer(self.cell, self._drawn())
                        holder = self.cell.add(Stack())
                        view = self.views[i] = BlockView(holder, self.ctx, self.turn)
                    view.update(segment["source"], closed, occurrence)

    def _drawn(self) -> bool:
        return any(view is not None for view in self.views)
