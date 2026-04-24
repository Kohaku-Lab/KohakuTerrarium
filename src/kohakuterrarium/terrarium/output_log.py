"""Output log capture -- tee wrapper for creature observability."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class LogEntry:
    """A single entry in the output log."""

    timestamp: datetime
    content: str
    entry_type: str = "text"  # "text", "stream_flush", "activity"
    metadata: dict[str, Any] = field(default_factory=dict)

    def preview(self, max_len: int = 100) -> str:
        """Return a truncated preview of the content."""
        if len(self.content) <= max_len:
            return self.content
        return self.content[:max_len] + "..."


class OutputLogCapture:
    """
    Tee wrapper that captures output into a ring buffer.

    Wraps an existing OutputModule. All output goes to the wrapped
    module AND is logged into a deque for later retrieval.

    Usage::

        original_output = creature.agent.output_router.default_output
        capture = OutputLogCapture(original_output, max_entries=100)
        creature.agent.output_router.default_output = capture

        # Later:
        entries = capture.get_entries(last_n=10)
    """

    def __init__(self, wrapped: OutputModule, max_entries: int = 100):
        self._wrapped = wrapped
        self._entries: deque[LogEntry] = deque(maxlen=max_entries)
        self._stream_buffer: str = ""
        self._max_entries = max_entries

    # ------------------------------------------------------------------
    # OutputModule protocol
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the wrapped output module."""
        await self._wrapped.start()

    async def stop(self) -> None:
        """Flush remaining buffer, then stop the wrapped module."""
        await self.flush()
        await self._wrapped.stop()

    async def write(self, content: str) -> None:
        """Write content to wrapped module and log it."""
        await self._wrapped.write(content)
        if content:
            self._entries.append(
                LogEntry(
                    timestamp=datetime.now(),
                    content=content,
                    entry_type="text",
                )
            )

    async def write_stream(self, chunk: str) -> None:
        """Stream a chunk to wrapped module and accumulate in buffer."""
        await self._wrapped.write_stream(chunk)
        self._stream_buffer += chunk

    async def flush(self) -> None:
        """Flush wrapped module and log any accumulated stream buffer."""
        await self._wrapped.flush()
        if self._stream_buffer:
            self._entries.append(
                LogEntry(
                    timestamp=datetime.now(),
                    content=self._stream_buffer,
                    entry_type="stream_flush",
                )
            )
            self._stream_buffer = ""

    async def on_processing_start(self) -> None:
        """Forward processing start to wrapped module."""
        await self._wrapped.on_processing_start()

    async def on_processing_end(self) -> None:
        """Forward processing end to wrapped module."""
        await self._wrapped.on_processing_end()

    def on_activity(self, activity_type: str, detail: str) -> None:
        """Forward activity to wrapped module and log it."""
        self._wrapped.on_activity(activity_type, detail)
        self._entries.append(
            LogEntry(
                timestamp=datetime.now(),
                content=detail,
                entry_type="activity",
                metadata={"activity_type": activity_type},
            )
        )

    # ------------------------------------------------------------------
    # Log access
    # ------------------------------------------------------------------

    def get_entries(
        self,
        last_n: int = 20,
        entry_type: str | None = None,
    ) -> list[LogEntry]:
        """Get recent log entries, optionally filtered by type."""
        entries = list(self._entries)
        if entry_type:
            entries = [e for e in entries if e.entry_type == entry_type]
        return entries[-last_n:]

    def get_text(self, last_n: int = 10) -> str:
        """Get recent text output concatenated (excludes activity)."""
        text_entries = self.get_entries(last_n=last_n, entry_type=None)
        return "\n".join(
            e.content for e in text_entries if e.entry_type in ("text", "stream_flush")
        )

    def clear(self) -> None:
        """Clear the log buffer."""
        self._entries.clear()
        self._stream_buffer = ""

    @property
    def entry_count(self) -> int:
        """Number of entries currently in the log."""
        return len(self._entries)

    # ------------------------------------------------------------------
    # Pass-through helpers
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Pass through reset to wrapped module if supported."""
        if hasattr(self._wrapped, "reset"):
            self._wrapped.reset()
