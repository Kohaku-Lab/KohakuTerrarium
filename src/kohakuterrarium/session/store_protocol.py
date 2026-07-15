"""Define the structural store surface required by session helpers."""

from pathlib import Path
from typing import Any, Protocol

from kohakuvault import KVault, TextVault


class SessionStoreLike(Protocol):
    """Expose the tables and operations required by store helper modules."""

    meta: KVault
    state: KVault
    events: KVault
    channels: KVault
    subagents: KVault
    jobs: KVault
    conversation: KVault
    turn_rollup: KVault
    fts: TextVault
    path: str
    artifacts_dir: Path

    def flush(self) -> None: ...
    def load_meta(self) -> dict[str, Any]: ...
