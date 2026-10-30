"""Open the native tables backing a ``SessionStore``."""

from pathlib import Path
from typing import Any

from kohakuvault import KVault, TextVault

from kohakuterrarium.session.table_policy import TablePolicy
from kohakuterrarium.session.vault_read import open_read_table_or_absent

# Open order; each table is assigned as soon as it opens so a failed open
# can release the ones before it.
TABLE_NAMES: tuple[str, ...] = (
    "meta",
    "state",
    "events",
    "channels",
    "subagents",
    "jobs",
    "conversation",
    "turn_rollup",
)


def open_store_tables(
    store: Any, path: str, *, read_only: bool, policy: dict[str, TablePolicy]
) -> None:
    """Assign every table on ``store``; ``store.fts`` is None when read-only.

    Read-only tables never write: a table missing from the file reads as empty.
    """
    for name in TABLE_NAMES:
        options = policy[name].kvault_options()
        if read_only:
            # A read-only store may outlive a writer's start, so it shares the WAL.
            table = open_read_table_or_absent(
                Path(path), name, snapshot=False, **options
            )
        else:
            table = KVault(path, table=name, **options)
            table.enable_auto_pack()
        setattr(store, name, table)
    if read_only:
        store.fts = None
        return
    store.fts = TextVault(path, table="fts")
    store.fts.enable_auto_pack()
