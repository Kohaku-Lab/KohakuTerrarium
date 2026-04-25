"""Protocols for session store helper modules.

Keeping the structural type separate from ``session.store`` lets helpers such
as ``store_fork`` type-check against the small surface they need without
importing the concrete ``SessionStore`` class at runtime.
"""

from pathlib import Path
from typing import Any, Protocol

from kohakuvault import KVault, TextVault


class SessionStoreLike(Protocol):
    """Structural surface of ``SessionStore`` needed by helper modules."""

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
