"""Typed turn results, streamed events, and non-destructive output capture."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.modules.output.base import BaseOutputModule

_ERROR_KINDS = ("processing_error", "turn_error")
_TOOL_KINDS = ("tool_start", "tool_done", "tool_error")


@dataclass
class TextChunk:
    """A streamed piece of assistant text."""

    text: str


@dataclass
class Activity:
    """Represent non-text tool, sub-agent, status, or interaction activity."""

    kind: str
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    """Describe one complete turn, including output, activity, usage, and failure.

    ``correlation_id`` lets out-of-band callers match settlement to the event that
    initiated the turn. ``rejected`` means the event never ran.
    """

    status: str
    text: str = ""
    error: str | None = None
    tool_calls: list[Activity] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    duration_s: float = 0.0
    correlation_id: str | None = None
    interrupted_by_user: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class TurnEnded:
    """Mark the terminal result of a streamed turn or attachment."""

    result: TurnResult


TurnEvent = TextChunk | Activity | TurnEnded


class TurnCapture(BaseOutputModule):
    """Record router output and optionally mirror it into a typed event queue.

    Capture is a secondary sink, so default outputs and other observers continue
    receiving the same events.
    """

    def __init__(self, queue: "asyncio.Queue[TurnEvent] | None" = None) -> None:
        super().__init__()
        self.chunks: list[str] = []
        self.activities: list[Activity] = []
        self.error: str | None = None
        self._queue = queue

    async def write(self, content: str) -> None:
        await self.write_stream(content)

    async def write_stream(self, chunk: str) -> None:
        if not chunk:
            return
        self.chunks.append(chunk)
        if self._queue is not None:
            self._queue.put_nowait(TextChunk(chunk))

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
    """Observe an agent's typed output until the stream is detached.

    Each attachment is an independent secondary sink, allowing concurrent
    non-destructive observers.
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
            # A sentinel releases an iterator currently blocked on the queue.
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
