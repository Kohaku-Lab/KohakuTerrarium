"""SessionIndexEntry — one row of the central session-listing sidecar.

Carries the listing-shape fields plus the bookkeeping the sidecar
cache needs:

  * ``file_mtime`` / ``file_size`` — fingerprint compared on
    :func:`reconcile` so unchanged files skip the per-session
    SQLite open.
  * ``_search_rowid`` — TextVault rowid we kept around for an
    in-place FTS5 update without a second reverse-map lookup.

The dataclass is purposefully flat: ``asdict()`` produces a payload
KohakuVault's KVault can store as a single msgpack value, and the
TextVault columns are pulled out of it for the FTS index without a
separate "search document" representation.
"""

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem


def _max_mtime_with_wal(path: Path) -> float:
    """Most-recent mtime across the session file + its WAL/SHM sidecars.

    SQLite WAL mode writes most data to ``foo.kohakutr-wal`` and
    ``foo.kohakutr-shm`` *before* checkpointing back to the main
    file.  A reconcile that only stats the main file would mark a
    busy session as unchanged for hours.  This helper matches what
    the legacy listing used to do (``store._max_mtime``) before the
    sidecar replaced it — fingerprint = ``(max_mtime, main_size)``
    so any append-only WAL growth invalidates the cache.
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


# Bump on any schema change.  ``SessionIndex._ensure_schema`` clears
# the sidecar when the stored version doesn't match — cheap because
# the sidecar is a derived cache and the next ``reconcile()`` rebuilds
# it from disk.
#
# Version history:
#   1 — initial release.
#   2 — added ``terrarium_name`` + ``config_type`` to FTS columns and
#       included WAL/SHM mtime in the file fingerprint.
SCHEMA_VERSION = 2


@dataclass
class SessionIndexEntry:
    """One row of the session-listing sidecar."""

    # Identity (sidecar primary key)
    filename: str
    name: str

    # File fingerprint (cache invalidation on reconcile)
    file_mtime: float
    file_size: int

    # Searchable text fields (also denormalised into TextVault columns)
    preview: str
    config_path: str
    agents: list[str]
    pwd: str

    # Filter / sort fields
    config_type: str
    status: str
    last_active: str
    created_at: str
    format_version: int
    node_id: str

    # Pass-through fields (rendered but not searched / sorted)
    terrarium_name: str = ""
    has_vector_index: bool = False
    parent_session_id: str | None = None
    fork_point: int | None = None
    forked_children: list[str] = field(default_factory=list)
    migrated_from_version: int | None = None

    # Internal — never returned to the API.
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
        """Build an entry from a session's loaded meta dict.

        ``file_mtime`` / ``file_size`` are optional because tests
        often want to pin them rather than re-stat.  Production
        callers pass ``None`` and let us stat the file once.
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
        """Inverse of :func:`asdict` — recreate an entry from a stored row.

        Tolerant of missing keys (older schema rows): each field falls
        back to its dataclass default.  Callers that need strict
        validation should run :func:`_ensure_schema` first.
        """
        kwargs: dict[str, Any] = {}
        # Required positional-style fields — fail loud if missing.
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
        # Optional / pass-through.
        for f in (
            "terrarium_name",
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
        """The denormalised text columns the FTS index keeps in sync.

        Keys must match the ``columns=`` arg passed to ``TextVault``
        in :class:`SessionIndex`.
        """
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
        """Serialise for storage.  Internal fields included."""
        return asdict(self)

    def to_listing_dict(self) -> dict[str, Any]:
        """Serialise for the HTTP listing payload.  Strips internal fields."""
        d = asdict(self)
        d.pop("_search_rowid", None)
        return d

    def fingerprint(self) -> tuple[float, int]:
        return (self.file_mtime, self.file_size)
