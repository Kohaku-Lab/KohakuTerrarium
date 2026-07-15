"""Store and retrieve denormalized per-turn session summaries."""

from typing import Any

from kohakuvault import KVault

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def turn_rollup_key(agent: str, turn_index: int) -> str:
    """Build a sortable ``<agent>:turn:<turn_index>`` rollup key."""
    return f"{agent}:turn:{turn_index:06d}"


def save_turn_rollup(
    table: KVault, agent: str, turn_index: int, data: dict[str, Any]
) -> None:
    """Write a per-turn rollup row.

    Agent and turn identifiers are populated when absent, and ``cost_usd``
    defaults to ``None`` so readers receive a stable shape.
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
