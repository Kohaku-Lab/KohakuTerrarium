"""Private event-value iteration within a store-owned read operation (blocking)."""

from collections.abc import Iterator, Sequence
from typing import Any

from kohakuterrarium.session.vault_io import read_values


def _iter_event_values(
    table: Any, keys: Sequence[Any], since_event_id: int | None = None
) -> Iterator[dict]:
    """Read supplied physical keys, logging and skipping unreadable values."""
    for _, evt in read_values(table, keys, what="event"):
        if since_event_id is not None:
            eid = evt.get("event_id") if isinstance(evt, dict) else None
            if not isinstance(eid, int) or eid <= since_event_id:
                continue
        yield evt
