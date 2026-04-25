"""
Named async channel system for cross-component communication.

Provides queue-based and broadcast channel types for decoupled communication
between agents, tools, and other framework components.

Channel types:
- SubAgentChannel (queue): Point-to-point, one consumer receives each message
- AgentChannel (broadcast): All subscribers receive every message
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def generate_message_id() -> str:
    """Generate a unique message ID."""
    return f"msg_{uuid4().hex[:12]}"


@dataclass
class ChannelMessage:
    """A message sent through a channel."""

    sender: str
    content: str | dict | list[dict]
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    message_id: str = field(default_factory=generate_message_id)
    reply_to: str | None = None
    channel: str | None = None


class BaseChannel(ABC):
    """Base interface for all channel types."""

    def __init__(self, name: str, description: str = "", max_history: int = 200):
        self.name = name
        self.description = description
        self.history: list[ChannelMessage] = []
        self._max_history = max_history
        self._on_send_callbacks: list[Any] = []

    def on_send(self, callback: Any) -> None:
        """Register a callback fired on every send (before queue consumption).

        Callback signature: callback(channel_name: str, message: ChannelMessage)
        Works for both queue and broadcast channels.
        """
        self._on_send_callbacks.append(callback)

    def remove_on_send(self, callback: Any) -> None:
        """Remove a send callback."""
        self._on_send_callbacks = [
            cb for cb in self._on_send_callbacks if cb is not callback
        ]

    def _fire_on_send(self, message: ChannelMessage) -> None:
        """Fire all send callbacks."""
        for cb in self._on_send_callbacks:
            try:
                cb(self.name, message)
            except Exception as e:
                logger.debug("on_send callback error", error=str(e), exc_info=True)

    @abstractmethod
    async def send(self, message: ChannelMessage) -> None: ...

    @property
    @abstractmethod
    def channel_type(self) -> str: ...

    @property
    @abstractmethod
    def empty(self) -> bool: ...

    @property
    @abstractmethod
    def qsize(self) -> int: ...


class SubAgentChannel(BaseChannel):
    """Named async queue channel for point-to-point communication.

    Each message is consumed by exactly one receiver. Suitable for
    sub-agent communication where one consumer processes each message.
    """

    def __init__(self, name: str, maxsize: int = 0, description: str = ""):
        super().__init__(name, description=description)
        self._queue: asyncio.Queue[ChannelMessage] = asyncio.Queue(maxsize=maxsize)

    @property
    def channel_type(self) -> str:
        return "queue"

    async def send(self, message: ChannelMessage) -> None:
        """Send a message to the channel.

        Emits a Wave B ``channel_backpressure`` callback (via
        :meth:`on_send` subscribers) if the bounded queue is full at
        call time — tells the session store the producer blocked.
        """
        message.channel = self.name
        self.history.append(message)
        if len(self.history) > self._max_history:
            self.history = self.history[-self._max_history :]
        self._fire_on_send(message)
        # Bounded queue back-pressure — observability only.
        if self._queue.maxsize and self._queue.full():
            wait_start = asyncio.get_event_loop().time()
            await self._queue.put(message)
            wait_ms = (asyncio.get_event_loop().time() - wait_start) * 1000.0
            logger.info(
                "channel_backpressure",
                channel=self.name,
                wait_ms=round(wait_ms, 2),
                sender=message.sender,
            )
        else:
            await self._queue.put(message)
        logger.debug(
            "Message sent on channel '%s' from '%s'",
            self.name,
            message.sender,
        )

    async def receive(self, timeout: float | None = None) -> ChannelMessage:
        """Receive a message from the channel. Blocks until available.

        Args:
            timeout: Maximum seconds to wait. None means wait indefinitely.

        Returns:
            The next ChannelMessage from the channel.

        Raises:
            asyncio.TimeoutError: If timeout is exceeded before a message arrives.
        """
        if timeout is not None:
            message = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        else:
            message = await self._queue.get()
        logger.debug(
            "Message received on channel '%s' from '%s'",
            self.name,
            message.sender,
        )
        return message

    def try_receive(self) -> ChannelMessage | None:
        """Non-blocking receive. Returns None if the channel is empty."""
        try:
            message = self._queue.get_nowait()
            logger.debug(
                "Message received (non-blocking) on channel '%s' from '%s'",
                self.name,
                message.sender,
            )
            return message
        except asyncio.QueueEmpty:
            return None

    @property
    def empty(self) -> bool:
        """Whether the channel has no pending messages."""
        return self._queue.empty()

    @property
    def qsize(self) -> int:
        """Approximate number of messages in the channel."""
        return self._queue.qsize()


class ChannelSubscription:
    """A subscriber's view of a broadcast channel."""

    def __init__(
        self,
        channel: "AgentChannel",
        subscriber_id: str,
        queue: asyncio.Queue[ChannelMessage],
    ):
        self._channel = channel
        self.subscriber_id = subscriber_id
        self._queue = queue

    async def receive(self, timeout: float | None = None) -> ChannelMessage:
        """Receive the next message. Blocks until available.

        Args:
            timeout: Maximum seconds to wait. None means wait indefinitely.

        Returns:
            The next ChannelMessage delivered to this subscriber.

        Raises:
            asyncio.TimeoutError: If timeout is exceeded before a message arrives.
        """
        if timeout is not None:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        return await self._queue.get()

    def try_receive(self) -> ChannelMessage | None:
        """Non-blocking receive. Returns None if no message is available."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def unsubscribe(self) -> None:
        """Remove this subscription from the broadcast channel."""
        self._channel.unsubscribe(self.subscriber_id)

    @property
    def empty(self) -> bool:
        """Whether this subscriber has no pending messages."""
        return self._queue.empty()

    @property
    def qsize(self) -> int:
        """Approximate number of pending messages for this subscriber."""
        return self._queue.qsize()


class AgentChannel(BaseChannel):
    """Broadcast channel - all subscribers receive every message.

    Suitable for scenarios where multiple agents or components need to
    observe the same stream of messages (e.g., status updates, events).
    """

    def __init__(self, name: str, description: str = ""):
        super().__init__(name, description=description)
        self._subscribers: dict[str, asyncio.Queue[ChannelMessage]] = {}

    @property
    def channel_type(self) -> str:
        return "broadcast"

    async def send(self, message: ChannelMessage) -> None:
        """Broadcast a message to all subscribers except the sender."""
        message.channel = self.name
        self.history.append(message)
        if len(self.history) > self._max_history:
            self.history = self.history[-self._max_history :]
        self._fire_on_send(message)
        delivered = 0
        for sub_id, queue in self._subscribers.items():
            if sub_id == message.sender:
                continue  # Don't echo back to sender
            await queue.put(message)
            delivered += 1
        logger.debug(
            "Broadcast on '%s' to %d/%d subscribers from '%s'",
            self.name,
            delivered,
            len(self._subscribers),
            message.sender,
        )

    def subscribe(self, subscriber_id: str) -> ChannelSubscription:
        """Subscribe to this channel. Returns existing subscription if already subscribed.

        Args:
            subscriber_id: Unique identifier for the subscriber.

        Returns:
            A ChannelSubscription for receiving messages.
        """
        if subscriber_id in self._subscribers:
            return ChannelSubscription(
                self, subscriber_id, self._subscribers[subscriber_id]
            )
        queue: asyncio.Queue[ChannelMessage] = asyncio.Queue()
        self._subscribers[subscriber_id] = queue
        logger.debug("Subscriber '%s' joined channel '%s'", subscriber_id, self.name)
        return ChannelSubscription(self, subscriber_id, queue)

    def unsubscribe(self, subscriber_id: str) -> None:
        """Remove a subscriber from this channel.

        Args:
            subscriber_id: The subscriber to remove.
        """
        if self._subscribers.pop(subscriber_id, None) is not None:
            logger.debug("Subscriber '%s' left channel '%s'", subscriber_id, self.name)

    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._subscribers)

    @property
    def empty(self) -> bool:
        """Whether all subscriber queues are empty."""
        return all(q.empty() for q in self._subscribers.values())

    @property
    def qsize(self) -> int:
        """Total pending messages across all subscriber queues."""
        return sum(q.qsize() for q in self._subscribers.values())


class ChannelRegistry:
    """Registry of named channels."""

    def __init__(self) -> None:
        self._channels: dict[str, BaseChannel] = {}

    def get_or_create(
        self,
        name: str,
        channel_type: str = "queue",
        maxsize: int = 0,
        description: str = "",
    ) -> BaseChannel:
        """Get an existing channel or create a new one.

        Args:
            name: The channel name.
            channel_type: Type of channel to create: "queue" or "broadcast".
                          Ignored if the channel already exists.
            maxsize: Maximum queue size for a newly created queue channel.
                     Ignored if the channel already exists or type is broadcast.
            description: Human-readable description of the channel's purpose.

        Returns:
            The existing or newly created channel.
        """
        if name not in self._channels:
            match channel_type:
                case "broadcast":
                    self._channels[name] = AgentChannel(name, description=description)
                case _:
                    self._channels[name] = SubAgentChannel(
                        name, maxsize=maxsize, description=description
                    )
            logger.debug("Created %s channel '%s'", channel_type, name)
        return self._channels[name]

    def get_channel_info(self) -> list[dict[str, str]]:
        """Get info about all registered channels for prompt injection.

        Returns:
            List of dicts with name, type, description for each channel.
        """
        return [
            {
                "name": ch.name,
                "type": ch.channel_type,
                "description": ch.description,
            }
            for ch in self._channels.values()
        ]

    def get(self, name: str) -> BaseChannel | None:
        """Get a channel by name, or None if it does not exist."""
        return self._channels.get(name)

    def list_channels(self) -> list[str]:
        """List all registered channel names."""
        return list(self._channels.keys())

    def remove(self, name: str) -> bool:
        """Remove a channel from the registry.

        Args:
            name: The channel name to remove.

        Returns:
            True if the channel existed and was removed, False otherwise.
        """
        if name in self._channels:
            del self._channels[name]
            logger.debug("Removed channel '%s'", name)
            return True
        return False


# Backward compatibility alias
Channel = SubAgentChannel


# get_channel_registry() has moved to kohakuterrarium.core.session to avoid
# a circular import (session.py imports ChannelRegistry from this module).
# Callers should import from kohakuterrarium.core.session directly.
