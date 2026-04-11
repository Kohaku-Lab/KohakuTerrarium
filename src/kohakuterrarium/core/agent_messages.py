"""Message edit / regenerate / rewind mixin for Agent.

Core feature: modify past messages and re-run the turn. Works from
TUI, frontend, and programmatic API — all three call the same
implementation.
"""

from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class AgentMessagesMixin:
    """Message edit / regenerate / rewind operations."""

    async def regenerate_last_response(self) -> None:
        """Pop the last assistant message (+tool results) and re-run LLM.

        Uses current model/settings — which may differ from when the
        original response was generated.
        """
        conv = self.controller.conversation
        last_user = conv.find_last_user_index()
        if last_user < 0:
            logger.warning("No user message to regenerate from")
            return
        removed = conv.truncate_from(last_user + 1)
        logger.info("Regenerating", dropped=len(removed))
        await self._rerun_from_last()

    async def edit_and_rerun(self, message_idx: int, new_content: str) -> None:
        """Replace a user message at ``message_idx`` and re-run from there."""
        conv = self.controller.conversation
        msgs = conv.get_messages()
        if message_idx < 0 or message_idx >= len(msgs):
            logger.warning("Invalid edit index", index=message_idx)
            return
        target = msgs[message_idx]
        if target.role != "user":
            logger.warning("Can only edit user messages", role=target.role)
            return
        conv.truncate_from(message_idx)
        conv.append("user", new_content)
        logger.info("Edited and re-running", index=message_idx)
        await self._rerun_from_last()

    async def rewind_to(self, message_idx: int) -> None:
        """Drop messages from ``message_idx`` onward without re-running."""
        conv = self.controller.conversation
        removed = conv.truncate_from(message_idx)
        logger.info("Rewound", index=message_idx, dropped=len(removed))
        if self.session_store:
            try:
                self.session_store.save_conversation(
                    self.config.name, conv.to_messages()
                )
            except Exception as e:
                logger.debug(
                    "Failed to save conversation after rewind",
                    error=str(e),
                    exc_info=True,
                )

    async def _rerun_from_last(self) -> None:
        """Trigger a new LLM turn from the current conversation state."""
        event = TriggerEvent(
            type=EventType.USER_INPUT,
            content="",
            context={"rerun": True},
            stackable=False,
        )
        await self._process_event(event)
