"""Agent run loop and input injection: ``run_forever``, ``inject_input``."""

import asyncio
from typing import Any

from kohakuterrarium.core.agent.turn.budget_recovery import (
    sync_emergency_drop_conversation,
)
from kohakuterrarium.core.agent.turn.input import drive_input
from kohakuterrarium.core.conversation.continuation import (
    continue_on_branch,
    normalize_branch_view,
)
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.llm.message import ContentPart
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentDriveMixin:
    """Run the agent until its input ends; accept input and events from outside."""

    def _on_provider_emergency_drop(self, messages: list[dict[str, Any]]) -> None:
        """Synchronize controller conversation after provider emergency drop."""
        sync_emergency_drop_conversation(self, messages)

    async def run_forever(self) -> None:
        """Run the agent lifecycle and input loop until input exits."""
        await self.start()
        try:
            await self._drive_input()
        finally:
            await self.stop()

    async def _drive_input(self) -> None:
        """Replay resume state, fire the startup trigger, then poll input.

        Extracted from :meth:`run` so engine-managed creatures can spawn
        this as a background task in ``Creature.start`` — that's the
        path headless configured-IO agents (Discord bot, webhook
        listener, custom polling input) depend on. ``run`` keeps the
        non-engine standalone path; both routes share this body.
        """
        try:
            # Replay session history to output if resuming
            if self._pending_resume_events:
                await self.output_router.emit(
                    OutputEvent(
                        type="resume_batch",
                        payload={"events": self._pending_resume_events},
                    )
                )
                self._pending_resume_events = None

            # Re-create resumable triggers from saved state
            pending_triggers = getattr(self, "_pending_resume_triggers", None)
            if pending_triggers:
                await self._restore_triggers(pending_triggers)
                self._pending_resume_triggers = None

            await drive_input(self, startup=True)

        except KeyboardInterrupt:
            logger.info("Interrupted")
        except asyncio.CancelledError:
            logger.info("Agent cancelled")
            raise
        except Exception as e:
            logger.error("Fatal agent error", error=str(e))
            # Try to show error in output before stopping
            try:
                error_type = type(e).__name__
                await self.output_router.default_output.write(
                    f"\n[Fatal Error] {error_type}: {e}\n"
                )
                await self.output_router.emit(OutputEvent(type="processing_end"))
            except Exception as inner:
                logger.warning(
                    "Failed to write fatal error to output",
                    error=str(inner),
                    exc_info=True,
                )
            raise

    @property
    def is_running(self) -> bool:
        """Check if agent is running."""
        return self._running

    @property
    def tools(self) -> list[str]:
        """Get list of registered tool names."""
        return self.registry.list_tools()

    @property
    def subagents(self) -> list[str]:
        """Get list of registered sub-agent names."""
        return self.subagent_manager.list_subagents()

    @property
    def conversation_history(self) -> list[dict]:
        """Get conversation history as list of message dicts."""
        return self.controller.conversation.to_messages()

    async def inject_input(
        self,
        content: str | list[ContentPart],
        source: str = "programmatic",
        pending_id: str | None = None,
        *,
        branch_view: dict[int, int] | None = None,
    ) -> bool:
        """Inject user input, optionally continuing a selected persisted branch.

        Returns True when the event ran; False when buffered for
        mid-turn drain — callers (WS attach) skip the post-call
        ``idle`` frame on False so KohakUwUing stays visible.

        ``pending_id`` lets a shell mint the queued-message id up front
        (UXI-08a) so it can target the message with ``edit_pending`` /
        ``cancel_pending`` the moment the queued ack lands. Ignored when
        the input runs immediately (it was never queued).
        ``branch_view`` reseats the in-memory conversation onto that
        persisted subtree before the new turn runs.
        """
        view = normalize_branch_view(branch_view)
        content = await self._prepare_injected_input(content, source)
        if content is None:
            # Slash-command or filter consumed the input — treat the
            # same as "processed" so the caller still gets its idle.
            return True
        event = create_user_input_event(content, source=source)
        if pending_id:
            event.context["pending_id"] = pending_id
        if view:
            return await continue_on_branch(self, event, view)
        return await self._process_event(event)

    async def inject_event(self, event: TriggerEvent) -> bool:
        """Inject a custom TriggerEvent programmatically.

        Same return contract as :meth:`inject_input`."""
        return await self._process_event(event)
