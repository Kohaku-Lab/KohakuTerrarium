"""Private event-value iteration within a store-owned read operation (blocking)."""

from collections.abc import Iterator, Sequence
from typing import Any

from kohakuterrarium.session.vault.io import read_values


def _iter_event_rows(
    table: Any,
    keys: Sequence[Any],
    since_event_id: int | None = None,
    *,
    strict: bool = False,
) -> Iterator[tuple[Any, dict]]:
    """Read physical event rows, optionally refusing incomplete reads."""
    count = 0
    for key, evt in read_values(table, keys, what="event", strict=strict):
        count += 1
        if strict and not isinstance(evt, dict):
            raise ValueError("Non-event payload in event table")
        if since_event_id is not None:
            eid = evt.get("event_id") if isinstance(evt, dict) else None
            if not isinstance(eid, int) or eid <= since_event_id:
                continue
        yield key, evt
    if strict and count != len(keys):
        raise ValueError("Incomplete physical event read")
