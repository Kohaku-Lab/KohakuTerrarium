"""Recorded compaction branch lineage and input provenance."""

from collections.abc import Mapping
from typing import Any


def compact_path_from_event(evt: Mapping[str, Any]) -> set[tuple[int, int]] | None:
    """Return the recorded branch lineage, or None for pathless legacy data."""
    raw = evt.get("compact_path")
    if not isinstance(raw, (list, tuple)):
        return None
    pairs: set[tuple[int, int]] = set()
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                pairs.add((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                continue
    return pairs or None


def compact_source_path_from_event(
    evt: Mapping[str, Any],
) -> set[tuple[int, int]] | None:
    """Return the recorded input lineage, or None when absent."""
    raw = evt.get("compact_source_path")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    pairs = set()
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        if any(type(value) is not int or value <= 0 for value in item):
            return None
        pairs.add(tuple(item))
    return pairs
