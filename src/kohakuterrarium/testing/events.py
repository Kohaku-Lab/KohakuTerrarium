"""Event recording for testing event flow and ordering."""

import time
from dataclasses import dataclass, field


@dataclass
class RecordedEvent:
    """Capture one event's monotonic timestamp, type, source, and metadata."""

    timestamp: float
    event_type: str
    content: str
    source: str
    metadata: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        content_preview = (
            self.content[:50] + "..." if len(self.content) > 50 else self.content
        )
        return f"RecordedEvent({self.source}/{self.event_type}: {content_preview!r})"


class EventRecorder:
    """Record event flow and provide filters and ordering assertions."""

    def __init__(self):
        self.events: list[RecordedEvent] = []

    def record(
        self,
        event_type: str,
        content: str = "",
        source: str = "unknown",
        **metadata: object,
    ) -> None:
        """Append an event stamped with the current monotonic time."""
        self.events.append(
            RecordedEvent(
                timestamp=time.monotonic(),
                event_type=event_type,
                content=content,
                source=source,
                metadata=metadata,
            )
        )

    def clear(self) -> None:
        """Remove all recorded events."""
        self.events.clear()

    @property
    def count(self) -> int:
        return len(self.events)

    def of_type(self, event_type: str) -> list[RecordedEvent]:
        """Return events with the requested type."""
        return [e for e in self.events if e.event_type == event_type]

    def of_source(self, source: str) -> list[RecordedEvent]:
        """Return events from the requested source."""
        return [e for e in self.events if e.source == source]

    def types_in_order(self) -> list[str]:
        """Return event types in recorded order."""
        return [e.event_type for e in self.events]

    def sources_in_order(self) -> list[str]:
        """Return event sources in recorded order."""
        return [e.source for e in self.events]

    def assert_order(self, *expected_types: str) -> None:
        """Assert the expected types appear as an ordered subsequence."""
        actual = self.types_in_order()
        idx = 0
        for expected in expected_types:
            found = False
            while idx < len(actual):
                if actual[idx] == expected:
                    found = True
                    idx += 1
                    break
                idx += 1
            assert found, (
                f"Expected event '{expected}' not found in remaining events "
                f"after index {idx}. Full order: {actual}"
            )

    def assert_before(self, first: str, second: str) -> None:
        """Assert the first occurrence of one type precedes another."""
        first_idx = next(
            (i for i, e in enumerate(self.events) if e.event_type == first), None
        )
        second_idx = next(
            (i for i, e in enumerate(self.events) if e.event_type == second), None
        )
        assert first_idx is not None, f"Event '{first}' not found"
        assert second_idx is not None, f"Event '{second}' not found"
        assert (
            first_idx < second_idx
        ), f"Expected '{first}' (idx={first_idx}) before '{second}' (idx={second_idx})"
