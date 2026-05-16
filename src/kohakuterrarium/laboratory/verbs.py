"""L4 user verbs for the Laboratory layer.

The three delivery verbs from the canonical design are:

- :class:`Channel` — **Send** verb: point-to-point delivery to a named
  channel. Multiple subscribers load-balance one envelope each.
- :class:`Topic` — **Broadcast** verb: pub-sub fan-out to all subscribers.
- Replicate verb — deferred to a later release with the universal state
  system (design.md §6.4 / §7).

Both verbs sit on top of any object implementing the small :class:`LabNode`
duck interface — typically a :class:`ClientConnector` (when used from a
laboratory client) but trivially adaptable to a host-side node if/when
the host hosts local creatures.
"""

import asyncio
import itertools
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from kohakuterrarium.laboratory._internal.control import (
    build_subscribe,
    build_unsubscribe,
)
from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeKind,
)
from kohakuterrarium.laboratory._internal.protocol import HOST_NODE_ID
from kohakuterrarium.laboratory._internal.streams import (
    DEFAULT_ACK_TIMEOUT_SECONDS,
    StreamSender,
    build_ack_envelope,
)

# Process-wide counter so verbs running on the same node get distinct
# stream_ids. Per-instance ids would also work, but a process-wide
# counter avoids any chance of collision across coexisting verbs.
_STREAM_ID_COUNTER = itertools.count(1)


def _next_stream_id() -> int:
    return next(_STREAM_ID_COUNTER)


class AckTimeoutError(TimeoutError):
    """Raised by :meth:`Channel.send` with ``ack=True`` when no ack arrives."""


class LabNode(Protocol):
    """Minimal interface a Channel/Topic uses to talk to its host.

    Implemented by :class:`~kohakuterrarium.laboratory._internal.client.ClientConnector`
    today; future host-side adapters will implement the same surface.
    """

    @property
    def client_id(self) -> str | None:
        """The node's own NodeId, or ``None`` if not connected yet."""
        ...

    async def send(self, env: Envelope) -> None:
        """Enqueue an envelope for transmission."""
        ...

    def on_envelope(self, handler: Callable[[Envelope], Awaitable[None]]) -> None:
        """Register a handler called for every inbound envelope."""
        ...


class _BaseEndpoint:
    """Shared subscribe/recv/iter machinery for Channel and Topic."""

    def __init__(self, name: str, node: LabNode) -> None:
        self._name = name
        self._node = node
        self._stream_id = _next_stream_id()
        self._inbox: asyncio.Queue[Envelope] = asyncio.Queue()
        self._subscribed = False
        node.on_envelope(self._on_envelope)

    @property
    def name(self) -> str:
        return self._name

    async def subscribe(self) -> None:
        """Ask the host to register this node as a listener.

        Idempotent: calling twice is harmless (the host's directory
        deduplicates).
        """
        if self._subscribed:
            return
        sender = self._node.client_id or ""
        await self._node.send(
            build_subscribe(
                from_node=sender,
                to_node=HOST_NODE_ID,
                channel=self._name,
            )
        )
        self._subscribed = True

    async def unsubscribe(self) -> None:
        if not self._subscribed:
            return
        sender = self._node.client_id or ""
        await self._node.send(
            build_unsubscribe(
                from_node=sender,
                to_node=HOST_NODE_ID,
                channel=self._name,
            )
        )
        self._subscribed = False

    async def recv(self) -> bytes:
        """Block until one matching envelope arrives, return its payload."""
        env = await self._inbox.get()
        return env.payload

    async def messages(self):
        """Async iterator over incoming payloads. Implicitly subscribes."""
        await self.subscribe()
        while True:
            env = await self._inbox.get()
            yield env.payload

    def _on_envelope(self, env: Envelope) -> Awaitable[None]:
        # Subclasses override; provided here so async signature is
        # consistent for handler registration.
        return self._handle(env)

    async def _handle(self, env: Envelope) -> None:
        raise NotImplementedError


class Channel(_BaseEndpoint):
    """L4 **Send** verb — point-to-point with load-balanced delivery.

    When multiple nodes :meth:`subscribe` to the same channel, each
    :meth:`send` lands on exactly one of them (host-side round-robin).
    With ``ack=True``, the receiver auto-acks and ``send`` awaits the
    ack with a timeout.
    """

    def __init__(self, name: str, node: LabNode) -> None:
        super().__init__(name, node)
        self._stream_sender = StreamSender()
        self._pending_acks: dict[int, asyncio.Future[None]] = {}

    async def send(
        self,
        payload: bytes,
        *,
        ack: bool = False,
        timeout: float = DEFAULT_ACK_TIMEOUT_SECONDS,
    ) -> None:
        """Send one payload to the channel.

        Args:
            payload: Opaque bytes delivered to one subscribed listener.
            ack: If ``True``, the receiver acks and this call awaits it.
            timeout: Max seconds to wait for the ack; raises
                :class:`AckTimeoutError` on expiry. Ignored when ``ack``
                is ``False``.
        """
        sender = self._node.client_id or ""
        seq = self._stream_sender.assign_seq()
        env = Envelope(
            from_node=sender,
            to_node=f"channel://{self._name}",
            kind=EnvelopeKind.SEND,
            stream_id=self._stream_id,
            seq=seq,
            payload=payload,
            flags={"ack_required": True} if ack else {},
        )
        if ack:
            loop = asyncio.get_event_loop()
            fut: asyncio.Future[None] = loop.create_future()
            self._pending_acks[seq] = fut
            self._stream_sender.remember(env, time.monotonic())
        await self._node.send(env)
        if ack:
            try:
                await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise AckTimeoutError(
                    f"no ack for seq={seq} on channel {self._name!r}"
                    f" within {timeout}s"
                ) from exc
            finally:
                self._pending_acks.pop(seq, None)
                self._stream_sender.ack(seq)

    async def _handle(self, env: Envelope) -> None:
        match env.kind:
            case EnvelopeKind.ACK if env.stream_id == self._stream_id:
                fut = self._pending_acks.get(env.seq)
                if fut is not None and not fut.done():
                    fut.set_result(None)
                return
            case EnvelopeKind.SEND if env.to_node == f"channel://{self._name}":
                await self._receive_send(env)
            case _:
                return

    async def _receive_send(self, env: Envelope) -> None:
        if env.flags.get("ack_required"):
            ack_env = build_ack_envelope(
                from_node=self._node.client_id or "",
                to_node=env.from_node,
                stream_id=env.stream_id,
                seq=env.seq,
            )
            await self._node.send(ack_env)
        await self._inbox.put(env)


class Topic(_BaseEndpoint):
    """L4 **Broadcast** verb — pub-sub fan-out to every subscriber.

    Every node that :meth:`subscribe` s to the topic receives every
    :meth:`publish` (including the publisher, if it also subscribed).
    Best-effort delivery; no acks.
    """

    async def publish(self, payload: bytes) -> None:
        """Publish a payload to every subscriber of this topic."""
        sender = self._node.client_id or ""
        env = Envelope(
            from_node=sender,
            to_node=self._name,
            kind=EnvelopeKind.BROADCAST,
            stream_id=self._stream_id,
            seq=0,
            payload=payload,
        )
        await self._node.send(env)

    async def _handle(self, env: Envelope) -> None:
        match env.kind:
            case EnvelopeKind.BROADCAST if env.to_node == self._name:
                await self._inbox.put(env)
            case _:
                return


__all__ = [
    "AckTimeoutError",
    "Channel",
    "LabNode",
    "Topic",
]
