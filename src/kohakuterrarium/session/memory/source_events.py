"""Memory-only binding from physical Store keys to indexed event facts.

The binding is read from the same store, not inferred from logical event IDs
or the ordinal inside a selected-branch index. Unverifiable histories fall
back to ordinary search without granting unproven source anchors.
"""

from kohakuterrarium.session.vault.io import key_text, read_values, scan_keys
from kohakuterrarium.session.memory.navigation import _certify


class SourcedEvent(dict):
    """A Store event with an ephemeral verified physical key."""

    def __init__(self, data: dict, source_ref: str):
        super().__init__(data)
        self.source_ref = source_ref


def sourced_events(store, agent: str, *, strict: bool = False) -> list[dict]:
    """Bind physical rows, with Search-only tolerant fallback by default.

    A destructive index rebuild must use strict=True: a decoding failure or
    missing physical row cannot be mistaken for an empty Agent history.
    """
    store._flush_events_cache()
    prefix = f"{agent}:e"
    keys = [
        key
        for key in scan_keys(store.events, prefix)
        if key_text(key).startswith(prefix)
    ]
    try:
        rows = list(read_values(store.events, keys, what="memory source", strict=True))
        if len(rows) != len(keys):
            raise ValueError("unreadable physical source row")
        events = [row for _, row in rows]
        if any(not isinstance(event, dict) for event in events):
            raise ValueError("non-event payload in event table")
        _certify(events)
        sourced = []
        for key, event in rows:
            ref = key_text(key)
            suffix = ref[len(prefix) :] if ref.startswith(prefix) else ""
            if (
                not suffix.isascii()
                or not suffix.isdecimal()
                or f"{int(suffix):06d}" != suffix
            ):
                raise ValueError("noncanonical physical event key")
            sourced.append(SourcedEvent(event, ref))
        return sourced
    except Exception:
        if strict:
            raise
        # Ordinary Search remains available for malformed older history, but
        # must not issue Anchors without physical source certification.
        return store.get_events(agent)
