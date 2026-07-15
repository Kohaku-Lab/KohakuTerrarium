"""Phase B interactive bus methods for :class:`OutputRouter`.

Interactive replies are keyed by event ID and use first-reply-wins semantics.
"""

from __future__ import annotations

import asyncio
import time

from kohakuterrarium.modules.output.event import (
    ACTION_TIMEOUT,
    OutputEvent,
    UIReply,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class OutputRouterInteractiveMixin:
    """Add interactive event waiting and renderer reply submission to a router."""

    async def emit_and_wait(
        self, event: OutputEvent, timeout_s: float | None = None
    ) -> UIReply:
        """Emit an interactive event and await its first reply or timeout marker."""
        if not event.interactive:
            raise ValueError("emit_and_wait requires event.interactive=True")
        if not event.id:
            raise ValueError("emit_and_wait requires non-empty event.id")

        effective_timeout = timeout_s if timeout_s is not None else event.timeout_s

        loop = asyncio.get_event_loop()
        future: asyncio.Future[UIReply] = loop.create_future()
        self._pending_replies[event.id] = future

        try:
            await self.emit(event)
        except Exception:
            self._pending_replies.pop(event.id, None)
            raise

        try:
            if effective_timeout is None:
                reply = await future
            else:
                reply = await asyncio.wait_for(future, timeout=effective_timeout)
            return reply
        except asyncio.TimeoutError:
            return UIReply(
                event_id=event.id,
                action_id=ACTION_TIMEOUT,
                values={},
                timestamp=time.time(),
            )
        finally:
            # Releasing the slot makes late replies distinguishable from pending ones.
            self._pending_replies.pop(event.id, None)

    def submit_reply(self, reply: UIReply) -> bool:
        """Deliver a renderer reply and report whether it resolved a pending event."""
        return self.submit_reply_with_status(reply)[0]

    def submit_reply_with_status(self, reply: UIReply) -> tuple[bool, str]:
        """Deliver a reply with accepted, unknown, or superseded status."""
        future = self._pending_replies.pop(reply.event_id, None)
        if future is None:
            return (False, "unknown")
        if future.done():
            # Losing renderers need an advisory signal to dim stale UI.
            self._broadcast_supersede(reply.event_id)
            return (False, "superseded")
        future.set_result(reply)
        return (True, "accepted")

    def _broadcast_supersede(self, event_id: str) -> None:
        """Synchronously advise renderers that an interactive event is no longer pending."""
        targets = [self.default_output, *self._secondary_outputs]
        for target in targets:
            handler = getattr(target, "on_supersede", None)
            if handler is None:
                continue
            try:
                handler(event_id)
            except Exception as e:  # pragma: no cover — defensive
                logger.warning(
                    "on_supersede handler raised",
                    error=str(e),
                    exc_info=True,
                )
