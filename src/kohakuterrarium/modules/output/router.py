"""
Output router - routes parse events to appropriate output modules.

Uses a simple state machine to handle different output modes.
"""

import asyncio

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.modules.output.event import OutputEvent, UIReply
from kohakuterrarium.modules.output.router_interactive import (
    OutputRouterInteractiveMixin,
)
from kohakuterrarium.modules.output.router_parsing import OutputRouterParseEventMixin
from kohakuterrarium.modules.output.router_state import CompletedOutput, OutputState
from kohakuterrarium.parsing import (
    CommandEvent,
    OutputCallEvent,
    SubAgentCallEvent,
    ToolCallEvent,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class OutputRouter(OutputRouterParseEventMixin, OutputRouterInteractiveMixin):
    """Routes parse events to appropriate output modules.

    Handles:
    - Text events → default output module (stdout)
    - OutputCallEvent → named output module (e.g., discord, tts)
    - Tool/subagent events → suppress text, queue for handling
    - Commands → queue for handling

    The implementation is split across mixins so each file stays
    focused:

    - :class:`OutputRouterParseEventMixin` (``router_parsing.py``) —
      ``route(parse_event)`` and the per-ParseEvent handlers
      (``_handle_text``, ``_handle_output``, ``_handle_assistant_image``,
      block start/end).
    - :class:`OutputRouterInteractiveMixin` (``router_interactive.py``)
      — Phase B interactive bus (``emit_and_wait``, ``submit_reply``,
      supersede broadcast).
    - This file — typed-event ``emit()`` dispatch, secondary-output
      management, lifecycle (``start``/``stop``/``flush``/processing
      hooks).

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

        # Phase B: pending interactive events awaiting a UIReply.
        # Keyed by event.id; values are Futures that resolve when a
        # renderer submits the reply via ``submit_reply``.
        self._pending_replies: dict[str, asyncio.Future[UIReply]] = {}

        # Phase B: outputs that submit replies (TUI / web bridge)
        # need a reference back to the router. Set duck-typed ``_router``
        # on every output that exposes it.
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
        self._maybe_link_router(output)

    def _maybe_link_router(self, output: OutputModule) -> None:
        """Set ``output._router`` to ``self`` if the output exposes the
        slot, so interactive renderers (TUI / web bridge) can call back
        into ``router.submit_reply``. Outputs without the slot are
        unaffected.
        """
        try:
            object.__setattr__(output, "_router", self)
        except Exception:
            # Some output types may forbid arbitrary attribute setting
            # (e.g. strict slots). Best-effort; non-interactive
            # renderers don't need this.
            pass

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
            case (
                "ask_text"
                | "confirm"
                | "selection"
                | "progress"
                | "notification"
                | "card"
                | "ui_supersede"
            ):
                # Phase B kinds. Fan via outputs' ``emit()`` so renderers
                # see the typed event with full payload — the legacy
                # ``on_activity`` path would lose the structure.
                await self._fan_event_to_outputs(event)
            case _:
                self._dispatch_activity_event(event)

    async def _fan_event_to_outputs(self, event: OutputEvent) -> None:
        """Call ``emit()`` on default + every secondary output.

        Used for Phase B event types that carry rich payloads — those
        bypass the legacy activity dispatch because renderers want the
        typed event. Failures in one renderer don't stop the others.
        """
        targets = [self.default_output, *self._secondary_outputs]
        for target in targets:
            try:
                await target.emit(event)
            except Exception as e:  # pragma: no cover — defensive
                logger.debug(
                    "output emit failed",
                    output=type(target).__name__,
                    event_type=event.type,
                    error=str(e),
                    exc_info=True,
                )

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

    # ─────────────────────────────────────────────────────────────
    # Phase B: interactive bus
    # ─────────────────────────────────────────────────────────────

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

    # ParseEvent routing (route, _handle_text, _handle_output,
    # _handle_block_start/end, _handle_assistant_image) lives in
    # OutputRouterParseEventMixin (router_parsing.py).

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


__all__ = ["CompletedOutput", "OutputRouter", "OutputState"]
