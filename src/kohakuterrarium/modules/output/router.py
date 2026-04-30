"""
Output router - routes parse events to appropriate output modules.

Uses a simple state machine to handle different output modes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.parsing import (
    AssistantImageEvent,
    BlockEndEvent,
    BlockStartEvent,
    CommandEvent,
    OutputCallEvent,
    ParseEvent,
    SubAgentCallEvent,
    TextEvent,
    ToolCallEvent,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CompletedOutput:
    """Record of a completed output event."""

    target: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    success: bool = True
    error: str | None = None

    def preview(self, max_len: int = 100) -> str:
        """Get a preview of the content."""
        if len(self.content) <= max_len:
            return self.content
        return self.content[:max_len] + "..."

    def to_feedback_line(self) -> str:
        """Format as a single feedback line for controller."""
        time_str = self.timestamp.strftime("%H:%M:%S")
        if self.success:
            preview = self.preview(80)
            # Escape newlines for single-line display
            preview = preview.replace("\n", "\\n")
            return f'- [{self.target}] ({time_str}): "{preview}"'
        else:
            return f"- [{self.target}] ({time_str}): FAILED - {self.error}"


class OutputState(Enum):
    """Output routing state."""

    NORMAL = auto()  # Regular text output (stdout)
    TOOL_BLOCK = auto()  # Inside tool call block (suppress output)
    SUBAGENT_BLOCK = auto()  # Inside sub-agent block (suppress output)
    COMMAND_BLOCK = auto()  # Inside command block
    OUTPUT_BLOCK = auto()  # Inside explicit output block


class OutputRouter:
    """
    Routes parse events to appropriate output modules.

    Handles:
    - Text events → default output module (stdout)
    - OutputCallEvent → named output module (e.g., discord, tts)
    - Tool/subagent events → suppress text, queue for handling
    - Commands → queue for handling

    Note on current architecture:
        In the standard Agent flow, ToolCallEvent, SubAgentCallEvent, and
        CommandEvent are handled BEFORE reaching the router:
        - ToolCallEvent/SubAgentCallEvent: Agent handles directly from controller output
        - CommandEvent: Controller handles inline, converts to TextEvent

        The pending_* properties exist for alternative architectures where
        the router receives all events and the caller processes them afterward.
    """

    def __init__(
        self,
        default_output: OutputModule,
        *,
        named_outputs: dict[str, OutputModule] | None = None,
        suppress_tool_blocks: bool = True,
        suppress_subagent_blocks: bool = True,
    ):
        """
        Initialize output router.

        Args:
            default_output: Default output module for text (stdout)
            named_outputs: Named output modules (e.g., {"discord": DiscordOutput})
            suppress_tool_blocks: Don't output text inside tool blocks
            suppress_subagent_blocks: Don't output text inside subagent blocks
        """
        self.default_output = default_output
        self.named_outputs = named_outputs or {}
        self.suppress_tool_blocks = suppress_tool_blocks
        self.suppress_subagent_blocks = suppress_subagent_blocks

        self._state = OutputState.NORMAL
        self._pending_tool_calls: list[ToolCallEvent] = []
        self._pending_subagent_calls: list[SubAgentCallEvent] = []
        self._pending_commands: list[CommandEvent] = []
        self._pending_outputs: list[OutputCallEvent] = []

        # Track completed outputs for feedback to controller
        self._completed_outputs: list[CompletedOutput] = []

        # Secondary output modules (receive copies of all text output)
        self._secondary_outputs: list[OutputModule] = []

    @property
    def state(self) -> OutputState:
        """Current output state."""
        return self._state

    @property
    def pending_tool_calls(self) -> list[ToolCallEvent]:
        """Get and clear pending tool calls."""
        calls = self._pending_tool_calls
        self._pending_tool_calls = []
        return calls

    @property
    def pending_subagent_calls(self) -> list[SubAgentCallEvent]:
        """Get and clear pending sub-agent calls."""
        calls = self._pending_subagent_calls
        self._pending_subagent_calls = []
        return calls

    @property
    def pending_commands(self) -> list[CommandEvent]:
        """Get and clear pending commands."""
        commands = self._pending_commands
        self._pending_commands = []
        return commands

    @property
    def pending_outputs(self) -> list[OutputCallEvent]:
        """Get and clear pending output events."""
        outputs = self._pending_outputs
        self._pending_outputs = []
        return outputs

    @property
    def completed_outputs(self) -> list[CompletedOutput]:
        """Get completed outputs (does not clear - use get_and_clear_completed_outputs)."""
        return self._completed_outputs

    def get_and_clear_completed_outputs(self) -> list[CompletedOutput]:
        """Get and clear completed outputs."""
        outputs = self._completed_outputs
        self._completed_outputs = []
        return outputs

    def get_output_feedback(self) -> str | None:
        """
        Get feedback string for completed outputs and clear the list.

        Returns:
            Formatted feedback string, or None if no outputs.
        """
        outputs = self.get_and_clear_completed_outputs()
        if not outputs:
            return None

        lines = [out.to_feedback_line() for out in outputs]
        return "## Outputs Sent\n" + "\n".join(lines)

    def get_output_targets(self) -> list[str]:
        """Get list of registered output target names."""
        return list(self.named_outputs.keys())

    def add_secondary(self, output: OutputModule) -> None:
        """Add a secondary output that receives copies of all text output."""
        self._secondary_outputs.append(output)

    def remove_secondary(self, output: OutputModule) -> None:
        """Remove a secondary output."""
        self._secondary_outputs = [
            o for o in self._secondary_outputs if o is not output
        ]

    async def emit(self, event: OutputEvent) -> None:
        """Bus-level entry point for typed OutputEvents.

        Phase A semantics: fans every event type to the same set of
        targets the legacy per-method hooks already fan to. Existing
        renderers see byte-identical method calls.

        Type → routing rule:
        - ``text``: through the text state machine (default + secondaries).
        - ``processing_start`` / ``processing_end``: default + named +
          secondary outputs.
        - ``user_input``: default output only (matches today's behaviour).
        - ``assistant_image``: default + secondaries.
        - ``resume_batch``: default output only.
        - any other type (activity events): default + secondaries via
          the same dispatch ``notify_activity`` uses.
        """
        match event.type:
            case "text":
                content = event.content
                if isinstance(content, str):
                    await self._handle_text(content)
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
                self._handle_assistant_image(
                    AssistantImageEvent(
                        url=payload["url"],
                        detail=payload.get("detail", "auto"),
                        source_type=payload.get("source_type"),
                        source_name=payload.get("source_name"),
                        revised_prompt=payload.get("revised_prompt"),
                    )
                )
            case "resume_batch":
                await self.on_resume(event.payload.get("events", []))
            case _:
                self._dispatch_activity_event(event)

    def _dispatch_activity_event(self, event: OutputEvent) -> None:
        """Internal sync dispatch for activity-style OutputEvents.

        Shared by ``emit()`` (async path) and ``notify_activity`` (legacy
        sync path) so the bus is uniformly event-based regardless of
        which entry point a caller uses.

        Renderers that override ``emit()`` natively (Phase A3) take
        precedence: this helper only runs for outputs that haven't
        migrated. The check is best-effort — we look for an overridden
        ``emit`` and call it via ``asyncio`` when possible, otherwise
        fall back to the legacy ``on_activity_with_metadata`` /
        ``on_activity`` hooks.
        """
        detail = event.content if isinstance(event.content, str) else ""
        metadata = event.payload or None
        targets = [self.default_output, *self._secondary_outputs]
        for target in targets:
            if metadata and hasattr(target, "on_activity_with_metadata"):
                target.on_activity_with_metadata(event.type, detail, metadata)
            else:
                target.on_activity(event.type, detail)

    def notify_activity(
        self, activity_type: str, detail: str, metadata: dict | None = None
    ) -> None:
        """Broadcast activity to default + all secondary outputs.

        Sync entry point preserved for callers that cannot ``await``
        (trigger callbacks, plugin observers, etc.). Internally
        constructs an :class:`OutputEvent` and routes through the
        same dispatch helper as :meth:`emit`, so the bus is event-
        based for every caller.

        Args:
            activity_type: Event type (tool_start, tool_done, subagent_start, etc.)
            detail: Human-readable summary (truncated, for TUI/stdout)
            metadata: Structured data (full args, job_id, tools_used, etc.)
                      Only consumed by outputs that support it (e.g. WebSocket).
        """
        self._dispatch_activity_event(
            OutputEvent(
                type=activity_type,
                content=detail,
                payload=metadata or {},
            )
        )

    async def start(self) -> None:
        """Start the router and output modules."""
        await self.default_output.start()
        for name, output in self.named_outputs.items():
            await output.start()
            logger.debug("Named output started", output_name=name)
        logger.debug("Output router started")

    async def stop(self) -> None:
        """Stop the router and output modules."""
        for name, output in self.named_outputs.items():
            await output.stop()
            logger.debug("Named output stopped", output_name=name)
        await self.default_output.stop()
        logger.debug("Output router stopped")

    async def route(self, event: ParseEvent) -> None:
        """
        Route a parse event to appropriate handler.

        Args:
            event: Parse event to route
        """
        match event:
            case TextEvent(text=text):
                await self._handle_text(text)

            case ToolCallEvent():
                self._pending_tool_calls.append(event)
                logger.debug("Tool call queued", tool_name=event.name)

            case SubAgentCallEvent():
                self._pending_subagent_calls.append(event)
                logger.debug("Sub-agent call queued", subagent_name=event.name)

            case CommandEvent():
                self._pending_commands.append(event)
                logger.debug("Command queued", command=event.command)

            case OutputCallEvent():
                # Route to named output immediately
                await self._handle_output(event)

            case BlockStartEvent(block_type=block_type):
                self._handle_block_start(block_type)

            case BlockEndEvent(block_type=block_type):
                self._handle_block_end(block_type)

            case AssistantImageEvent():
                self._handle_assistant_image(event)

    def _handle_assistant_image(self, event: AssistantImageEvent) -> None:
        """Fan out an assistant image to every attached output module.

        Both the default output and every secondary output receives
        the notification. Text-only outputs inherit the default
        no-op; StreamOutput (API) pushes a JSON event to the WS
        queue so the frontend can render live.
        """
        targets = [self.default_output, *self._secondary_outputs]
        for target in targets:
            handler = getattr(target, "on_assistant_image", None)
            if handler is None:
                continue
            try:
                handler(
                    event.url,
                    detail=event.detail,
                    source_type=event.source_type,
                    source_name=event.source_name,
                    revised_prompt=event.revised_prompt,
                )
            except Exception as e:  # pragma: no cover — defensive
                logger.debug(
                    "on_assistant_image handler raised",
                    error=str(e),
                    exc_info=True,
                )

    async def _handle_text(self, text: str) -> None:
        """Handle text event based on current state."""
        # Always send to secondary outputs (for API streaming, logging, etc.)
        for secondary in self._secondary_outputs:
            await secondary.write_stream(text)

        match self._state:
            case OutputState.NORMAL:
                await self.default_output.write_stream(text)

            case OutputState.TOOL_BLOCK:
                if not self.suppress_tool_blocks:
                    await self.default_output.write_stream(text)

            case OutputState.SUBAGENT_BLOCK:
                if not self.suppress_subagent_blocks:
                    await self.default_output.write_stream(text)

            case OutputState.COMMAND_BLOCK:
                pass

            case OutputState.OUTPUT_BLOCK:
                pass

    async def _handle_output(self, event: OutputCallEvent) -> None:
        """
        Handle explicit output event.

        Routes to named output module if registered.
        Tracks completed outputs for feedback to controller.
        """
        target = event.target
        content = event.content

        if target in self.named_outputs:
            output_module = self.named_outputs[target]
            try:
                await output_module.write(content)
                # Track successful output
                self._completed_outputs.append(
                    CompletedOutput(target=target, content=content, success=True)
                )
                logger.debug(
                    "Output sent to target", target=target, content_len=len(content)
                )
            except Exception as e:
                # Track failed output
                self._completed_outputs.append(
                    CompletedOutput(
                        target=target, content=content, success=False, error=str(e)
                    )
                )
                logger.error("Output failed", target=target, error=str(e))
        else:
            # Unknown target - log warning, send to default
            logger.warning(
                "Unknown output target, sending to default",
                target=target,
                available=list(self.named_outputs.keys()),
            )
            await self.default_output.write(f"[output_{target}] {content}")
            # Track as completed (to default)
            self._completed_outputs.append(
                CompletedOutput(
                    target=f"{target}(default)", content=content, success=True
                )
            )

    def _handle_block_start(self, block_type: str) -> None:
        """Handle block start event."""
        # Check for output block first (format: output_<target>)
        if block_type.startswith("output_"):
            self._state = OutputState.OUTPUT_BLOCK
            return

        match block_type:
            case "tool":
                self._state = OutputState.TOOL_BLOCK
            case "subagent":
                self._state = OutputState.SUBAGENT_BLOCK
            case "command":
                self._state = OutputState.COMMAND_BLOCK

    def _handle_block_end(self, block_type: str) -> None:
        """Handle block end event."""
        self._state = OutputState.NORMAL

    async def flush(self) -> None:
        """Flush output modules."""
        await self.default_output.flush()
        for output in self.named_outputs.values():
            await output.flush()

    async def on_user_input(self, text: str) -> None:
        """Notify default output that user input was received."""
        if hasattr(self.default_output, "on_user_input"):
            await self.default_output.on_user_input(text)

    async def on_resume(self, events: list[dict]) -> None:
        """Replay session history to user-facing outputs.

        Forwards to default output only (not secondary outputs,
        since those are observers like SessionOutput/StreamOutput).
        """
        if hasattr(self.default_output, "on_resume"):
            await self.default_output.on_resume(events)

    async def on_processing_start(self) -> None:
        """Notify all output modules that processing is starting."""
        await self.default_output.on_processing_start()
        for output in self.named_outputs.values():
            await output.on_processing_start()
        for secondary in self._secondary_outputs:
            await secondary.on_processing_start()

    async def on_processing_end(self) -> None:
        """Notify all output modules that processing has ended."""
        await self.default_output.on_processing_end()
        for output in self.named_outputs.values():
            await output.on_processing_end()
        for secondary in self._secondary_outputs:
            await secondary.on_processing_end()

    def reset(self) -> None:
        """
        Reset router state for new round (within a turn).

        Note: completed_outputs is NOT cleared here - it accumulates across rounds
        and is cleared when feedback is consumed via get_output_feedback().
        """
        self._state = OutputState.NORMAL
        self._pending_tool_calls.clear()
        self._pending_subagent_calls.clear()
        self._pending_commands.clear()
        self._pending_outputs.clear()

    def clear_all(self) -> None:
        """
        Clear all state including completed outputs.

        Call this when a turn is completely finished.
        """
        self.reset()
        self._completed_outputs.clear()


class MultiOutputRouter(OutputRouter):
    """
    Router that can route to multiple output modules.

    Different content types can go to different destinations.
    """

    def __init__(
        self,
        default_output: OutputModule,
        outputs: dict[str, OutputModule] | None = None,
        **kwargs: Any,
    ):
        """
        Initialize multi-output router.

        Args:
            default_output: Default output module
            outputs: Named output modules for specific content types
            **kwargs: Additional arguments for base router
        """
        super().__init__(default_output, **kwargs)
        self.outputs = outputs or {}

    async def start(self) -> None:
        """Start all output modules."""
        await super().start()
        for output in self.outputs.values():
            await output.start()

    async def stop(self) -> None:
        """Stop all output modules."""
        for output in self.outputs.values():
            await output.stop()
        await super().stop()

    async def write_to(self, name: str, content: str) -> None:
        """
        Write to a specific named output.

        Args:
            name: Output module name
            content: Content to write
        """
        if name in self.outputs:
            await self.outputs[name].write(content)
        else:
            logger.warning("Unknown output module", output_name=name)

    async def flush(self) -> None:
        """Flush all output modules."""
        await super().flush()
        for output in self.outputs.values():
            await output.flush()
