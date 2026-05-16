"""L3 membership — track which nodes are alive and what they can do.

Heartbeat-based liveness:

- Nodes call :meth:`Membership.join` on connect; the host registers them.
- Each side sends periodic heartbeats. :meth:`Membership.heartbeat`
  refreshes the ``last_heartbeat`` timestamp.
- The host periodically calls :meth:`Membership.check_lost`; any node
  whose last heartbeat is older than ``heartbeat_timeout_seconds``
  is removed and a :attr:`MembershipEvent.LOST` event is emitted.
- :meth:`Membership.leave` removes a node cleanly (e.g. on graceful
  disconnect) and emits a :attr:`MembershipEvent.LEFT` event.

All time is supplied by callers (``now`` parameter, monotonic seconds).
This keeps the module synchronous and trivially testable; the host
engine drives the ticking.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum


class MembershipEvent(str, Enum):
    """Lifecycle transitions emitted by :class:`Membership`."""

    JOINED = "joined"
    LEFT = "left"
    LOST = "lost"


@dataclass(frozen=True)
class NodeInfo:
    """Snapshot of a known node's state.

    Attributes:
        node_id: Cluster-unique identifier.
        capabilities: Strings advertised by the node at join time
            (e.g. ``("gpu", "cuda")``).
        last_heartbeat: Monotonic timestamp of the most recent heartbeat
            (or join, which counts as a heartbeat).
    """

    node_id: str
    capabilities: tuple[str, ...]
    last_heartbeat: float


class Membership:
    """Registry of alive nodes with heartbeat-based liveness.

    Thread-affinity: not thread-safe. Drive from a single asyncio loop.
    """

    def __init__(self, heartbeat_timeout_seconds: float = 15.0) -> None:
        self._nodes: dict[str, NodeInfo] = {}
        self._subscribers: list[asyncio.Queue[tuple[MembershipEvent, str] | None]] = []
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds

    # ------------------------------------------------------------------
    # Mutating operations
    # ------------------------------------------------------------------

    def join(self, node_id: str, capabilities, now: float) -> bool:
        """Register a node as joined or refresh its capabilities.

        Returns ``True`` if this was a *new* node (JOINED event emitted),
        ``False`` if it was already known (capabilities + heartbeat
        refreshed without an event).
        """
        is_new = node_id not in self._nodes
        self._nodes[node_id] = NodeInfo(
            node_id=node_id,
            capabilities=tuple(capabilities),
            last_heartbeat=now,
        )
        if is_new:
            self._emit(MembershipEvent.JOINED, node_id)
        return is_new

    def heartbeat(self, node_id: str, now: float) -> bool:
        """Refresh ``last_heartbeat`` for an existing node.

        Returns ``True`` if the node was known and the heartbeat was
        recorded, ``False`` if the node is unknown (no event emitted).
        """
        existing = self._nodes.get(node_id)
        if existing is None:
            return False
        self._nodes[node_id] = NodeInfo(
            node_id=existing.node_id,
            capabilities=existing.capabilities,
            last_heartbeat=now,
        )
        return True

    def leave(self, node_id: str) -> bool:
        """Remove a node explicitly (graceful disconnect).

        Emits :attr:`MembershipEvent.LEFT`. Returns ``True`` if the node
        was actually present.
        """
        if node_id not in self._nodes:
            return False
        del self._nodes[node_id]
        self._emit(MembershipEvent.LEFT, node_id)
        return True

    def check_lost(self, now: float) -> list[str]:
        """Scan for nodes whose last heartbeat exceeds the timeout.

        Removes each stale node and emits a :attr:`MembershipEvent.LOST`
        event. Returns the list of removed node ids.
        """
        timeout = self.heartbeat_timeout_seconds
        lost: list[str] = []
        # Materialize the list first since we're going to mutate the dict.
        for node_id, info in list(self._nodes.items()):
            if now - info.last_heartbeat > timeout:
                del self._nodes[node_id]
                self._emit(MembershipEvent.LOST, node_id)
                lost.append(node_id)
        return lost

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def alive(self) -> set[str]:
        """Set of currently-known node ids."""
        return set(self._nodes.keys())

    def capabilities(self, node_id: str) -> tuple[str, ...] | None:
        """Capabilities advertised by a node, or ``None`` if unknown."""
        info = self._nodes.get(node_id)
        return info.capabilities if info else None

    def info(self, node_id: str) -> NodeInfo | None:
        """Full :class:`NodeInfo` snapshot, or ``None`` if unknown."""
        return self._nodes.get(node_id)

    def snapshot(self) -> dict[str, NodeInfo]:
        """Copy of the full node table (shallow)."""
        return dict(self._nodes)

    # ------------------------------------------------------------------
    # Event subscription
    # ------------------------------------------------------------------

    def subscribe(self) -> AsyncIterator[tuple[MembershipEvent, str]]:
        """Async iterator yielding membership events as they happen.

        The iterator stays alive until :meth:`close_subscribers` is
        called or the subscriber stops consuming. Multiple subscribers
        are supported; each gets its own queue.
        """
        queue: asyncio.Queue[tuple[MembershipEvent, str] | None] = asyncio.Queue()
        self._subscribers.append(queue)

        async def _gen():
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        return
                    yield event
            finally:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)

        return _gen()

    def close_subscribers(self) -> None:
        """Signal all current subscribers to stop iterating."""
        for queue in list(self._subscribers):
            queue.put_nowait(None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit(self, event: MembershipEvent, node_id: str) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait((event, node_id))


__all__ = [
    "Membership",
    "MembershipEvent",
    "NodeInfo",
]
