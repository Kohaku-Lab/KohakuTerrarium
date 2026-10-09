"""``meta["summary"]`` = {text, source, at_turn, updated_at} of a session store.

``source`` is ``heuristic`` / ``compaction`` / ``llm`` / ``user``. A ``user``
summary is never replaced automatically, and an automatic summary is never
replaced by a weaker fallback of a different source.
"""

import time
from typing import Any

from kohakuterrarium.studio.identity.session_summary import (
    COMPACTION,
    HEURISTIC,
    LLM,
)

SUMMARY_KEY = "summary"
USER = "user"
_RANK = {HEURISTIC: 1, COMPACTION: 2, LLM: 3, USER: 4}


def read_summary(store_or_meta: Any) -> dict:
    meta = getattr(store_or_meta, "meta", store_or_meta)
    try:
        value = meta.get(SUMMARY_KEY)
    except Exception:  # noqa: BLE001
        return {}
    return value if isinstance(value, dict) and value.get("text") else {}


def should_replace(current: dict, source: str, configured: str) -> bool:
    """Whether an automatic ``source`` summary may overwrite ``current``.

    The configured source always wins; a fallback source only replaces a
    summary it does not rank below.
    """
    if not current:
        return True
    held = current.get("source")
    if held == USER:
        return False
    return source == configured or _RANK.get(source, 0) >= _RANK.get(held, 0)


def write_summary(store: Any, text: str, source: str, at_turn: int) -> dict:
    """Store a summary record (requires blocking)."""
    record = {
        "text": text,
        "source": source,
        "at_turn": int(at_turn),
        "updated_at": time.time(),
    }
    store.meta[SUMMARY_KEY] = record
    return record


def clear_summary(store: Any) -> None:
    """Drop the summary so automatic writing starts over (requires blocking)."""
    store.meta[SUMMARY_KEY] = {}
