"""Route parsed and typed events across default, named, and secondary outputs."""

import asyncio

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.modules.output.router_interactive import (
    OutputRouterInteractiveMixin,
)
from kohakuterrarium.modules.output.router_parsing import OutputRouterParseEventMixin
from kohakuterrarium.modules.output.router_state import CompletedOutput, OutputState
from kohakuterrarium.parsing import (
    AssistantImageEvent,
    CommandEvent,
    OutputCallEvent,
    SubAgentCallEvent,
    ToolCallEvent,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class OutputRouter(OutputRouterParseEventMixin, OutputRouterInteractiveMixin):
    """Coordinate output state, typed events, lifecycle, and interactive replies."""

    def __init__(
        self,
        default_output: OutputModule,
        *,
        named_outputs: dict[str, OutputModule] | None = None,
        suppress_tool_blocks: bool = True,
        suppress_subagent_blocks: bool = True,
    ):
        """Initialize output targets, suppression state, and event queues."""
        self.default_output = default_output
        self.named_outputs = named_outputs or {}
        self.suppress_tool_blocks = suppress_tool_blocks
        self.suppress_subagent_blocks = suppress_subagent_blocks

        self._state = OutputState.NORMAL
        self._pending_tool_calls: list[ToolCallEvent] = []
        self._pending_subagent_calls: list[SubAgentCallEvent] = []
        self._pending_commands: list[CommandEvent] = []
        self._pending_outputs: list[OutputCallEvent] = []

        self._completed_outputs: list[CompletedOutput] = []

        self._secondary_outputs: list[OutputModule] = []

        # Interactive reply futures are keyed by the originating event ID.
        self._pending_replies: dict[str, asyncio.Future[UIReply]] = {}

        # Interactive outputs need a back-reference for reply submission.
        for output in (default_output, *self._secondary_outputs):
            self._maybe_link_router(output)
        for output in (self.named_outputs or {}).values():
            self._maybe_link_router(output)

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
        """Return completed outputs without consuming them."""
        return self._completed_outputs

    def get_and_clear_completed_outputs(self) -> list[CompletedOutput]:
        """Get and clear completed outputs."""
        outputs = self._completed_outputs
        self._completed_outputs = []
        return outputs

    def get_output_feedback(self) -> str | None:
        """Consume completed outputs and format controller feedback."""
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
        self._maybe_link_router(output)

    def _maybe_link_router(self, output: OutputModule) -> None:
        """Best-effort link an interactive output back to this router."""
        try:
            object.__setattr__(output, "_router", self)
        except Exception:
            # Non-interactive or slotted outputs do not require the back-reference.
            pass

    def remove_secondary(self, output: OutputModule) -> None:
        """Remove a secondary output."""
        self._secondary_outputs = [
            o for o in self._secondary_outputs if o is not output
        ]

    async def emit(self, event: OutputEvent) -> None:
        """Dispatch a typed event according to its output visibility contract."""
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
            case (
                "ask_text"
                | "confirm"
                | "selection"
                | "progress"
                | "notification"
                | "card"
                | "ui_supersede"
            ):
                # Rich UI events retain their typed payload through output emitters.
                await self._fan_event_to_outputs(event)
            case _:
                self._dispatch_activity_event(event)

    async def _fan_event_to_outputs(self, event: OutputEvent) -> None:
        """Fan a typed event to outputs while isolating renderer failures."""
        targets = [self.default_output, *self._secondary_outputs]
        for target in targets:
            try:
                await target.emit(event)
            except Exception as e:  # pragma: no cover — defensive
                logger.warning(
                    "output emit failed",
                    output=type(target).__name__,
                    event_type=event.type,
                    error=str(e),
                    exc_info=True,
                )

    def _dispatch_activity_event(self, event: OutputEvent) -> None:
        """Synchronously dispatch activity events through legacy renderer hooks."""
        detail = event.content if isinstance(event.content, str) else ""
        metadata = event.payload or None
        targets = [self.default_output, *self._secondary_outputs]
        for target in targets:
            # One renderer failure must not prevent delivery to remaining targets.
            try:
                if metadata and hasattr(target, "on_activity_with_metadata"):
                    target.on_activity_with_metadata(event.type, detail, metadata)
                else:
                    target.on_activity(event.type, detail)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Output target raised in dispatch",
                    output=type(target).__name__,
                    event_type=event.type,
                    error=str(exc),
                    exc_info=True,
                )

    def notify_activity(
        self, activity_type: str, detail: str, metadata: dict | None = None
    ) -> None:
        """Broadcast an activity event from synchronous callback paths."""
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
        """Replay session history only to the user-facing default output."""
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
        """Reset per-round state while retaining unconsumed completion feedback."""
        self._state = OutputState.NORMAL
        self._pending_tool_calls.clear()
        self._pending_subagent_calls.clear()
        self._pending_commands.clear()
        self._pending_outputs.clear()

    def clear_all(self) -> None:
        """Clear per-round state and completed output feedback after a turn."""
        self.reset()
        self._completed_outputs.clear()


__all__ = ["CompletedOutput", "OutputRouter", "OutputState"]
