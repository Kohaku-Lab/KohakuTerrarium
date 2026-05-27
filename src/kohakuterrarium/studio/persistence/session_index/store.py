"""SessionIndex — central per-session_dir sidecar for fast listing + search.

Backs ``/api/sessions`` with:

  * **KVault ``entries``** — keyed by filename (``alice.kohakutr.v2``);
    value is the full :class:`SessionIndexEntry` as a msgpack dict.
    Iteration is the source of truth for sort + filter queries.

  * **TextVault ``search``** — FTS5 over the five searchable text
    columns (``name``, ``preview``, ``config_path``, ``agents``,
    ``pwd``).  Row ``value`` is the filename, so a hit gives us
    ``(rowid, score, filename)`` and we don't need a separate
    rowid-to-filename mapping.

  * **KVault ``meta``** — schema version, bootstrap flag,
    last-reconcile timestamp.

One sidecar file ``<session_dir>/.kt-index.kvault`` holds all three
tables.  KohakuVault's WAL + retry handles reader / writer races.
The class is a long-lived singleton (see ``__init__.py``) — open
once at server startup, close on shutdown.

Concurrency: KVault and TextVault each retry on ``SQLITE_BUSY``.
Reader / writer contention is bounded.  Multiple processes sharing
the same sidecar are tolerated (last-writer-wins on an entry row),
though only the API server typically writes.
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


# KVault.keys() yields bytes; SessionStore + our entries use string
# filenames as keys.  Decode here so the rest of the index works in
# str-space.  The ``limit`` arg is KVault's per-batch size — the
# iterator itself handles pagination internally past that.
_DEFAULT_KEY_BATCH = 100_000


def _iter_kv_keys(kv: KVault, batch: int = _DEFAULT_KEY_BATCH) -> Iterator[str]:
    for k in kv.keys(limit=batch):
        yield k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else str(k)


# ──────────────────────────────────────────────────────────────────
# Listing result
# ──────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────
# SessionIndex
# ──────────────────────────────────────────────────────────────────


# Names of search-vault columns — kept in sync with
# ``SessionIndexEntry.to_search_columns``.  Module-level so tests +
# reconcile can reference the canonical list.  Bump
# ``entry.SCHEMA_VERSION`` whenever this set changes — the schema
# check on open will then clear the sidecar and trigger a full
# reconcile.
SEARCH_COLUMNS = (
    "name",
    "preview",
    "config_path",
    "agents",
    "pwd",
    "terrarium_name",
    "config_type",
)

# Sort keys the API exposes.  Anything else returns rows in the
# order the underlying store provided.
_VALID_SORT_KEYS = ("last_active", "created_at", "name", "status", "relevance")


class SessionIndex:
    """Central per-session_dir index of session metadata + FTS search.

    The sidecar lives at ``<session_dir>/.kt-index.kvault``; the
    parent directory is created on first use.  Schema bumps clear
    the sidecar — re-bootstrap from disk is cheap because the
    sidecar is a derived cache.
    """

    def __init__(self, sidecar_path: Path) -> None:
        self._path = str(sidecar_path)
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        # Schema check BEFORE opening the search table.  TextVault's
        # FTS5 schema (column list) is baked into the SQLite table
        # on first creation; opening it against a stale table with
        # the old column set would happily reuse it, and the next
        # upsert would raise ``table search has no column named X``.
        # ``_purge_if_stale_schema`` does a meta-only peek and, on
        # mismatch, deletes the whole sidecar file (and ``-wal`` /
        # ``-shm`` companions) so the constructors below open fresh.
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

    # ── Schema ────────────────────────────────────────────────

    def _stamp_schema(self) -> None:
        """Persist the current schema signature so the next open can
        detect drift without inspecting the FTS table directly.

        We write BOTH ``schema_version`` (legacy scalar, kept for
        forward telemetry) AND ``search_columns`` (the ground-truth
        column list).  The drift check trusts ``search_columns``
        because the version scalar has been seen to lie — an older
        broken ``_ensure_schema`` cleared row content + bumped the
        version without actually recreating the FTS table.
        """
        if self._meta.get("schema_version") != SCHEMA_VERSION:
            self._meta.put("schema_version", SCHEMA_VERSION)
        if self._meta.get("search_columns") != list(SEARCH_COLUMNS):
            self._meta.put("search_columns", list(SEARCH_COLUMNS))

    @staticmethod
    def _purge_if_stale_schema(sidecar_path: Path) -> None:
        """If a sidecar exists with a different FTS column set, delete it.

        Cheap meta-only probe — opens just the ``meta`` table to
        read ``search_columns`` and closes immediately.  If the
        stored list doesn't match :data:`SEARCH_COLUMNS` (missing,
        wrong type, different order, or different membership), the
        sidecar plus its SQLite WAL/SHM companions are removed so
        the caller's full constructor opens a clean file.  No-op
        when the sidecar doesn't exist yet.

        WHY trust ``search_columns`` over ``schema_version``: an
        older broken ``_ensure_schema`` cleared row content + wrote
        the new version scalar BUT didn't drop the underlying FTS
        table.  Sidecars in that state claim to be current while
        the FTS table still has the old column set; the next upsert
        crashes with ``table search has no column named X``.  The
        actual column list is ground truth — compare against that.
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

    # ── Mutations ─────────────────────────────────────────────

    def upsert(self, entry: SessionIndexEntry) -> None:
        """Idempotent insert-or-update keyed by ``entry.filename``.

        Reuses the existing FTS rowid when the entry already exists
        so the search index stays compact across many edits.  If the
        FTS row was deleted out from under us (corrupted sidecar,
        external mutation), we transparently insert a new one.
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

    # ── Reads ─────────────────────────────────────────────────

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
        """Yield every stored entry dict (stripped of internal fields).

        Used by aggregation paths that need a full scan (stats,
        :meth:`Studio.persistence.list`).  For filtered / paginated
        reads, use :meth:`list` instead — it short-circuits on FTS
        hits and supports sort ordering.
        """
        for fname in _iter_kv_keys(self._entries):
            entry = self._entries.get(fname)
            if entry is None:
                continue
            d = dict(entry)
            d.pop("_search_rowid", None)
            yield d

    def count(self) -> int:
        # Iterate keys because KVault doesn't expose a cheap count.
        # Cap at first 100k to avoid runaway scans on a broken sidecar.
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
        """Paginated, filtered, optionally-searched listing.

        ``sort`` accepts ``last_active``, ``created_at``, ``name``,
        ``status``, or ``relevance``.  ``relevance`` only makes
        sense with a non-empty ``search`` — it preserves the BM25
        order TextVault returns.

        Filters (``status``, ``config_type``, ``node_id``) match
        exact string equality.  Pass ``None`` to skip.

        ``limit`` accepts any positive integer; ``offset`` is clamped
        to ``≥0``.  The previous ``≤1000`` cap was a holdover from
        HTTP-only callers — :meth:`Studio.persistence.list` (the
        Python surface) passes ``limit=index.count()`` to get the full
        list and the cap silently truncated install with >1000
        sessions.  HTTP callers still pass an explicit page size, so
        in practice this only matters for the programmatic path.
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

    # ── Internal list paths ───────────────────────────────────

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
        # Over-fetch FTS hits so a downstream facet filter still has
        # rows left after pruning.  Cap at ``max(2000, limit + offset)``
        # so programmatic callers asking for everything (e.g.
        # ``Studio.persistence.list(search='foo')`` passing the full
        # row count as ``limit``) don't silently truncate at 2000,
        # while typical HTTP pages stay capped at 2000.
        k = max(2_000, limit + offset, (limit + offset) * 5)
        k = max(200, k)
        hits = self._search.search(q, k=k)
        rows: list[dict[str, Any]] = []
        for _rowid, score, value in hits:
            fname = value if isinstance(value, str) else None
            if not fname or fname not in self._entries:
                # FTS row's backing entry vanished — defer cleanup
                # to the next ``reconcile()``; just skip here.
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
            # FTS returns BM25 order already (lower = better in
            # KohakuVault); preserve it but honour ``order=asc`` as
            # the explicit "least relevant first" inversion.
            if order == "asc":
                rows.reverse()
        else:
            rows.sort(key=lambda e: (e.get(sort) or ""), reverse=(order == "desc"))
        total = len(rows)
        page = [_strip_internal(e) for e in rows[offset : offset + limit]]
        return SessionIndexPage(page, total, offset, limit)

    # ── Meta-table accessors used by reconcile + bootstrap ────

    def meta_get(self, key: str, default: Any = None) -> Any:
        if key not in self._meta:
            return default
        return self._meta.get(key)

    def meta_put(self, key: str, value: Any) -> None:
        self._meta.put(key, value)

    # ── Lifecycle ─────────────────────────────────────────────

    def close(self) -> None:
        """Release every native SQLite handle.

        Mirrors ``SessionStore.close``'s pattern of explicit ``del
        table._inner`` to ensure refcount-driven cleanup runs now,
        not at GC time.  Critical on Windows where a lingering
        handle blocks the next process from opening the same file.
        """
        if self._closed:
            return
        for table in (self._entries, self._search, self._meta):
            try:
                table.close()
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
