"""Output recording module for test assertions."""

from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.output.event import OutputEvent


@dataclass
class ActivityRecord:
    """Capture one legacy activity notification."""

    activity_type: str
    detail: str


@dataclass
class EventRecord:
    """Capture one typed output event."""

    type: str
    content: str | Any = ""
    payload: dict = field(default_factory=dict)


class OutputRecorder(BaseOutputModule):
    """Capture text, activities, typed events, flushes, and lifecycle calls."""

    def __init__(self):
        super().__init__()
        self.writes: list[str] = []
        self.streams: list[str] = []
        self.activities: list[ActivityRecord] = []
        self.events: list[EventRecord] = []
        self.processing_starts: int = 0
        self.processing_ends: int = 0
        self._flushed: int = 0

    async def write(self, content: str) -> None:
        self.writes.append(content)

    async def write_stream(self, chunk: str) -> None:
        self.streams.append(chunk)

    async def flush(self) -> None:
        self._flushed += 1

    async def on_processing_start(self) -> None:
        self.processing_starts += 1

    async def on_processing_end(self) -> None:
        self.processing_ends += 1

    def on_activity(self, activity_type: str, detail: str) -> None:
        self.activities.append(
            ActivityRecord(activity_type=activity_type, detail=detail)
        )

    async def emit(self, event: OutputEvent) -> None:
        """Record a typed event and forward it through legacy output hooks."""
        detail = event.content if isinstance(event.content, str) else ""
        self.events.append(
            EventRecord(type=event.type, content=detail, payload=dict(event.payload))
        )
        await super().emit(event)

    def reset(self) -> None:
        """Clear per-turn text and flush state while retaining history."""
        self.writes.clear()
        self.streams.clear()
        # Activities and typed events intentionally accumulate across turns.
        self._flushed = 0

    def clear_all(self) -> None:
        """Clear per-turn state and accumulated history."""
        self.reset()
        self.activities.clear()
        self.events.clear()
        self.processing_starts = 0
        self.processing_ends = 0

    @property
    def all_text(self) -> str:
        """Return streamed text followed by completed writes."""
        return "".join(self.streams) + "".join(self.writes)

    @property
    def stream_text(self) -> str:
        """Concatenate all streamed chunks."""
        return "".join(self.streams)

    @property
    def has_output(self) -> bool:
        """Return whether any text was written or streamed."""
        return bool(self.writes or self.streams)

    def activity_types(self) -> list[str]:
        """Return activity types in recorded order."""
        return [a.activity_type for a in self.activities]

    def activities_of_type(self, activity_type: str) -> list[ActivityRecord]:
        """Return activities of the requested type."""
        return [a for a in self.activities if a.activity_type == activity_type]

    def assert_no_text(self, msg: str = "") -> None:
        """Assert that no text was written or streamed."""
        detail = f"Expected no output, got: {self.all_text[:100]}"
        if msg:
            detail += f" — {msg}"
        assert not self.has_output, detail

    def assert_text_contains(self, substring: str, msg: str = "") -> None:
        """Assert that combined output contains a substring."""
        detail = f"Expected '{substring}' in output: {self.all_text[:200]}"
        if msg:
            detail += f" — {msg}"
        assert substring in self.all_text, detail

    def assert_activity_count(self, activity_type: str, expected: int) -> None:
        """Assert an activity type occurred the expected number of times."""
        actual = len(self.activities_of_type(activity_type))
        assert (
            actual == expected
        ), f"Expected {expected} '{activity_type}' activities, got {actual}"
