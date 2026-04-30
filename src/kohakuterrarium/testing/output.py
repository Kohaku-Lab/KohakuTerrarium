"""Output recording module for test assertions."""

from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.output.event import OutputEvent


@dataclass
class ActivityRecord:
    """Record of an on_activity call."""

    activity_type: str
    detail: str


@dataclass
class EventRecord:
    """Record of an OutputEvent emit() call."""

    type: str
    content: str | Any = ""
    payload: dict = field(default_factory=dict)


class OutputRecorder(BaseOutputModule):
    """
    Records all output for test assertions.

    Captures writes, streaming chunks, activity notifications,
    and processing lifecycle events separately.

    Usage:
        recorder = OutputRecorder()
        await recorder.write("hello")
        await recorder.write_stream("chunk1")
        await recorder.write_stream("chunk2")

        assert recorder.all_text == "chunk1chunk2hello"
        assert recorder.writes == ["hello"]
        assert recorder.streams == ["chunk1", "chunk2"]
    """

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
        """Record the typed event AND forward to legacy hooks.

        Tests that assert on ``recorder.activities`` / ``recorder.streams``
        keep working because we still drive the legacy methods. Tests
        that want to assert on typed events use ``recorder.events``.
        """
        detail = event.content if isinstance(event.content, str) else ""
        self.events.append(
            EventRecord(type=event.type, content=detail, payload=dict(event.payload))
        )
        await super().emit(event)

    def reset(self) -> None:
        """Reset all recorded state. Called between turns by OutputRouter."""
        self.writes.clear()
        self.streams.clear()
        # Note: activities and events NOT cleared on reset (accumulate across turns)
        self._flushed = 0

    def clear_all(self) -> None:
        """Clear everything including activities and events."""
        self.reset()
        self.activities.clear()
        self.events.clear()
        self.processing_starts = 0
        self.processing_ends = 0

    # =========================================================================
    # Assertion Helpers
    # =========================================================================

    @property
    def all_text(self) -> str:
        """All streamed + written text concatenated."""
        return "".join(self.streams) + "".join(self.writes)

    @property
    def stream_text(self) -> str:
        """All streamed chunks concatenated."""
        return "".join(self.streams)

    @property
    def has_output(self) -> bool:
        """Whether any text was outputted."""
        return bool(self.writes or self.streams)

    def activity_types(self) -> list[str]:
        """Get list of activity types in order."""
        return [a.activity_type for a in self.activities]

    def activities_of_type(self, activity_type: str) -> list[ActivityRecord]:
        """Filter activities by type."""
        return [a for a in self.activities if a.activity_type == activity_type]

    def assert_no_text(self, msg: str = "") -> None:
        """Assert no text was written or streamed."""
        detail = f"Expected no output, got: {self.all_text[:100]}"
        if msg:
            detail += f" — {msg}"
        assert not self.has_output, detail

    def assert_text_contains(self, substring: str, msg: str = "") -> None:
        """Assert output contains substring."""
        detail = f"Expected '{substring}' in output: {self.all_text[:200]}"
        if msg:
            detail += f" — {msg}"
        assert substring in self.all_text, detail

    def assert_activity_count(self, activity_type: str, expected: int) -> None:
        """Assert specific activity type occurred N times."""
        actual = len(self.activities_of_type(activity_type))
        assert (
            actual == expected
        ), f"Expected {expected} '{activity_type}' activities, got {actual}"
