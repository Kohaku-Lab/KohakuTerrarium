"""SessionIndexEntry — one row of the central session-listing sidecar.

The flat dataclass stores listing fields, the file fingerprint used to skip
unchanged sessions, and the TextVault row ID used for in-place FTS updates.
``asdict`` therefore produces both the KVault value and the source for search
columns without a separate document model.
"""

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem


def _max_mtime_with_wal(path: Path) -> float:
    """Return the newest mtime across a session file and SQLite sidecars.

    WAL writes precede main-file checkpoints, so the fingerprint must include
    sidecars to invalidate active sessions promptly.
    """
    try:
        best = path.stat().st_mtime
    except OSError:
        return 0.0
    for suffix in ("-wal", "-shm"):
        sidecar = str(path) + suffix
        if not os.path.exists(sidecar):
            continue
        try:
            mt = os.stat(sidecar).st_mtime
        except OSError:
            continue
        if mt > best:
            best = mt
    return best


# Bump for any stored or FTS schema change; the derived sidecar is rebuilt on
# mismatch. Version 2 added terrarium/config search fields and WAL-aware
# fingerprints. Version 3 added the persisted conversation-open marker;
# version 4 added stable conversation identities.
SCHEMA_VERSION = 4


@dataclass
class SessionIndexEntry:
    """One row of the session-listing sidecar."""

    filename: str
    name: str

    file_mtime: float
    file_size: int

    preview: str
    config_path: str
    agents: list[str]
    pwd: str

    config_type: str
    status: str
    last_active: str
    created_at: str
    format_version: int
    node_id: str

    terrarium_name: str = ""
    conversation_open: bool = False
    conversation_id: str | None = None
    has_vector_index: bool = False
    parent_session_id: str | None = None
    fork_point: int | None = None
    forked_children: list[str] = field(default_factory=list)
    migrated_from_version: int | None = None

    _search_rowid: int = 0

    @classmethod
    def from_meta(
        cls,
        *,
        path: Path,
        meta: dict[str, Any],
        preview: str,
        has_vector_index: bool,
        file_mtime: float | None = None,
        file_size: int | None = None,
    ) -> "SessionIndexEntry":
        """Build an entry from loaded metadata and an optional fingerprint.

        Missing fingerprint components are read from disk; supplied values let
        callers reuse an earlier stat and keep the indexed snapshot coherent.
        """
        if file_mtime is None or file_size is None:
            st = path.stat()
            if file_mtime is None:
                file_mtime = _max_mtime_with_wal(path)
            if file_size is None:
                file_size = st.st_size
        lineage = meta.get("lineage") if isinstance(meta.get("lineage"), dict) else None
        fork = (
            (lineage or {}).get("fork")
            if isinstance((lineage or {}).get("fork"), dict)
            else None
        )
        migration = (
            (lineage or {}).get("migration")
            if isinstance((lineage or {}).get("migration"), dict)
            else None
        )
        forked_raw = meta.get("forked_children") or []
        forked_children = [
            c.get("session_id") if isinstance(c, dict) else c
            for c in forked_raw
            if c is not None
        ]
        return cls(
            filename=path.name,
            name=normalize_session_stem(path),
            file_mtime=float(file_mtime),
            file_size=int(file_size),
            preview=str(preview or ""),
            config_path=str(meta.get("config_path", "") or ""),
            agents=list(meta.get("agents") or []),
            pwd=str(meta.get("pwd", "") or ""),
            config_type=str(meta.get("config_type", "unknown") or "unknown"),
            status=str(meta.get("status", "") or ""),
            last_active=str(meta.get("last_active", "") or ""),
            created_at=str(meta.get("created_at", "") or ""),
            format_version=int(meta.get("format_version", 1) or 1),
            node_id=str(meta.get("on_node", "") or ""),
            terrarium_name=str(meta.get("terrarium_name", "") or ""),
            conversation_open=bool(meta.get("conversation_open", False)),
            conversation_id=(
                str(meta["conversation_id"]) if meta.get("conversation_id") else None
            ),
            has_vector_index=bool(has_vector_index),
            parent_session_id=(fork or {}).get("parent_session_id") if fork else None,
            fork_point=(fork or {}).get("fork_point") if fork else None,
            forked_children=forked_children,
            migrated_from_version=(
                (migration or {}).get("source_version") if migration else None
            ),
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionIndexEntry":
        """Recreate an entry from a stored row.

        Optional fields may be absent in older rows; required fields remain
        required by the dataclass constructor.
        """
        kwargs: dict[str, Any] = {}
        for f in (
            "filename",
            "name",
            "file_mtime",
            "file_size",
            "preview",
            "config_path",
            "agents",
            "pwd",
            "config_type",
            "status",
            "last_active",
            "created_at",
            "format_version",
            "node_id",
        ):
            if f in d:
                kwargs[f] = d[f]
        for f in (
            "terrarium_name",
            "conversation_open",
            "conversation_id",
            "has_vector_index",
            "parent_session_id",
            "fork_point",
            "forked_children",
            "migrated_from_version",
            "_search_rowid",
        ):
            if f in d:
                kwargs[f] = d[f]
        return cls(**kwargs)

    def to_search_columns(self) -> dict[str, str]:
        """Return denormalized FTS text using ``SessionIndex`` column names."""
        return {
            "name": self.name,
            "preview": self.preview,
            "config_path": self.config_path,
            "agents": " ".join(self.agents),
            "pwd": self.pwd,
            "terrarium_name": self.terrarium_name,
            "config_type": self.config_type,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize all fields for sidecar storage."""
        return asdict(self)

    def to_listing_dict(self) -> dict[str, Any]:
        """Serialize public listing fields without the FTS row ID."""
        d = asdict(self)
        d.pop("_search_rowid", None)
        return d

    def fingerprint(self) -> tuple[float, int]:
        return (self.file_mtime, self.file_size)
