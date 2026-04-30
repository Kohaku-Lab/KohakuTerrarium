"""Phase B interactive bus methods for :class:`OutputRouter`.

Lives in a mixin so the main ``router.py`` stays focused on the Phase
A routing state machine + lifecycle. The mixin requires the host
class to expose:

- ``self._pending_replies: dict[str, asyncio.Future[UIReply]]``
- ``self.default_output`` and ``self._secondary_outputs``
- ``self.emit(event)`` (the bus entry point)

The host class (``OutputRouter``) initialises ``_pending_replies`` in
``__init__``; the mixin only manipulates it.

Methods provided:

- :meth:`emit_and_wait` — emit an interactive event and await reply.
- :meth:`submit_reply` — deliver a reply to a pending Future.
- :meth:`submit_reply_with_status` — like ``submit_reply`` but returns
  a distinct status code (``accepted`` / ``unknown`` / ``superseded``).
- :meth:`_broadcast_supersede` — sync fan-out to ``on_supersede`` hooks
  on attached outputs.
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
    """Phase B interactive bus methods for :class:`OutputRouter`.

    Provides ``emit_and_wait`` (producer-side) and ``submit_reply`` /
    ``submit_reply_with_status`` (renderer-side). Pending Futures are
    keyed by ``event.id`` in ``self._pending_replies``.
    """

    async def emit_and_wait(
        self, event: OutputEvent, timeout_s: float | None = None
    ) -> UIReply:
        """Emit an interactive event and await a :class:`UIReply`.

        Requires ``event.interactive=True`` and ``event.id`` non-empty.
        The bus registers a Future in the pending-reply map keyed by
        ``event.id``, fans the event to renderers via :meth:`emit`,
        and awaits the first reply (first-reply-wins across multiple
        attached frontends).

        Default behaviour: **wait forever** for a reply. The agent is
        often idle while the human is away from the keyboard — letting
        the await sit indefinitely matches the natural UX. Producers
        can opt into a timeout per-event (``event.timeout_s``) or
        per-call (``timeout_s`` arg) when bounded waits matter
        (e.g. budget-sensitive workflows).

        Behaviour:
        - With timeout: on expiry, returns
          ``UIReply(event_id, action_id="__timeout__", values={})``;
          no exception is raised. Producers always handle the same
          return shape.
        - Without timeout: blocks until a reply lands or the agent
          shuts down (in which case the surrounding cancellation
          unwinds the await).
        - If a different renderer beats this caller (race), the
          winning reply is returned to the awaiter; the late
          renderer's ``submit_reply`` returns ``False`` and the
          renderer is told to dim its widget via the supersede
          broadcast.

        Args:
            event: The OutputEvent to emit. Must have ``interactive=True``
                   and a non-empty ``id``.
            timeout_s: Override for ``event.timeout_s``. ``None`` (the
                       default) means wait forever.
        """
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
            # Always release the slot; if the timeout path fires
            # while a late reply is in flight, ``submit_reply`` will
            # see a missing entry and tell the renderer it was
            # superseded.
            self._pending_replies.pop(event.id, None)

    def submit_reply(self, reply: UIReply) -> bool:
        """Renderer entry point — deliver a :class:`UIReply` to the bus.

        Returns ``True`` when the reply resolved a pending Future,
        ``False`` for any other case (unknown event id, already
        replied, etc.). Use :meth:`submit_reply_with_status` when you
        need to distinguish those cases.
        """
        return self.submit_reply_with_status(reply)[0]

    def submit_reply_with_status(self, reply: UIReply) -> tuple[bool, str]:
        """Like :meth:`submit_reply` but returns ``(accepted, status)``.

        ``status`` is one of:

        - ``"accepted"`` — reply resolved the pending Future.
        - ``"unknown"`` — no event with that id was waiting (the
          producer either timed out, never emitted, or this is a
          stale frame from a reconnect). No supersede broadcast.
        - ``"superseded"`` — a different renderer already replied to
          this event. The frontend that submitted should dim its
          widget. (Phase B v1 reaches this branch only on a true race
          between concurrent submitters.)

        On the success path we do **not** broadcast a supersede event
        back through the renderer fan-out: the WS framing of supersede
        is delivered to other attached renderers via their own ack
        path when they later try to submit a reply for the same event
        and get rejected. This keeps the winning renderer from seeing
        a stray ``ui_supersede`` for an event it just replied to.
        """
        future = self._pending_replies.pop(reply.event_id, None)
        if future is None:
            return (False, "unknown")
        if future.done():
            # Race already won by another renderer; tell our local
            # supersede hook so this caller's widget can dim.
            self._broadcast_supersede(reply.event_id)
            return (False, "superseded")
        future.set_result(reply)
        return (True, "accepted")

    def _broadcast_supersede(self, event_id: str) -> None:
        """Notify renderers that ``event_id`` is no longer awaiting a
        reply. Used both when a different renderer claimed the reply
        and when a late reply arrives after timeout/cancel.

        Renderers that care expose a synchronous ``on_supersede(event_id)``
        hook. The broadcast is fully synchronous: ``submit_reply`` is
        called from sync renderer paths, and we don't want to require
        a running event loop here. Renderers that need an async path
        (e.g. WS streamers) buffer to their own queue inside their
        sync hook and drain on the loop side — see
        :class:`StreamOutput.on_supersede`.

        Renderers without ``on_supersede`` (e.g. ``StdoutOutput``)
        simply skip the notification — it's purely advisory for UIs
        that want to dim a previously-shown widget.
        """
        targets = [self.default_output, *self._secondary_outputs]
        for target in targets:
            handler = getattr(target, "on_supersede", None)
            if handler is None:
                continue
            try:
                handler(event_id)
            except Exception as e:  # pragma: no cover — defensive
                logger.debug(
                    "on_supersede handler raised",
                    error=str(e),
                    exc_info=True,
                )
