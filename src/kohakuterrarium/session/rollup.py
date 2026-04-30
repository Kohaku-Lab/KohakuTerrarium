"""Per-turn rollup helpers (Wave B §3.3).

Denormalised cache keyed by ``<agent>:turn:<turn_index:06d>``. Stores
per-turn counters (tokens, duration, cost) so viewers such as Studio
and the TUI cost panel can fetch a summary for a given turn without
reducing over the raw event log.

Kept in a separate module so ``session/store.py`` stays under the
600-line soft cap enforced by ``tests/unit/test_file_sizes.py``.
"""

from typing import Any

from kohakuvault import KVault

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def turn_rollup_key(agent: str, turn_index: int) -> str:
    """Build a rollup key. Shape: ``<agent>:turn:<turn_index:06d>``.

    Exposed so tests can probe the table directly and so other
    helpers (resume, migration) can scan by prefix.
    """
    return f"{agent}:turn:{turn_index:06d}"


def save_turn_rollup(
    table: KVault, agent: str, turn_index: int, data: dict[str, Any]
) -> None:
    """Write a per-turn rollup row.

    The payload is a free-form dict; today's callers fill in
    ``started_at``, ``ended_at``, ``tokens_in``, ``tokens_out``,
    ``tokens_cached``, and optionally ``cost_usd`` (nullable for now).
    ``agent`` and ``turn_index`` are added automatically if missing so
    readers can round-trip without the caller knowing the key layout.
    """
    key = turn_rollup_key(agent, turn_index)
    merged = dict(data)
    merged.setdefault("agent", agent)
    merged.setdefault("turn_index", turn_index)
    merged.setdefault("cost_usd", None)
    table[key] = merged


def get_turn_rollup(table: KVault, agent: str, turn_index: int) -> dict | None:
    """Load a per-turn rollup row. Returns ``None`` when missing."""
    try:
        val = table[turn_rollup_key(agent, turn_index)]
        return val if isinstance(val, dict) else None
    except KeyError:
        return None


def list_turn_rollups(table: KVault, agent: str) -> list[dict]:
    """List every rollup row for an agent, ordered by ``turn_index``."""
    prefix = f"{agent}:turn:"
    out: list[dict] = []
    for key_bytes in sorted(table.keys(prefix=prefix, limit=2**31 - 1)):
        try:
            val = table[key_bytes]
            if isinstance(val, dict):
                out.append(val)
        except Exception as e:
            logger.debug(
                "Failed to read turn rollup row",
                error=str(e),
                exc_info=True,
            )
    return out
