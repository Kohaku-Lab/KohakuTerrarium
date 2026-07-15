"""Map creature references and channel listeners to Laboratory nodes."""


class AddressDirectory:
    """Maintain host-loop routing state for creatures and channel listeners."""

    def __init__(self) -> None:
        self._creatures: dict[str, str] = {}
        self._listeners: dict[str, set[str]] = {}
        self._round_robin: dict[str, int] = {}

    def register_creature(self, ref: str, node_id: str) -> None:
        """Bind a creature ref to its hosting node. Overwrites any prior."""
        self._creatures[ref] = node_id

    def unregister_creature(self, ref: str) -> bool:
        """Remove a creature ref. Returns whether one was actually present."""
        return self._creatures.pop(ref, None) is not None

    def resolve_creature(self, ref: str) -> str | None:
        """Return the node hosting ``ref``, or ``None`` if unknown."""
        return self._creatures.get(ref)

    def known_creatures(self) -> dict[str, str]:
        """Snapshot of all (ref → node_id) registrations."""
        return dict(self._creatures)

    def register_listener(self, channel: str, node_id: str) -> bool:
        """Add a listener to a channel. Returns whether it was new for this channel."""
        existing = self._listeners.setdefault(channel, set())
        if node_id in existing:
            return False
        existing.add(node_id)
        return True

    def unregister_listener(self, channel: str, node_id: str) -> bool:
        """Remove a single listener. Returns whether it was actually present."""
        existing = self._listeners.get(channel)
        if not existing or node_id not in existing:
            return False
        existing.discard(node_id)
        if not existing:
            del self._listeners[channel]
            self._round_robin.pop(channel, None)
        return True

    def listeners(self, channel: str) -> set[str]:
        """Return a copy of the listener set for ``channel`` (empty if none)."""
        existing = self._listeners.get(channel)
        return set(existing) if existing else set()

    def known_channels(self) -> dict[str, set[str]]:
        """Snapshot of all (channel → listener-set) registrations."""
        return {name: set(nodes) for name, nodes in self._listeners.items()}

    def pick_listener(self, channel: str) -> str | None:
        """Round-robin pick of one listener for a load-balanced SEND.

        Returns ``None`` if no listener is registered.
        """
        existing = self._listeners.get(channel)
        if not existing:
            return None
        ordered = sorted(existing)
        idx = self._round_robin.get(channel, 0) % len(ordered)
        picked = ordered[idx]
        self._round_robin[channel] = idx + 1
        return picked

    def evict_node(self, node_id: str) -> tuple[int, int]:
        """Remove a departed node's creatures and listener registrations."""
        creatures_removed = 0
        for ref, host in list(self._creatures.items()):
            if host == node_id:
                del self._creatures[ref]
                creatures_removed += 1

        listener_entries_removed = 0
        for channel in list(self._listeners.keys()):
            listeners = self._listeners[channel]
            if node_id in listeners:
                listeners.discard(node_id)
                listener_entries_removed += 1
            if not listeners:
                del self._listeners[channel]
                self._round_robin.pop(channel, None)
        return creatures_removed, listener_entries_removed


__all__ = ["AddressDirectory"]
