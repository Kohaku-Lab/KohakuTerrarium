"""SessionIndex — central per-session_dir sidecar for fast listing + search.

A single ``<session_dir>/.kt-index.kvault`` sidecar contains metadata entries,
full-text search rows, and index metadata. Entry rows are keyed by session
filename; search-row values store the same filename to avoid a separate row-ID
mapping.

The index is long-lived and tolerates concurrent processes through
KohakuVault's WAL and busy retries. Entry updates are last-writer-wins.
"""

from collections.abc import Iterable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from kohakuvault import KVault, TextVault

from kohakuterrarium.studio.persistence.session_index.entry import (
    SCHEMA_VERSION,
    SessionIndexEntry,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# KVault yields byte keys and paginates internally; normalize filenames to
# strings at the boundary.
_DEFAULT_KEY_BATCH = 100_000


def _iter_kv_keys(kv: KVault, batch: int = _DEFAULT_KEY_BATCH) -> Iterator[str]:
    for k in kv.keys(limit=batch):
        yield k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else str(k)


class SessionIndexPage:
    """Paginated listing result returned by :meth:`SessionIndex.list`."""

    __slots__ = ("rows", "total", "offset", "limit")

    def __init__(
        self,
        rows: list[dict[str, Any]],
        total: int,
        offset: int,
        limit: int,
    ) -> None:
        self.rows = rows
        self.total = total
        self.offset = offset
        self.limit = limit

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessions": self.rows,
            "total": self.total,
            "offset": self.offset,
            "limit": self.limit,
        }


# This order must match ``SessionIndexEntry.to_search_columns``. Changing it
# requires a schema-version bump so the derived sidecar is rebuilt.
SEARCH_COLUMNS = (
    "name",
    "preview",
    "config_path",
    "agents",
    "pwd",
    "terrarium_name",
    "config_type",
)

# Unsupported sort keys fall back to ``last_active``.
_VALID_SORT_KEYS = ("last_active", "created_at", "name", "status", "relevance")


class SessionIndex:
    """Maintain session metadata and full-text search in a derived sidecar.

    The parent directory is created on first use. Schema drift discards the
    sidecar because all indexed data can be reconstructed from session files.
    """

    def __init__(self, sidecar_path: Path) -> None:
        self._path = str(sidecar_path)
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        # FTS column definitions are fixed at table creation. Purging stale
        # schemas before opening prevents TextVault from reusing an incompatible
        # table that would fail on the next upsert.
        self._purge_if_stale_schema(sidecar_path)
        self._entries = KVault(self._path, table="entries")
        self._entries.enable_auto_pack()
        self._search = TextVault(
            self._path,
            table="search",
            columns=list(SEARCH_COLUMNS),
        )
        self._search.enable_auto_pack()
        self._meta = KVault(self._path, table="meta")
        self._meta.enable_auto_pack()
        self._closed = False
        self._stamp_schema()

    def _stamp_schema(self) -> None:
        """Persist schema telemetry and the authoritative FTS column order.

        Drift detection trusts ``search_columns`` because a historical migration
        could update the scalar version without recreating the FTS table.
        """
        if self._meta.get("schema_version") != SCHEMA_VERSION:
            self._meta.put("schema_version", SCHEMA_VERSION)
        if self._meta.get("search_columns") != list(SEARCH_COLUMNS):
            self._meta.put("search_columns", list(SEARCH_COLUMNS))

    @staticmethod
    def _purge_if_stale_schema(sidecar_path: Path) -> None:
        """Delete sidecars whose stored FTS column order is not current.

        A meta-only probe avoids opening the search table. Missing, unreadable,
        reordered, or changed columns invalidate the database and its WAL/SHM
        companions. The column list, rather than the version scalar, is the
        ground truth for compatibility.
        """
        if not sidecar_path.exists():
            return
        stored: Any = None
        meta = None
        try:
            meta = KVault(str(sidecar_path), table="meta")
            if "search_columns" in meta:
                stored = meta.get("search_columns")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Session index meta probe failed; rebuilding sidecar",
                path=str(sidecar_path),
                error=str(exc),
            )
            stored = "__unreadable__"
        finally:
            if meta is not None:
                try:
                    meta.close()
                    if hasattr(meta, "_inner"):
                        del meta._inner
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "meta probe close failed", error=str(exc), exc_info=True
                    )
        if isinstance(stored, list) and stored == list(SEARCH_COLUMNS):
            return
        logger.info(
            "Session index schema drift; purging sidecar",
            stored_columns=stored,
            current_columns=list(SEARCH_COLUMNS),
            path=str(sidecar_path),
        )
        for suffix in ("", "-wal", "-shm"):
            target = Path(str(sidecar_path) + suffix)
            try:
                target.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    "Failed to remove stale sidecar file",
                    path=str(target),
                    error=str(exc),
                )

    def upsert(self, entry: SessionIndexEntry) -> None:
        """Insert or update an entry and its FTS row by filename.

        Existing search row IDs are reused to keep the index compact. A missing
        or externally removed search row is recreated transparently.
        """
        cols = entry.to_search_columns()
        existing = (
            self._entries.get(entry.filename)
            if entry.filename in self._entries
            else None
        )
        rowid = int((existing or {}).get("_search_rowid", 0))
        if rowid:
            try:
                self._search.update(id=rowid, texts=cols, value=entry.filename)
                entry._search_rowid = rowid
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "FTS row missing; reinserting",
                    rowid=rowid,
                    error=str(exc),
                    exc_info=True,
                )
                entry._search_rowid = int(
                    self._search.insert(cols, value=entry.filename)
                )
        else:
            entry._search_rowid = int(self._search.insert(cols, value=entry.filename))
        self._entries.put(entry.filename, asdict(entry))

    def upsert_many(self, entries: Iterable[SessionIndexEntry]) -> int:
        n = 0
        for e in entries:
            self.upsert(e)
            n += 1
        return n

    def delete(self, filename: str) -> bool:
        """Remove both the KVault row and its FTS twin.

        Returns ``True`` when something was removed, ``False`` when
        the filename was already absent.
        """
        if filename not in self._entries:
            return False
        existing = self._entries.get(filename)
        rowid = int((existing or {}).get("_search_rowid", 0))
        if rowid:
            try:
                self._search.delete(rowid)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "FTS row already gone", rowid=rowid, error=str(exc), exc_info=True
                )
        self._entries.delete(filename)
        return True

    def clear(self) -> None:
        """Wipe every table — used on schema bumps and explicit rebuild."""
        try:
            self._entries.clear()
        except Exception as exc:  # noqa: BLE001
            logger.warning("clear entries failed", error=str(exc), exc_info=True)
        try:
            self._search.clear()
        except Exception as exc:  # noqa: BLE001
            logger.warning("clear search failed", error=str(exc), exc_info=True)

    def get(self, filename: str) -> dict[str, Any] | None:
        if filename not in self._entries:
            return None
        d = dict(self._entries.get(filename))
        d.pop("_search_rowid", None)
        return d

    def fingerprint(self, filename: str) -> tuple[float, int] | None:
        if filename not in self._entries:
            return None
        d = self._entries.get(filename)
        return (float(d.get("file_mtime", 0.0)), int(d.get("file_size", 0)))

    def all_filenames(self) -> list[str]:
        return list(_iter_kv_keys(self._entries))

    def iter_entries(self) -> Iterator[dict[str, Any]]:
        """Yield all entries without internal search fields.

        Full scans support aggregation; filtered and paginated callers should
        use :meth:`list` to benefit from FTS and sorting.
        """
        for fname in _iter_kv_keys(self._entries):
            entry = self._entries.get(fname)
            if entry is None:
                continue
            d = dict(entry)
            d.pop("_search_rowid", None)
            yield d

    def count(self) -> int:
        # KVault has no cheap count; the safety cap bounds scans of malformed
        # or unexpectedly large sidecars.
        n = 0
        for _ in _iter_kv_keys(self._entries):
            n += 1
            if n >= 100_000:
                break
        return n

    def list(
        self,
        *,
        search: str = "",
        status: str | None = None,
        config_type: str | None = None,
        node_id: str | None = None,
        sort: str = "last_active",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> SessionIndexPage:
        """Return a filtered, sorted, optionally searched page.

        Filters use exact equality. Relevance sorting preserves TextVault's
        BM25 order and is meaningful only with search text. Limits remain
        unbounded above because programmatic callers request the complete index;
        HTTP callers enforce their own page sizes.
        """
        sort = sort if sort in _VALID_SORT_KEYS else "last_active"
        order = order if order in ("asc", "desc") else "desc"
        limit = max(1, int(limit))
        offset = max(0, int(offset))
        q = (search or "").strip()
        if q:
            return self._list_searched(
                q,
                status=status,
                config_type=config_type,
                node_id=node_id,
                sort=sort,
                order=order,
                limit=limit,
                offset=offset,
            )
        return self._list_unsearched(
            status=status,
            config_type=config_type,
            node_id=node_id,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

    def _passes(
        self,
        e: dict[str, Any],
        status: str | None,
        config_type: str | None,
        node_id: str | None,
    ) -> bool:
        if status is not None and e.get("status") != status:
            return False
        if config_type is not None and e.get("config_type") != config_type:
            return False
        if node_id is not None and e.get("node_id") != node_id:
            return False
        return True

    def _list_unsearched(
        self,
        *,
        status: str | None,
        config_type: str | None,
        node_id: str | None,
        sort: str,
        order: str,
        limit: int,
        offset: int,
    ) -> SessionIndexPage:
        rows: list[dict[str, Any]] = []
        for fname in _iter_kv_keys(self._entries):
            entry_dict = self._entries.get(fname)
            if not entry_dict or not self._passes(
                entry_dict, status, config_type, node_id
            ):
                continue
            rows.append(entry_dict)
        rows.sort(key=lambda e: (e.get(sort) or ""), reverse=(order == "desc"))
        total = len(rows)
        page = [_strip_internal(e) for e in rows[offset : offset + limit]]
        return SessionIndexPage(page, total, offset, limit)

    def _list_searched(
        self,
        q: str,
        *,
        status: str | None,
        config_type: str | None,
        node_id: str | None,
        sort: str,
        order: str,
        limit: int,
        offset: int,
    ) -> SessionIndexPage:
        # Over-fetch before facet filtering. The requested page size prevents
        # full-index programmatic searches from truncating at the HTTP-oriented
        # baseline of 2,000 hits.
        k = max(2_000, limit + offset, (limit + offset) * 5)
        k = max(200, k)
        hits = self._search.search(q, k=k)
        rows: list[dict[str, Any]] = []
        for _rowid, score, value in hits:
            fname = value if isinstance(value, str) else None
            if not fname or fname not in self._entries:
                # Reconciliation owns cleanup of orphaned FTS rows.
                continue
            entry_dict = self._entries.get(fname)
            if not entry_dict or not self._passes(
                entry_dict, status, config_type, node_id
            ):
                continue
            entry_dict = dict(entry_dict)
            entry_dict["_fts_score"] = float(score)
            rows.append(entry_dict)
        if sort == "relevance":
            # KohakuVault returns lower BM25 scores first; ascending order is
            # defined here as the explicit least-relevant-first inversion.
            if order == "asc":
                rows.reverse()
        else:
            rows.sort(key=lambda e: (e.get(sort) or ""), reverse=(order == "desc"))
        total = len(rows)
        page = [_strip_internal(e) for e in rows[offset : offset + limit]]
        return SessionIndexPage(page, total, offset, limit)

    def meta_get(self, key: str, default: Any = None) -> Any:
        if key not in self._meta:
            return default
        return self._meta.get(key)

    def meta_put(self, key: str, value: Any) -> None:
        self._meta.put(key, value)

    def close(self) -> None:
        """Release native SQLite handles immediately.

        Explicitly deleting native wrappers forces refcount-driven cleanup,
        which prevents lingering Windows handles from blocking later opens.
        """
        if self._closed:
            return
        # TextVault has no close method; its underlying vault is released
        # explicitly after closable tables are handled.
        for table in (self._entries, self._search, self._meta):
            close = getattr(table, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("close table failed", error=str(exc), exc_info=True)
        for table in (self._entries, self._meta):
            try:
                del table._inner
            except AttributeError:
                pass
        try:
            del self._search._vault
        except AttributeError:
            pass
        self._closed = True

    @property
    def path(self) -> str:
        return self._path


def _strip_internal(e: dict[str, Any]) -> dict[str, Any]:
    e = dict(e)
    e.pop("_search_rowid", None)
    e.pop("_fts_score", None)
    return e
