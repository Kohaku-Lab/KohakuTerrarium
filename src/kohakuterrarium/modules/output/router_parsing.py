"""Parser-event routing for :class:`OutputRouter`.

Translates :class:`ParseEvent` objects (produced by the streaming
parser in ``kohakuterrarium.parsing``) into ``OutputModule`` calls
and state-machine transitions. Lives in a mixin so the main router
file stays focused on the typed-event bus + lifecycle.

Responsibilities:

- ``route(event)`` — top-level dispatch on ParseEvent variants.
- ``_handle_text`` — text chunk routing under the suppression state
  machine (TOOL_BLOCK, SUBAGENT_BLOCK, etc. silence raw text by
  default).
- ``_handle_output`` — explicit ``[/output_<target>]…`` blocks routed
  to a named output module, with completed-output tracking for
  controller feedback.
- ``_handle_assistant_image`` — fans assistant image parts to every
  attached output (default + secondaries).
- ``_handle_block_start`` / ``_handle_block_end`` — drive the
  ``OutputState`` enum.

The mixin requires the host class to expose:

- ``self._state`` (:class:`OutputState`)
- ``self.default_output`` and ``self._secondary_outputs``
- ``self.named_outputs``
- ``self.suppress_tool_blocks`` / ``self.suppress_subagent_blocks``
- ``self._pending_tool_calls`` / ``_pending_subagent_calls`` /
  ``_pending_commands``
- ``self._completed_outputs``
"""

from __future__ import annotations

from kohakuterrarium.modules.output.router_state import CompletedOutput, OutputState
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


class OutputRouterParseEventMixin:
    """Parser-event routing for :class:`OutputRouter`."""

    async def route(self, event: ParseEvent) -> None:
        """Route a parse event to the appropriate handler."""
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
        """Handle an explicit ``[/output_<target>]…`` block.

        Routes to the named output module if registered. Tracks
        completed outputs for feedback to the controller.
        """
        target = event.target
        content = event.content

        if target in self.named_outputs:
            output_module = self.named_outputs[target]
            try:
                await output_module.write(content)
                self._completed_outputs.append(
                    CompletedOutput(target=target, content=content, success=True)
                )
                logger.debug(
                    "Output sent to target", target=target, content_len=len(content)
                )
            except Exception as e:
                self._completed_outputs.append(
                    CompletedOutput(
                        target=target, content=content, success=False, error=str(e)
                    )
                )
                logger.error("Output failed", target=target, error=str(e))
        else:
            logger.warning(
                "Unknown output target, sending to default",
                target=target,
                available=list(self.named_outputs.keys()),
            )
            await self.default_output.write(f"[output_{target}] {content}")
            self._completed_outputs.append(
                CompletedOutput(
                    target=f"{target}(default)", content=content, success=True
                )
            )

    def _handle_block_start(self, block_type: str) -> None:
        """Handle block start event."""
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
