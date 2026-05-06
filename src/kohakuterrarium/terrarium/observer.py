"""
Non-destructive channel message observer.

Subscribes to broadcast channels as a silent observer and records
messages that flow through the API for queue channels.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from kohakuterrarium.core.channel import (
    AgentChannel,
    ChannelMessage,
    ChannelSubscription,
)
from kohakuterrarium.core.session import Session
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ObservedMessage:
    """A message observed on a channel."""

    channel: str
    sender: str
    content: str
    message_id: str
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelObserver:
    """
    Non-destructive observer for channel traffic.

    For AgentChannel (broadcast): subscribes as a silent observer and
    receives copies of every message in a background loop.

    For SubAgentChannel (queue): cannot peek non-destructively, so
    messages sent via the API's ``send_to_channel()`` are recorded
    through the ``record()`` method.

    Usage::

        observer = ChannelObserver(session)
        await observer.observe("team_chat")
        observer.on_message(lambda msg: print(f"[{msg.channel}] {msg.content}"))

        # Or collect messages later
        messages = observer.get_messages(channel="team_chat", last_n=10)
    """

    def __init__(self, session: Session, max_history: int = 1000) -> None:
        self._session = session
        self._messages: list[ObservedMessage] = []
        self._max_history = max_history
        self._callbacks: list[Callable[[ObservedMessage], None]] = []
        self._subscriptions: dict[str, ChannelSubscription] = {}
        self._observe_tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_message(self, callback: Callable[[ObservedMessage], None]) -> None:
        """Register a callback for observed messages."""
        self._callbacks.append(callback)

    async def observe(self, channel_name: str) -> None:
        """Start observing a channel.

        For broadcast channels: subscribes as a silent observer.
        For queue channels: only messages sent via the API are recorded
        (use ``record()`` from the API layer).
        """
        if channel_name in self._subscriptions:
            return  # already observing

        channel = self._session.channels.get(channel_name)
        if channel is None:
            logger.warning("Channel not found for observation", channel=channel_name)
            return

        if isinstance(channel, AgentChannel):
            sub_id = f"_observer_{channel_name}"
            sub = channel.subscribe(sub_id)
            self._subscriptions[channel_name] = sub
            task = asyncio.create_task(
                self._observe_loop(channel_name, sub),
                name=f"observer_{channel_name}",
            )
            self._observe_tasks.append(task)
            logger.debug("Observing broadcast channel", channel=channel_name)

    def record(self, channel_name: str, msg: ChannelMessage) -> None:
        """Record a message — called by Studio observer / API endpoints
        after a send completes, for any channel kind where
        non-destructive subscription isn't available.
        """
        observed = _to_observed(channel_name, msg)
        self._append(observed)

    def get_messages(
        self,
        channel: str | None = None,
        last_n: int = 20,
    ) -> list[ObservedMessage]:
        """Get observed messages, optionally filtered by channel."""
        msgs = self._messages
        if channel:
            msgs = [m for m in msgs if m.channel == channel]
        return msgs[-last_n:]

    async def stop(self) -> None:
        """Stop all observations and clean up subscriptions."""
        for task in self._observe_tasks:
            task.cancel()
        if self._observe_tasks:
            await asyncio.gather(*self._observe_tasks, return_exceptions=True)

        for name, sub in self._subscriptions.items():
            sub.unsubscribe()
            logger.debug("Observer unsubscribed", channel=name)

        self._subscriptions.clear()
        self._observe_tasks.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _observe_loop(
        self,
        channel_name: str,
        subscription: ChannelSubscription,
    ) -> None:
        """Background loop receiving from a broadcast subscription."""
        try:
            while True:
                try:
                    msg = await subscription.receive(timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                observed = _to_observed(channel_name, msg)
                self._append(observed)
        except asyncio.CancelledError:
            return

    def _append(self, observed: ObservedMessage) -> None:
        """Append a message and trim history, firing callbacks."""
        self._messages.append(observed)
        if len(self._messages) > self._max_history:
            self._messages = self._messages[-self._max_history :]
        for cb in self._callbacks:
            try:
                cb(observed)
            except Exception as e:
                logger.debug("Observer callback error", error=str(e), exc_info=True)


def _to_observed(channel_name: str, msg: ChannelMessage) -> ObservedMessage:
    """Convert a ChannelMessage to an ObservedMessage."""
    return ObservedMessage(
        channel=channel_name,
        sender=msg.sender,
        content=msg.content if isinstance(msg.content, str) else str(msg.content),
        message_id=msg.message_id,
        timestamp=msg.timestamp,
        metadata=msg.metadata,
    )
