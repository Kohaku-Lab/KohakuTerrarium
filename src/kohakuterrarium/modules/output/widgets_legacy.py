"""The six pre-widget UI kinds expressed as widgets.

``card``, ``ask_text``, ``confirm``, ``selection``, ``progress`` and
``notification`` are widgets with built-in renderers ``kt.<kind>``. The
reply-waiting contract of ``emit_and_wait`` (first reply wins, timeout
marker, close broadcast) lives here, on :class:`WidgetService`.
"""

import asyncio
import secrets
import time
from typing import Any

from kohakuterrarium.modules.output.event import ACTION_TIMEOUT, OutputEvent, UIReply
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

LEGACY_RENDERERS: dict[str, str] = {
    kind: f"kt.{kind}"
    for kind in ("card", "ask_text", "confirm", "selection", "progress", "notification")
}
LEGACY_KINDS: dict[str, str] = {
    renderer: kind for kind, renderer in LEGACY_RENDERERS.items()
}
LEGACY_EVENT_TYPES = frozenset(LEGACY_RENDERERS) | {"ui_supersede"}
MAX_CLOSED_INTERACTIONS = 1024


def legacy_fallback(kind: str, payload: dict[str, Any]) -> str:
    """One-line text for a legacy kind, for outputs without its renderer."""
    for key in ("title", "prompt", "text", "label", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return kind


def legacy_event_from_widget(event: OutputEvent) -> OutputEvent | None:
    """The legacy-typed event a widget-family event stands for, or ``None``.

    Widgets built from a legacy event carry it in ``metadata["legacy_event"]``;
    a legacy widget opened without one is rebuilt from its renderer and spec.
    """
    original = (event.metadata or {}).get("legacy_event")
    if isinstance(original, OutputEvent):
        return original
    if event.type != "widget" or event.update_target is not None:
        return None
    kind = LEGACY_KINDS.get(str((event.payload or {}).get("renderer") or ""))
    if kind is None:
        return None
    return OutputEvent(
        type=kind,
        id=event.id,
        surface=event.surface,
        interactive=event.interactive,
        timeout_s=event.timeout_s,
        payload=dict(event.payload.get("spec") or {}),
    )


async def render_widget_as_legacy(output: Any, event: OutputEvent) -> None:
    """``on_widget`` for outputs that draw only the legacy kinds.

    Legacy widgets go through the output's own ``emit`` with the original
    event; other renderers show their fallback text once, when they open.
    """
    legacy = legacy_event_from_widget(event)
    if legacy is not None:
        await output.emit(legacy)
    elif event.type == "widget_close":
        output.on_widget_close(event)
    elif (
        event.type == "widget"
        and event.update_target is None
        and event.payload.get("fallback")
    ):
        output.on_activity("widget", str(event.payload["fallback"]))


class LegacyWidgetsMixin:
    """Reply waiting and close broadcast for the legacy kinds on :class:`WidgetService`."""

    def _init_legacy(self) -> None:
        self.pending_replies: dict[str, asyncio.Future[UIReply]] = {}
        self.closed_interactions: dict[str, None] = {}

    async def emit_legacy(self, event: OutputEvent) -> None:
        """Record a legacy-kind event as a widget and deliver it to every output."""
        if event.type == "ui_supersede":
            target_id = event.payload.get("event_id") or event.id
            record = self.registry.get(target_id) if target_id else None
            if record is not None:
                record.closed = True
            widget = OutputEvent(
                type="widget_close",
                id=target_id,
                update_target=target_id,
                payload={"reason": "superseded"},
                metadata={"legacy_event": event},
            )
            await self._router.deliver_legacy(event, widget)
            return
        await self._router.deliver_legacy(event, self._legacy_widget(event))

    def _legacy_widget(self, event: OutputEvent) -> OutputEvent:
        """Register or patch the widget record behind one legacy event."""
        kind = event.type
        target = event.update_target
        record = self.registry.get(target) if target else None
        if record is not None and record.legacy:
            record.spec = {**(record.spec or {}), **dict(event.payload)}
            return OutputEvent(
                type="widget",
                id=target,
                update_target=target,
                surface=record.surface,
                payload={"spec": dict(record.spec)},
                metadata={"legacy_event": event},
            )
        widget_id = event.id or target or f"ev_{secrets.token_hex(6)}"
        spec = dict(event.payload)
        self.registry.add(
            self._record_type(
                widget_id=widget_id,
                renderer=LEGACY_RENDERERS[kind],
                surface=event.surface,
                routing="drop",
                interactive=bool(event.interactive),
                legacy=kind,
                spec=spec,
            )
        )
        return OutputEvent(
            type="widget",
            id=widget_id,
            surface=event.surface,
            interactive=bool(event.interactive),
            timeout_s=event.timeout_s,
            payload={
                "renderer": LEGACY_RENDERERS[kind],
                "version": 1,
                "title": "",
                "spec": spec,
                "state": {},
                "fallback": legacy_fallback(kind, spec),
                "routing": "drop",
            },
            metadata={"legacy_event": event},
        )

    async def wait_legacy(
        self, event: OutputEvent, timeout_s: float | None = None
    ) -> UIReply:
        """Emit an interactive event and await its first reply or the timeout marker."""
        if not event.interactive:
            raise ValueError("emit_and_wait requires event.interactive=True")
        if not event.id:
            raise ValueError("emit_and_wait requires non-empty event.id")
        effective_timeout = timeout_s if timeout_s is not None else event.timeout_s
        if event.id in self.pending_replies:
            raise ValueError(f"interactive event id already pending: {event.id}")

        future: asyncio.Future[UIReply] = asyncio.get_running_loop().create_future()
        self.closed_interactions.pop(event.id, None)
        self.pending_replies[event.id] = future
        try:
            await self._router.emit(event)
            if effective_timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=effective_timeout)
        except asyncio.TimeoutError:
            self.close_legacy(event.id, "timeout")
            return UIReply(
                event_id=event.id,
                action_id=ACTION_TIMEOUT,
                values={},
                timestamp=time.time(),
            )
        except BaseException:
            self.close_legacy(event.id, "superseded")
            raise
        finally:
            # Only this waiter may release its slot; identity protects against ID reuse.
            if self.pending_replies.get(event.id) is future:
                self.pending_replies.pop(event.id, None)

    def submit_legacy_reply(self, reply: UIReply) -> tuple[bool, str]:
        """Deliver a reply with ``accepted``, ``unknown`` or ``superseded`` status."""
        future = self.pending_replies.get(reply.event_id)
        if future is None:
            return (False, "unknown")
        if future.done():
            # Losing renderers need an advisory signal to dim stale UI.
            self.close_legacy(reply.event_id, "superseded")
            return (False, "superseded")
        future.set_result(reply)
        self.close_legacy(reply.event_id, "replied", action_id=reply.action_id)
        return (True, "accepted")

    def close_legacy(
        self, event_id: str, reason: str, *, action_id: str | None = None
    ) -> None:
        """Tell every output, once and synchronously, that an interaction is over.

        Outputs with ``on_widget_close`` get a ``widget_close`` event; the
        others get ``on_supersede(event_id)``.
        """
        if event_id in self.closed_interactions:
            return
        self.closed_interactions[event_id] = None
        while len(self.closed_interactions) > MAX_CLOSED_INTERACTIONS:
            self.closed_interactions.pop(next(iter(self.closed_interactions)))
        record = self.registry.get(event_id)
        if record is not None:
            record.closed = True
            for consumer in list(record.consumers):
                consumer.queue.put_nowait(None)
        payload: dict[str, Any] = {"reason": reason}
        if action_id:
            payload["action_id"] = action_id
        widget = OutputEvent(
            type="widget_close", id=event_id, update_target=event_id, payload=payload
        )
        for target in self._router._widget_targets():
            try:
                closer = getattr(target, "on_widget_close", None)
                if closer is not None:
                    closer(widget)
                    continue
                handler = getattr(target, "on_supersede", None)
                if handler is not None:
                    handler(event_id)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "interactive close handler raised",
                    error=str(e),
                    exc_info=True,
                )

    def _legacy_message(self, msg: Any) -> str:
        """A widget message to a legacy widget is that event's reply."""
        _accepted, status = self.submit_legacy_reply(
            UIReply(
                event_id=msg.widget_id,
                action_id=msg.name,
                values=dict(msg.data or {}),
                user=msg.user,
                timestamp=msg.ts or time.time(),
            )
        )
        return status
