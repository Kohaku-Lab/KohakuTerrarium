"""
Output module protocol and base class.

Output modules handle the final delivery of agent output.
"""

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from kohakuterrarium.modules.output.event import OutputEvent


@runtime_checkable
class OutputModule(Protocol):
    """
    Protocol for output modules.

    Output modules receive content from the controller/router
    and deliver it to the appropriate destination.
    """

    async def start(self) -> None:
        """Start the output module."""
        ...

    async def stop(self) -> None:
        """Stop the output module."""
        ...

    async def write(self, content: str) -> None:
        """
        Write complete content.

        Args:
            content: Full content to output
        """
        ...

    async def write_stream(self, chunk: str) -> None:
        """
        Write a streaming chunk.

        Args:
            chunk: Partial content chunk
        """
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
        """
        Called when tool/subagent activity occurs.

        Args:
            activity_type: "tool_start", "tool_done", "tool_error",
                          "subagent_start", "subagent_done", "subagent_error"
            detail: Human-readable detail string
        """
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
        """Called when the assistant emits a structured image part.

        Implementations that render to a user (stdout / TUI / WS)
        should surface the image. Default is no-op for text-only
        outputs.
        """
        ...

    async def on_user_input(self, text: str) -> None:
        """Called when user input is received, before processing starts.

        Output modules can render the user's message (e.g. as a panel).
        Default is no-op.
        """
        ...

    async def on_resume(self, events: list[dict]) -> None:
        """Called during session resume with historical events.

        Output modules that render to users (TUI, stdout) can implement
        this to show previous conversation history. Default is no-op.

        Args:
            events: List of event dicts from SessionStore.get_events().
                    Each has at minimum {type, ts}. Common types:
                    user_input (content), text (content),
                    tool_call (name, args), tool_result (name, output),
                    processing_start, processing_end.
        """
        ...

    async def emit(self, event: OutputEvent) -> None:
        """Receive a typed output event.

        Bus-level entry point for the unified output system. The default
        implementation on ``BaseOutputModule`` forwards each event to
        the legacy imperative methods (``write_stream``,
        ``on_processing_start``, ``on_activity`` …) so subclasses that
        haven't been migrated to consume events directly keep working.

        Renderers that want to consume events natively override this
        method and dispatch on ``event.type`` themselves.
        """
        ...


class BaseOutputModule(ABC):
    """
    Base class for output modules.

    Provides common functionality for output handling.
    """

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
        """Called when module starts. Override in subclass."""
        pass

    async def _on_stop(self) -> None:
        """Called when module stops. Override in subclass."""
        pass

    @abstractmethod
    async def write(self, content: str) -> None:
        """Write complete content. Must be implemented by subclass."""
        ...

    async def write_stream(self, chunk: str) -> None:
        """Write streaming chunk. Default calls write()."""
        await self.write(chunk)

    async def flush(self) -> None:
        """Flush buffered content. Default is no-op."""
        pass

    async def on_processing_start(self) -> None:
        """Called when agent starts processing. Default is no-op."""
        pass

    async def on_processing_end(self) -> None:
        """Called when agent finishes processing. Default is no-op."""
        pass

    def on_activity(self, activity_type: str, detail: str) -> None:
        """Called when tool/subagent activity occurs. Default is no-op."""
        pass

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
        pass

    async def on_user_input(self, text: str) -> None:
        """Called when user input is received. Default is no-op."""
        pass

    async def on_resume(self, events: list[dict]) -> None:
        """Called during session resume with historical events. Default is no-op."""
        pass

    async def emit(self, event: OutputEvent) -> None:
        """Default emit() — forwards typed events to legacy methods.

        Phase A: every legacy method has a corresponding event type.
        This switch keeps subclasses that don't override emit() working
        identically to before. Subclasses that want to consume events
        natively override emit() and bypass this switch entirely.
        """
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
                # Activity event. Use metadata-aware hook if present
                # and the payload has structured data, mirroring the
                # router's notify_activity dispatch.
                detail = event.content if isinstance(event.content, str) else ""
                if event.payload and hasattr(self, "on_activity_with_metadata"):
                    self.on_activity_with_metadata(event.type, detail, event.payload)
                else:
                    self.on_activity(event.type, detail)
