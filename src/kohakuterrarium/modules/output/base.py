"""Define output delivery protocols and typed-event compatibility behavior."""

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from kohakuterrarium.modules.output.event import OutputEvent


@runtime_checkable
class OutputModule(Protocol):
    """Define lifecycle, streaming, activity, and typed-event output delivery."""

    async def start(self) -> None:
        """Start the output module."""
        ...

    async def stop(self) -> None:
        """Stop the output module."""
        ...

    async def write(self, content: str) -> None:
        """Write one complete content value."""
        ...

    async def write_stream(self, chunk: str) -> None:
        """Write one partial streaming chunk."""
        ...

    async def flush(self) -> None:
        """Flush any buffered content."""
        ...

    async def on_processing_start(self) -> None:
        """Called when agent starts processing (before LLM generates)."""
        ...

    async def on_processing_end(self) -> None:
        """Called when agent finishes processing (after LLM generates)."""
        ...

    def on_activity(self, activity_type: str, detail: str) -> None:
        """Receive one tool or sub-agent activity notification."""
        ...

    def on_assistant_image(
        self,
        url: str,
        *,
        detail: str = "auto",
        source_type: str | None = None,
        source_name: str | None = None,
        revised_prompt: str | None = None,
    ) -> None:
        """Receive a structured assistant image for optional rendering."""
        ...

    async def on_user_input(self, text: str) -> None:
        """Receive user input before processing starts."""
        ...

    async def on_resume(self, events: list[dict]) -> None:
        """Receive historical events for user-facing session replay."""
        ...

    async def emit(self, event: OutputEvent) -> None:
        """Receive a typed event directly or through legacy method adaptation."""
        ...


class BaseOutputModule(ABC):
    """Provide lifecycle defaults and typed-event adaptation for outputs."""

    def __init__(self):
        self._running = False

    @property
    def is_running(self) -> bool:
        """Check if module is running."""
        return self._running

    async def start(self) -> None:
        """Start the output module."""
        self._running = True
        await self._on_start()

    async def stop(self) -> None:
        """Stop the output module."""
        await self.flush()
        self._running = False
        await self._on_stop()

    async def _on_start(self) -> None:
        """Handle subclass-specific startup."""

    async def _on_stop(self) -> None:
        """Handle subclass-specific shutdown."""

    @abstractmethod
    async def write(self, content: str) -> None:
        """Write complete content."""
        ...

    async def write_stream(self, chunk: str) -> None:
        """Write streaming chunk. Default calls write()."""
        await self.write(chunk)

    async def flush(self) -> None:
        """Flush buffered content. Default is no-op."""

    async def on_processing_start(self) -> None:
        """Called when agent starts processing. Default is no-op."""

    async def on_processing_end(self) -> None:
        """Called when agent finishes processing. Default is no-op."""

    def on_activity(self, activity_type: str, detail: str) -> None:
        """Called when tool/subagent activity occurs. Default is no-op."""

    def on_assistant_image(
        self,
        url: str,
        *,
        detail: str = "auto",
        source_type: str | None = None,
        source_name: str | None = None,
        revised_prompt: str | None = None,
    ) -> None:
        """Called when the assistant emits a structured image. Default no-op."""

    async def on_user_input(self, text: str) -> None:
        """Called when user input is received. Default is no-op."""

    async def on_resume(self, events: list[dict]) -> None:
        """Called during session resume with historical events. Default is no-op."""

    async def emit(self, event: OutputEvent) -> None:
        """Forward typed events to legacy methods for backward-compatible outputs."""
        match event.type:
            case "text":
                content = event.content
                if isinstance(content, str):
                    await self.write_stream(content)
            case "processing_start":
                await self.on_processing_start()
            case "processing_end":
                await self.on_processing_end()
            case "user_input":
                content = event.content
                if isinstance(content, str):
                    await self.on_user_input(content)
            case "assistant_image":
                payload = event.payload
                self.on_assistant_image(
                    payload["url"],
                    detail=payload.get("detail", "auto"),
                    source_type=payload.get("source_type"),
                    source_name=payload.get("source_name"),
                    revised_prompt=payload.get("revised_prompt"),
                )
            case "resume_batch":
                await self.on_resume(event.payload.get("events", []))
            case _:
                # Structured payloads use the metadata-aware activity hook when present.
                detail = event.content if isinstance(event.content, str) else ""
                if event.payload and hasattr(self, "on_activity_with_metadata"):
                    self.on_activity_with_metadata(event.type, detail, event.payload)
                else:
                    self.on_activity(event.type, detail)
