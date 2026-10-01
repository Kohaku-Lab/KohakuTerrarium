"""Rebuildable, per-store event-id locators; never cache event payloads."""

import threading
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)
_KEYS_LIMIT = 2**31 - 1


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
                    # Preserve the first key in native lexical scan order,
                    # including imported duplicate ids and long sequence keys.
                    index[event_id] = min(index.get(event_id, encoded), encoded)

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
        complete = True
        for key in table.keys(prefix=f"{agent}:e", limit=_KEYS_LIMIT):
            try:
                event = table[key]
            except Exception as exc:
                logger.warning("Failed to read event", error=str(exc), exc_info=True)
                complete = False
                continue
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
