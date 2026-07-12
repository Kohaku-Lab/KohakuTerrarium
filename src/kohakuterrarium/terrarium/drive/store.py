"""SQLite-backed durable Drive repository (design §7, Phase 0 decision).

:class:`SqliteDriveRepository` owns a single dedicated ``sqlite3`` connection
holding the eight ``drive_*`` tables. When session-attached that connection lives
in a dedicated sidecar file paired with the session (``<name>.kohakutr.drives``,
see :func:`drive_sidecar_path`), so Drive writes never share a lock with
KohakuVault's ``save_state`` on the ``.kohakutr``. Legacy same-file sessions
migrate into the sidecar once.

sqlite3 is synchronous, so every DB call runs on a single dedicated worker thread
while the public surface stays async; the base class's asyncio lock serializes
whole transactions (a real ``BEGIN IMMEDIATE ... COMMIT`` per operation, any
exception rolling it back). Close via :meth:`close_blocking` (WinError 32).
"""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveError,
    DriveSchemaVersionError,
)
from kohakuterrarium.terrarium.drive.models import (
    DriveAssignment,
    DriveAuditRecord,
    DriveDelivery,
    DriveProgress,
    DriveRecord,
)
from kohakuterrarium.terrarium.drive.repository import (
    DriveDeadLetter,
    DriveOutboxEntry,
    IdempotencyRecord,
    Mutation,
    dead_letter_from_wire,
    dead_letter_to_wire,
    matches_query,
    outbox_from_wire,
    outbox_to_wire,
    parse_dt,
)
from kohakuterrarium.terrarium.drive.repository_base import BaseDriveRepository
from kohakuterrarium.terrarium.drive.requests import DriveQuery, DriveTransitionProposal
from kohakuterrarium.terrarium.drive.saved_snapshot import (
    OfflineDriveSnapshot,
    open_offline_drive_snapshot,
)
from kohakuterrarium.terrarium.drive.store_migration import (
    _SCHEMA,
    DRIVE_SCHEMA_VERSION,
    _migrate_same_file_drives,
    _parse_schema_version,
    drive_sidecar_family,
    drive_sidecar_path,
)
from kohakuterrarium.terrarium.drive.wire import (
    pack_drive_assignment,
    pack_drive_audit,
    pack_drive_delivery,
    pack_drive_progress,
    pack_drive_record,
    pack_transition_proposal,
    unpack_drive_assignment,
    unpack_drive_audit,
    unpack_drive_delivery,
    unpack_drive_progress,
    unpack_drive_record,
    unpack_transition_proposal,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Saved-viewer reads refuse fast (409) on a live-writer lock, not the 5s default.
_READONLY_BUSY_TIMEOUT_MS = 1000

# Tables carrying a ``drive_id`` column, cascaded when a Drive is pruned (R1-10).
# drive_idempotency is (actor,key)-keyed, not per-drive, and is excluded.
_DRIVE_PER_DRIVE_TABLES = (
    "drives",
    "drive_assignments",
    "drive_deliveries",
    "drive_progress",
    "drive_audit",
    "drive_outbox",
    "drive_dead_letters",
    "drive_proposals",
)

__all__ = [
    "DRIVE_SCHEMA_VERSION",
    "DriveRepositoryClosedError",
    "SqliteDriveRepository",
    "drive_sidecar_family",
    "drive_sidecar_path",
    "open_drive_repository_readonly",
    "open_session_drive_repository",
]


class DriveRepositoryClosedError(DriveError):
    """A repository op was attempted after :meth:`SqliteDriveRepository.close_blocking`.

    Fail-first: once the dedicated executor is shut, a late dispatcher/reconcile
    call would otherwise leak a bare ``RuntimeError: cannot schedule new futures
    after shutdown`` from :func:`asyncio.loop.run_in_executor`. Surfacing a typed
    Drive error lets callers distinguish "repository is gone" from a real fault.
    """


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"))


def _load(text: str) -> dict[str, Any]:
    return json.loads(text)


class _SqliteDriveTransaction:
    """A real ``BEGIN IMMEDIATE`` transaction on the dedicated connection."""

    def __init__(self, repo: "SqliteDriveRepository") -> None:
        self._repo = repo

    async def begin(self) -> None:
        await self._repo._ensure_open()
        # A read-only (immutable) sidecar open can take no write lock, so a
        # listing must use a deferred BEGIN, never BEGIN IMMEDIATE.
        stmt = "BEGIN" if self._repo._read_only else "BEGIN IMMEDIATE"
        await self._repo._run(lambda c: c.execute(stmt))

    async def commit(self) -> None:
        await self._repo._run(lambda c: c.execute("COMMIT"))

    async def rollback(self) -> None:
        await self._repo._run(self._rollback_sync)

    @staticmethod
    def _rollback_sync(conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            # No active transaction (e.g. commit already ran) — nothing to undo.
            pass

    async def apply(self, mutation: Mutation) -> None:
        await self._repo._run(lambda c: self._apply_sync(c, mutation))

    @staticmethod
    def _apply_sync(conn: sqlite3.Connection, m: Mutation) -> None:
        for r in m.drives:
            conn.execute(
                "INSERT OR REPLACE INTO drives(drive_id, blob) VALUES(?, ?)",
                (r.drive_id, _dump(pack_drive_record(r))),
            )
        for a in m.assignments:
            conn.execute(
                "INSERT OR REPLACE INTO drive_assignments(drive_id, blob) "
                "VALUES(?, ?)",
                (a.drive_id, _dump(pack_drive_assignment(a))),
            )
        for d in m.deliveries:
            conn.execute(
                "INSERT OR REPLACE INTO drive_deliveries(delivery_id, drive_id, "
                "blob) VALUES(?, ?, ?)",
                (d.delivery_id, d.drive_id, _dump(pack_drive_delivery(d))),
            )
        for au in m.audit:
            conn.execute(
                "INSERT INTO drive_audit(drive_id, blob) VALUES(?, ?)",
                (au.drive_id, _dump(pack_drive_audit(au))),
            )
        for p in m.progress:
            conn.execute(
                "INSERT INTO drive_progress(drive_id, blob) VALUES(?, ?)",
                (p.drive_id, _dump(pack_drive_progress(p))),
            )
        for e in m.outbox:
            conn.execute(
                "INSERT OR REPLACE INTO drive_outbox(outbox_id, drive_id, "
                "dispatched, blob) VALUES(?, ?, ?, ?)",
                (e.outbox_id, e.drive_id, int(e.dispatched), _dump(outbox_to_wire(e))),
            )
        for dl in m.dead_letters:
            conn.execute(
                "INSERT OR REPLACE INTO drive_dead_letters(delivery_id, drive_id, "
                "blob) VALUES(?, ?, ?)",
                (dl.delivery_id, dl.drive_id, _dump(dead_letter_to_wire(dl))),
            )
        for idem in m.idempotency:
            conn.execute(
                "INSERT OR REPLACE INTO drive_idempotency(actor, key, "
                "operation_hash, result_blob, created_at) VALUES(?, ?, ?, ?, ?)",
                (
                    idem.actor,
                    idem.key,
                    idem.operation_hash,
                    _dump(idem.result),
                    idem.created_at.isoformat(),
                ),
            )
        for prop in m.proposals:
            conn.execute(
                "INSERT OR REPLACE INTO drive_proposals(proposal_id, drive_id, blob) "
                "VALUES(?, ?, ?)",
                (
                    prop.proposal_id,
                    prop.drive_id,
                    _dump(pack_transition_proposal(prop)),
                ),
            )
        for outbox_id in m.outbox_dispatched:
            conn.execute(
                "UPDATE drive_outbox SET dispatched = 1 WHERE outbox_id = ?",
                (outbox_id,),
            )
        for delivery_id in m.deleted_deliveries:
            conn.execute(
                "DELETE FROM drive_deliveries WHERE delivery_id = ?", (delivery_id,)
            )
        for proposal_id in m.deleted_proposals:
            conn.execute(
                "DELETE FROM drive_proposals WHERE proposal_id = ?", (proposal_id,)
            )
        for drive_id in m.deleted_drives:
            # Cascade a pruned Drive across every per-drive table (R1-10); the
            # (actor,key)-keyed idempotency ledger is left intact by design.
            for table in _DRIVE_PER_DRIVE_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE drive_id = ?", (drive_id,))

    async def get_drive(self, drive_id: str) -> DriveRecord | None:
        rows = await self._repo._run(
            lambda c: c.execute(
                "SELECT blob FROM drives WHERE drive_id = ?", (drive_id,)
            ).fetchall()
        )
        return unpack_drive_record(_load(rows[0][0])) if rows else None

    async def get_assignment(self, drive_id: str) -> DriveAssignment | None:
        rows = await self._repo._run(
            lambda c: c.execute(
                "SELECT blob FROM drive_assignments WHERE drive_id = ?", (drive_id,)
            ).fetchall()
        )
        return unpack_drive_assignment(_load(rows[0][0])) if rows else None

    async def get_delivery(self, delivery_id: str) -> DriveDelivery | None:
        rows = await self._repo._run(
            lambda c: c.execute(
                "SELECT blob FROM drive_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchall()
        )
        return unpack_drive_delivery(_load(rows[0][0])) if rows else None

    async def get_idempotency(self, actor: str, key: str) -> IdempotencyRecord | None:
        rows = await self._repo._run(
            lambda c: c.execute(
                "SELECT operation_hash, result_blob, created_at FROM "
                "drive_idempotency WHERE actor = ? AND key = ?",
                (actor, key),
            ).fetchall()
        )
        if not rows:
            return None
        op_hash, result_blob, created_at = rows[0]
        return IdempotencyRecord(
            actor=actor,
            key=key,
            operation_hash=op_hash,
            result=_load(result_blob),
            created_at=parse_dt(created_at),
        )

    async def get_proposal(self, proposal_id: str) -> DriveTransitionProposal | None:
        rows = await self._repo._run(
            lambda c: c.execute(
                "SELECT blob FROM drive_proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchall()
        )
        return unpack_transition_proposal(_load(rows[0][0])) if rows else None

    async def all_proposals(self) -> list[DriveTransitionProposal]:
        rows = await self._repo._run(
            lambda c: c.execute("SELECT blob FROM drive_proposals").fetchall()
        )
        return [unpack_transition_proposal(_load(r[0])) for r in rows]

    async def all_idempotency(self) -> list[IdempotencyRecord]:
        rows = await self._repo._run(
            lambda c: c.execute(
                "SELECT actor, key, operation_hash, result_blob, created_at "
                "FROM drive_idempotency"
            ).fetchall()
        )
        return [
            IdempotencyRecord(
                actor=actor,
                key=key,
                operation_hash=op_hash,
                result=_load(result_blob),
                created_at=parse_dt(created_at),
            )
            for actor, key, op_hash, result_blob, created_at in rows
        ]

    async def all_drives(self) -> list[DriveRecord]:
        rows = await self._repo._run(
            lambda c: c.execute("SELECT blob FROM drives").fetchall()
        )
        return [unpack_drive_record(_load(r[0])) for r in rows]

    async def all_deliveries(self) -> list[DriveDelivery]:
        rows = await self._repo._run(
            lambda c: c.execute("SELECT blob FROM drive_deliveries").fetchall()
        )
        return [unpack_drive_delivery(_load(r[0])) for r in rows]

    async def all_dead_letters(self) -> list[DriveDeadLetter]:
        rows = await self._repo._run(
            lambda c: c.execute("SELECT blob FROM drive_dead_letters").fetchall()
        )
        return [dead_letter_from_wire(_load(r[0])) for r in rows]

    async def deliveries_for_drive(self, drive_id: str) -> list[DriveDelivery]:
        rows = await self._repo._run(
            lambda c: c.execute(
                "SELECT blob FROM drive_deliveries WHERE drive_id = ?", (drive_id,)
            ).fetchall()
        )
        return [unpack_drive_delivery(_load(r[0])) for r in rows]

    async def progress_for_drive(self, drive_id: str) -> list[DriveProgress]:
        rows = await self._repo._run(
            lambda c: c.execute(
                "SELECT blob FROM drive_progress WHERE drive_id = ? ORDER BY seq",
                (drive_id,),
            ).fetchall()
        )
        return [unpack_drive_progress(_load(r[0])) for r in rows]

    async def audit_for_drive(self, drive_id: str) -> list[DriveAuditRecord]:
        rows = await self._repo._run(
            lambda c: c.execute(
                "SELECT blob FROM drive_audit WHERE drive_id = ? ORDER BY seq",
                (drive_id,),
            ).fetchall()
        )
        return [unpack_drive_audit(_load(r[0])) for r in rows]

    async def outbox_entries(
        self, *, include_dispatched: bool
    ) -> list[DriveOutboxEntry]:
        sql = "SELECT blob, dispatched FROM drive_outbox"
        if not include_dispatched:
            sql += " WHERE dispatched = 0"
        sql += " ORDER BY seq"
        rows = await self._repo._run(lambda c: c.execute(sql).fetchall())
        return [
            replace(outbox_from_wire(_load(blob)), dispatched=bool(dispatched))
            for blob, dispatched in rows
        ]


class SqliteDriveRepository(BaseDriveRepository):
    """Durable Drive repository over one dedicated ``sqlite3`` connection."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        busy_timeout_ms: int = 5000,
        read_only: bool = False,
        offline_session_path=None,
    ) -> None:
        super().__init__(clock=clock, id_factory=id_factory)
        self._path = str(path)
        self._busy_timeout_ms = busy_timeout_ms
        self._read_only = read_only
        self._offline_session_path = offline_session_path
        self._snapshot: OfflineDriveSnapshot | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="drive")
        self._conn: sqlite3.Connection | None = None
        self._opened = False
        self._closed = False
        # A read-only open never creates anything, not even the parent directory.
        if self._path != ":memory:" and not read_only:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

    def _new_transaction(self) -> _SqliteDriveTransaction:
        return _SqliteDriveTransaction(self)

    @property
    def durability(self) -> str:
        return "persistent"

    async def _run(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        if self._closed:
            raise DriveRepositoryClosedError(
                "Drive repository is closed; its executor has been shut down"
            )
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._executor, lambda: fn(self._conn))
        except RuntimeError as exc:
            # A concurrent close_blocking() can shut the executor between the
            # check above and the schedule below; convert the bare executor
            # RuntimeError into the typed closed error.
            if self._closed or "after shutdown" in str(exc):
                raise DriveRepositoryClosedError(
                    "Drive repository closed while an operation was in flight"
                ) from exc
            raise

    async def _ensure_open(self) -> None:
        if self._opened:
            return
        await self._run(lambda _c: self._open_conn())
        self._opened = True

    def _open_conn(self) -> None:
        if self._read_only:
            self._open_conn_readonly()
            return
        conn = sqlite3.connect(
            self._path, check_same_thread=False, isolation_level=None
        )
        # ``busy_timeout`` is a per-connection setting (no file write), so it is
        # safe before validation. Validate a PRE-EXISTING store's schema version
        # READ-ONLY — no WAL pragma, no ``_SCHEMA`` DDL — BEFORE any mutation, so a
        # future/invalid-version file written by a newer build is rejected without
        # v1 code touching it (R1-14).
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        self._validate_existing_schema(conn)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT value FROM drive_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO drive_meta(key, value) VALUES('schema_version', ?)",
                (str(DRIVE_SCHEMA_VERSION),),
            )
        self._conn = conn

    def _open_conn_readonly(self) -> None:
        if self._offline_session_path is None:
            raise DriveConflictError("offline session path is required for saved reads")
        snapshot = open_offline_drive_snapshot(
            self._offline_session_path,
            self._path,
            busy_timeout_ms=self._busy_timeout_ms,
        )
        conn = snapshot.connection
        try:
            self._validate_existing_schema(conn)
        except (sqlite3.Error, DriveError) as exc:
            snapshot.close()
            if isinstance(exc, DriveError):
                raise
            raise _readonly_open_error(self._path, exc) from exc
        self._snapshot = snapshot
        self._conn = conn

    def _validate_existing_schema(self, conn: sqlite3.Connection) -> None:
        """Reject a pre-existing future/invalid-version store READ-ONLY (R1-14).

        Runs only reads against ``sqlite_master`` + ``drive_meta`` and closes the
        connection on rejection. A fresh file (no ``drive_meta`` table) or an
        unseeded/interrupted one (no ``schema_version`` row) is left for the
        caller to create/seed; it is never mutated here."""
        has_meta = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='drive_meta'"
        ).fetchone()
        if has_meta is None:
            return
        row = conn.execute(
            "SELECT value FROM drive_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return
        try:
            found = _parse_schema_version(row[0])
        except ValueError as exc:
            conn.close()
            raise DriveSchemaVersionError(
                f"Drive store {self._path!r} has an unparseable schema version "
                f"{row[0]!r}; refusing to open (migration required)"
            ) from exc
        if found > DRIVE_SCHEMA_VERSION:
            conn.close()
            raise DriveSchemaVersionError(
                f"Drive store {self._path!r} declares schema version {found}, but "
                f"this build supports at most {DRIVE_SCHEMA_VERSION}; a newer build "
                "wrote it (migration required)"
            )

    def close_blocking(self) -> None:
        """Close the connection + worker thread (synchronous). Idempotent.

        The handle MUST drop or a later file move/delete fails (WinError 32).
        """
        if self._closed:
            return
        self._closed = True
        conn = self._conn
        snapshot = self._snapshot
        if self._opened and conn is not None:
            try:
                closer = snapshot.close if snapshot is not None else conn.close
                self._executor.submit(closer).result()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Drive connection close failed", error=str(exc))
        self._snapshot = None
        self._conn = None
        self._executor.shutdown(wait=True)

    async def close(self) -> None:
        self.close_blocking()

    async def list_saved_rows(
        self, query: DriveQuery
    ) -> list[tuple[DriveRecord, DriveAssignment | None]]:
        try:
            async with self._txn() as txn:
                pairs = []
                for record in await txn.all_drives():
                    assignment = await txn.get_assignment(record.drive_id)
                    if matches_query(query, record, assignment):
                        pairs.append((record, assignment))
                pairs.sort(key=lambda ra: (ra[0].created_at, ra[0].drive_id))
                return pairs[: query.limit] if query.limit is not None else pairs
        except (
            sqlite3.Error,
            json.JSONDecodeError,
            UnicodeError,
            TypeError,
            ValueError,
            KeyError,
            AttributeError,
        ) as exc:
            if isinstance(exc, DriveError):
                raise
            if isinstance(exc, sqlite3.Error):
                raise _readonly_open_error(self._path, exc) from exc
            raise DriveError(
                f"Drive sidecar {self._path!r} contains an invalid saved row: {exc}"
            ) from exc


def _readonly_open_error(path: str, exc: sqlite3.Error) -> DriveError:
    message = str(exc).lower()
    if isinstance(exc, sqlite3.OperationalError) and any(
        token in message for token in ("locked", "busy", "readonly", "read-only")
    ):
        return DriveConflictError(
            f"Drive sidecar {path!r} is held by a live writer; not readable offline"
        )
    return DriveError(f"Drive sidecar {path!r} could not be read offline: {exc}")


def open_drive_repository_readonly(sidecar_path, *, session_path):
    return SqliteDriveRepository(
        sidecar_path,
        read_only=True,
        busy_timeout_ms=_READONLY_BUSY_TIMEOUT_MS,
        offline_session_path=session_path,
    )


def open_session_drive_repository(
    session_store: Any, *, read_only: bool = False
) -> SqliteDriveRepository:
    """Open a Drive repository over a session store's sidecar and wire its closer.

    Terrarium opens the ``sqlite3`` connection against a dedicated sidecar file
    (:func:`drive_sidecar_path`), NOT the ``.kohakutr`` itself, so Drive writes
    never contend with KohakuVault on one file's lock. Legacy same-file sessions
    migrate into the sidecar once; the repo's ``close_blocking`` is registered via
    ``register_companion_closer`` so ``close()`` drops the handle before any file
    move (WinError 32). The dependency points downward — terrarium -> session.
    """
    sidecar = drive_sidecar_path(session_store.path)
    if read_only:
        return SqliteDriveRepository(sidecar, read_only=True)
    _migrate_same_file_drives(str(session_store.path), sidecar)
    repo = SqliteDriveRepository(sidecar)
    session_store.register_companion_closer(repo.close_blocking)
    return repo
