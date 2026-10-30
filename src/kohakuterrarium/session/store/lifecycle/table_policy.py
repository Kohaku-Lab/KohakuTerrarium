"""Per-table SQLite cache and reader-pool sizing for session stores."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TablePolicy:
    """Native connection sizing for one KVault table.

    ``cache_kb`` is the writer's page-cache budget; ``reader_cache_kb`` and
    ``reader_idle_limit`` size the pool of independent reader connections;
    ``mmap_size`` is shared by the writer and its readers. Budgets are SQLite
    targets that grow with pages touched, not preallocations.
    """

    cache_kb: int = 4096
    reader_cache_kb: int = 1024
    reader_idle_limit: int = 2
    mmap_size: int = 64 << 20

    def kvault_options(self) -> dict[str, Any]:
        """Keyword arguments for ``KVault(...)``."""
        return asdict(self)


# Event and conversation tables hold the session's bulk and serve replay scans.
BULK_TABLE = TablePolicy(
    cache_kb=16384, reader_cache_kb=4096, reader_idle_limit=4, mmap_size=256 << 20
)
SMALL_TABLE = TablePolicy(
    cache_kb=1024, reader_cache_kb=256, reader_idle_limit=1, mmap_size=0
)

DEFAULT_TABLE_POLICY: Mapping[str, TablePolicy] = {
    "meta": SMALL_TABLE,
    "state": SMALL_TABLE,
    "events": BULK_TABLE,
    "channels": TablePolicy(),
    "subagents": TablePolicy(),
    "jobs": SMALL_TABLE,
    "conversation": BULK_TABLE,
    "turn_rollup": SMALL_TABLE,
}


def resolve_table_policy(
    overrides: Mapping[str, TablePolicy] | None = None,
) -> dict[str, TablePolicy]:
    """The default policy with ``overrides`` applied per table name."""
    policy = dict(DEFAULT_TABLE_POLICY)
    for name, value in (overrides or {}).items():
        if name not in policy:
            raise ValueError(f"unknown session table {name!r}")
        policy[name] = value
    return policy
