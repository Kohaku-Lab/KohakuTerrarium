"""Typed turn events + results — the programmatic observation surface.

Before this module, a script driving an agent saw a text-only pipe
(``Creature.chat``) that silently dropped tool activity, errors, and
usage — a dead provider looked like a clean empty reply.  The typed
surface fixes that:

- :class:`TurnResult` — what ``Agent.run`` / ``Creature.run`` return:
  status, full text, the error (if any), tool calls, activities.
- :class:`TurnEvent` union — what ``run_stream`` / ``attach`` yield:
  :class:`TextChunk`, :class:`Activity`, :class:`TurnEnded`.
- :class:`TurnCapture` — the OutputRouter secondary sink that records
  a live turn (and optionally feeds an ``asyncio.Queue`` for
  streaming consumers).

Errors are first-class: the agent's internal ``processing_error``
activity is mapped onto ``TurnResult.error`` and (by default) raised
as :class:`~kohakuterrarium.errors.TurnError`.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.modules.output.base import BaseOutputModule

# Activity kinds that mean "this turn failed".
_ERROR_KINDS = ("processing_error", "turn_error")
# Activity kinds describing tool execution.
_TOOL_KINDS = ("tool_start", "tool_done", "tool_error")


@dataclass
class TextChunk:
    """A streamed piece of assistant text."""

    text: str


@dataclass
class Activity:
    """A non-text event during a turn (tool / sub-agent / status).

    ``kind`` mirrors the router's activity types: ``tool_start``,
    ``tool_done``, ``tool_error``, ``subagent_start``, ``subagent_done``,
    ``processing_start``, ``processing_end``, ``processing_error``,
    ``session_info``, ``ask_user``, ...
    """

    kind: str
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    """Outcome of one full turn.

    ``status`` is one of ``"ok"`` / ``"error"`` / ``"timeout"`` /
    ``"interrupted"``.  ``text`` is the concatenated assistant text.
    ``error`` carries the failure detail when status != ok.
    """

    status: str
    text: str = ""
    error: str | None = None
    tool_calls: list[Activity] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class TurnEnded:
    """Terminal event of a ``run_stream`` / final ``attach`` marker."""

    result: TurnResult


TurnEvent = TextChunk | Activity | TurnEnded


class TurnCapture(BaseOutputModule):
    """OutputRouter secondary sink recording one (or many) turns.

    Receives every text chunk + activity the router fans out.  With a
    ``queue`` it ALSO pushes each typed event for live streaming —
    non-destructively: the default output and other secondaries keep
    receiving everything (unlike the old ``Creature.chat`` pipe, which
    swallowed out-of-band output).
    """

    def __init__(self, queue: "asyncio.Queue[TurnEvent] | None" = None) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self.activities: list[Activity] = []
        self.error: str | None = None
        self._queue = queue

    # -- text ----------------------------------------------------------

    async def write(self, content: str) -> None:
        await self.write_stream(content)

    async def write_stream(self, chunk: str) -> None:
        if not chunk:
            return
        self.chunks.append(chunk)
        if self._queue is not None:
            self._queue.put_nowait(TextChunk(chunk))

    # -- activities ----------------------------------------------------

    def on_activity(self, activity_type: str, detail: str) -> None:
        self._record(Activity(kind=activity_type, detail=detail))

    def on_activity_with_metadata(
        self, activity_type: str, detail: str, metadata: dict | None
    ) -> None:
        self._record(
            Activity(kind=activity_type, detail=detail, metadata=metadata or {})
        )

    async def on_processing_start(self) -> None:
        self._record(Activity(kind="processing_start"))

    async def on_processing_end(self) -> None:
        self._record(Activity(kind="processing_end"))

    def _record(self, activity: Activity) -> None:
        self.activities.append(activity)
        if activity.kind in _ERROR_KINDS and self.error is None:
            self.error = activity.detail or activity.kind
        if self._queue is not None:
            self._queue.put_nowait(activity)

    # -- result --------------------------------------------------------

    @property
    def text(self) -> str:
        return "".join(self.chunks)

    def build_result(self, status: str, *, duration_s: float = 0.0) -> TurnResult:
        """Assemble the :class:`TurnResult` for the captured turn."""
        if status == "ok" and self.error is not None:
            status = "error"
        usage: dict[str, Any] | None = None
        for activity in reversed(self.activities):
            meta = activity.metadata
            if meta and ("total_tokens" in meta or "usage" in meta):
                usage = meta.get("usage") or meta
                break
        return TurnResult(
            status=status,
            text=self.text,
            error=self.error,
            tool_calls=[a for a in self.activities if a.kind in _TOOL_KINDS],
            activities=list(self.activities),
            usage=usage,
            duration_s=duration_s,
        )


class AgentEventStream:
    """Open-ended typed event stream over an agent's output router.

    The body behind ``Creature.attach()``: an async context manager
    whose iterator yields :class:`TurnEvent`\\ s for as long as it stays
    attached.  Non-destructive — a plain secondary sink; any number of
    streams can observe the same agent concurrently.
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._queue: "asyncio.Queue[TurnEvent]" = asyncio.Queue()
        self._capture = TurnCapture(queue=self._queue)
        self._open = False

    async def __aenter__(self) -> "AgentEventStream":
        self._agent.output_router.add_secondary(self._capture)
        self._open = True
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        """Detach from the router; the iterator then stops."""
        if self._open:
            self._agent.output_router.remove_secondary(self._capture)
            self._open = False
            # Unblock a pending __anext__.
            self._queue.put_nowait(TurnEnded(TurnResult(status="ok")))

    def __aiter__(self) -> "AgentEventStream":
        return self

    async def __anext__(self) -> TurnEvent:
        if not self._open and self._queue.empty():
            raise StopAsyncIteration
        event = await self._queue.get()
        if not self._open and isinstance(event, TurnEnded):
            raise StopAsyncIteration
        return event
