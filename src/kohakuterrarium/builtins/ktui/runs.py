"""Runs of consecutive tool calls, grouped into batch cells like the web's tool batch.

The view asks :meth:`ToolRuns.parent` where each call goes: the open run's batch cell (made on
demand) or, with batching off (``size`` 0, verbosity high), the creature's document. The engine shows a
batch as its calls below ``size`` and folds it into "N tool calls" from there on. Anything that is
not a tool call closes the run (:meth:`end`); a closed run seals once its last call is done,
because an unsealed cell holds back every transcript line after it.
"""

from kohakutui.ui import Cell, Code, Node, Scroll

from kohakuterrarium.builtins.ktui.format import BATCH_TITLE

BATCH_SIZE = 3
PREVIEW_LINES = 12
# an opened result keeps at most this many lines (the box shows PREVIEW_LINES at a time)
OUTPUT_LINES = 400


class ToolRuns:
    """The open run and every unsealed batch with the number of its calls still running."""

    def __init__(self, doc: Node, size: int = BATCH_SIZE):
        self.doc = doc
        self.size = size
        self._open: Node | None = None
        self._batches: dict[int, tuple[Node, int]] = {}
        self._batch_of: dict[str, int] = {}

    def parent(self, job_id: str) -> Node:
        """Where the call ``job_id`` goes; it counts as running until :meth:`done`."""
        if self.size <= 0:
            return self.doc
        if self._open is None:
            self._open = self.doc.add(
                Cell(role="tool", batch=self.size, title=BATCH_TITLE)
            )
            self._batches[self._open.id] = (self._open, 0)
        node, running = self._batches[self._open.id]
        self._batches[self._open.id] = (node, running + 1)
        self._batch_of[job_id] = self._open.id
        return self._open

    def end(self) -> None:
        """Closes the open run; it seals now when none of its calls is still running."""
        run, self._open = self._open, None
        if run is not None and self._batches.get(run.id, (run, 0))[1] == 0:
            self._batches.pop(run.id, None)
            run.seal()

    def done(self, job_id: str) -> None:
        """A call ended (or left the view): a closed run whose last call it was seals."""
        batch_id = self._batch_of.pop(job_id, None)
        if batch_id is None or batch_id not in self._batches:
            return
        node, running = self._batches[batch_id]
        running = max(0, running - 1)
        self._batches[batch_id] = (node, running)
        if running == 0 and (self._open is None or self._open.id != batch_id):
            del self._batches[batch_id]
            node.seal()

    def seal_all(self) -> None:
        """Seals every batch now (the view is left or the turn interrupted)."""
        self._open = None
        for node, _ in self._batches.values():
            node.seal()
        self._batches.clear()
        self._batch_of.clear()


def output_box(output: str) -> Scroll:
    """A tool's output in a scroll box: the last lines show, the wheel (full screen) scrolls."""
    lines = output.rstrip().splitlines()
    if len(lines) > OUTPUT_LINES:
        dropped = len(lines) - OUTPUT_LINES
        lines = [f"… {dropped} earlier lines not kept"] + lines[-OUTPUT_LINES:]
    return Scroll(height=PREVIEW_LINES, children=[Code("\n".join(lines))])
