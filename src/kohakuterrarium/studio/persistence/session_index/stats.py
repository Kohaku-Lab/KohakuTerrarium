"""Aggregations over the session-index sidecar.

The HTTP ``GET /api/sessions/stats`` route + the Python
``Studio.persistence.stats()`` surface both rely on this helper.
Every field comes straight off the cached :class:`SessionIndexEntry`
columns — no ``.kohakutr`` file is opened.

Output shape (frozen — frontend consumers rely on it)::

    {
        "count": int,
        "by_config_type":    {<type>: <n>, ...},
        "by_status":         {<status>: <n>, ...},
        "by_recency":        {"1d": <n>, "7d": <n>, "30d": <n>, "older": <n>},
        "by_format_version": {"1": <n>, "2": <n>, ...},
        "agents_top":        [[<agent>, <n>], ...],   # top 5 by count
        "average_age_seconds": float | None,
    }
"""

import time
from collections import Counter
from datetime import datetime
from typing import Any

from kohakuterrarium.studio.persistence.session_index.store import SessionIndex

_RECENCY_BUCKETS = ("1d", "7d", "30d", "older")


def _empty() -> dict[str, Any]:
    return {
        "count": 0,
        "by_config_type": {},
        "by_status": {},
        "by_recency": {b: 0 for b in _RECENCY_BUCKETS},
        "by_format_version": {},
        "agents_top": [],
        "average_age_seconds": None,
    }


def _to_ts(s: str) -> float | None:
    """Parse an ISO-8601 timestamp into a float epoch.  ``None`` on failure.

    Mirrors the legacy parser: accepts ``Z`` suffix via the
    ``+00:00`` rewrite that ``datetime.fromisoformat`` needs pre-3.11
    (we still support 3.10).
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def aggregate_stats(index: SessionIndex) -> dict[str, Any]:
    """Walk every sidecar entry once and return aggregation counts.

    The aggregation is a single linear scan, dominated by KVault
    deserialisation per row — sub-millisecond up to ~10k sessions.
    """
    by_config_type: Counter = Counter()
    by_status: Counter = Counter()
    by_format: Counter = Counter()
    agents_counter: Counter = Counter()
    by_recency = {b: 0 for b in _RECENCY_BUCKETS}

    now = time.time()
    age_total = 0.0
    age_count = 0
    count = 0

    for entry in index.iter_entries():
        count += 1
        by_config_type[entry.get("config_type") or "unknown"] += 1
        by_status[entry.get("status") or "unknown"] += 1
        by_format[str(entry.get("format_version", 1))] += 1
        for agent in entry.get("agents") or []:
            if agent:
                agents_counter[agent] += 1
        ts = _to_ts(entry.get("last_active") or entry.get("created_at") or "")
        if ts is not None:
            age = now - ts
            if age >= 0:
                age_total += age
                age_count += 1
                if age < 86400:
                    by_recency["1d"] += 1
                elif age < 86400 * 7:
                    by_recency["7d"] += 1
                elif age < 86400 * 30:
                    by_recency["30d"] += 1
                else:
                    by_recency["older"] += 1

    if count == 0:
        return _empty()

    return {
        "count": count,
        "by_config_type": dict(by_config_type),
        "by_status": dict(by_status),
        "by_recency": by_recency,
        "by_format_version": dict(by_format),
        "agents_top": [list(p) for p in agents_counter.most_common(5)],
        "average_age_seconds": (age_total / age_count) if age_count else None,
    }
