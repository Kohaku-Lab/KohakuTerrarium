"""Rebuildable, per-store event-id locators; never cache event payloads."""

import threading
from typing import Any

from kohakuterrarium.session.history.paging import physical_refs
from kohakuterrarium.session.vault.io import read_values
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _sequence(key: bytes) -> int | None:
    """KT's numeric physical sequence, if the legacy key has one."""
    suffix = key.rsplit(b":e", 1)[-1]
    return int(suffix) if suffix.isascii() and suffix.isdigit() else None


class EventKeyIndex:
    """Lazily index queried namespaces without changing the session format."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        self._agents: dict[str, dict[Any, bytes]] = {}

    def clear(self) -> None:
        """Invalidate derived locators after bulk copies or counter restoration."""
        with self._lock:
            self._generation += 1
            self._agents.clear()

    def record(self, key: str, event_id: int) -> None:
        """Update built indexes after a successful append, under a short lock."""
        encoded = key.encode("utf-8")
        with self._lock:
            self._generation += 1
            for agent, index in self._agents.items():
                if key.startswith(f"{agent}:e"):
                    # Reused logical IDs resolve to the first *physical*
                    # event, including across the six-digit key-width boundary.
                    previous = index.get(event_id)
                    if previous is None:
                        index[event_id] = encoded
                        continue
                    old_seq, new_seq = _sequence(previous), _sequence(encoded)
                    if (
                        old_seq is not None
                        and new_seq is not None
                        and new_seq < old_seq
                    ) or ((old_seq is None or new_seq is None) and encoded < previous):
                        # Preserve lexical behavior for malformed legacy keys.
                        index[event_id] = encoded

    def get(self, table: Any, agent: str, event_id: int) -> dict | None:
        """Read one current payload on a hit; scan once per cold namespace."""
        with self._lock:
            index = self._agents.get(agent)
            ready = index is not None
            key = index.get(event_id) if ready else None
        if ready:
            if key is None:
                return None
            try:
                event = table[key]
                if isinstance(event, dict) and event.get("event_id") == event_id:
                    return event
            except Exception as exc:
                logger.warning("Failed to read event", error=str(exc), exc_info=True)
            # A stale locator is a hint, never an authority over the source.
            self.clear()

        with self._lock:
            generation = self._generation
        built = {}
        found = None
        keys = physical_refs(table, agent, "e", preserve_noncanonical=True)
        rows = list(read_values(table, keys, what="event"))
        complete = len(rows) == len(keys)
        for key, event in rows:
            if not isinstance(event, dict):
                continue
            eid = event.get("event_id")
            if found is None and eid == event_id:
                found = event
            try:
                built.setdefault(
                    eid, key.encode("utf-8") if isinstance(key, str) else key
                )
            except TypeError:
                # Malformed legacy ids cannot match the integer lookup contract.
                continue
        with self._lock:
            # Disk work never holds the lock. If an append overlapped the scan,
            # return its observed result but do not publish an incomplete index.
            if complete and generation == self._generation:
                self._agents[agent] = built
        return found
