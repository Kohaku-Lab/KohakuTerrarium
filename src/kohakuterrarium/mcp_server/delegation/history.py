"""Bounded, cursor-addressed public activity for delegated sessions."""

from collections import deque
from typing import Any

from kohakuterrarium.modules.output.base import BaseOutputModule


class DelegationHistory(BaseOutputModule):
    """Observe output without consuming the creature's configured sinks."""

    def __init__(self, capacity: int = 2000):
        super().__init__()
        self.events = deque(maxlen=capacity)
        self.sequence = 0

    def append(self, kind: str, **data: Any) -> None:
        self.events.append({"cursor": self.sequence, "kind": kind, **data})
        self.sequence += 1

    async def write(self, content: str) -> None:
        self.append("text", text=content)

    async def write_stream(self, chunk: str) -> None:
        if chunk:
            self.append("text", text=chunk)

    def on_activity(self, activity_type: str, detail: str) -> None:
        self.on_activity_with_metadata(activity_type, detail, None)

    def on_activity_with_metadata(self, activity_type, detail, metadata) -> None:
        if activity_type in {"thinking", "reasoning", "reasoning_delta"}:
            return
        self.append(activity_type, detail=detail, metadata=metadata or {})

    async def on_processing_start(self) -> None:
        self.append("processing_start")

    async def on_processing_end(self) -> None:
        self.append("processing_end")

    def page(self, cursor: int = 0, limit: int = 100) -> dict:
        if isinstance(cursor, bool) or cursor < 0 or not 1 <= limit <= 200:
            raise ValueError("cursor must be nonnegative and limit must be 1..200")
        first = self.events[0]["cursor"] if self.events else self.sequence
        rows = [event for event in self.events if event["cursor"] >= cursor][:limit]
        return {
            "events": rows,
            "next_cursor": rows[-1]["cursor"] + 1 if rows else self.sequence,
            "earliest_cursor": first,
            "truncated": cursor < first,
            "has_more": bool(rows and rows[-1]["cursor"] + 1 < self.sequence),
        }
