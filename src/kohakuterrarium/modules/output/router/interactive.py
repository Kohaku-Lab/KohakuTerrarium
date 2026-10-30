"""Interactive bus methods for :class:`OutputRouter`.

Interactive replies are keyed by event ID and use first-reply-wins semantics.
The implementation lives on the router's :class:`WidgetService`; these are the
stable router-level entry points.
"""

from kohakuterrarium.modules.output.event import OutputEvent, UIReply


class OutputRouterInteractiveMixin:
    """Add interactive event waiting and renderer reply submission to a router."""

    async def emit_and_wait(
        self, event: OutputEvent, timeout_s: float | None = None
    ) -> UIReply:
        """Emit an interactive event and await its first reply or timeout marker."""
        return await self.widgets.wait_legacy(event, timeout_s)

    def submit_reply(self, reply: UIReply) -> bool:
        """Deliver a renderer reply and report whether it resolved a pending event."""
        return self.submit_reply_with_status(reply)[0]

    def submit_reply_with_status(self, reply: UIReply) -> tuple[bool, str]:
        """Deliver a reply with accepted, unknown, or superseded status."""
        return self.widgets.submit_legacy_reply(reply)

    def _broadcast_interactive_closed(self, event_id: str) -> None:
        """Synchronously advise renderers once that an interaction is no longer pending."""
        self.widgets.close_legacy(event_id, "superseded")
