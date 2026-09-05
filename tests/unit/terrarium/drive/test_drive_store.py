"""Persistence, crash-atomicity, and concurrency for the SQLite Drive store.

These exercise what only the durable backend can: close/reopen recovery,
all-or-nothing commit across a simulated crash, lazy table creation on a file
with no Drive tables, real-task CAS races, the Windows file-handle release
(``os.replace`` after close), and the SessionStore attach seam. The
backend-agnostic behaviour lives in ``test_drive_repository.py``.
"""

import asyncio
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from pathlib import Path

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.drive import store_migration
from kohakuterrarium.terrarium.drive.errors import (
    DriveConflictError,
    DriveSchemaVersionError,
    DriveStorageError,
)
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.repository import Mutation, build_create
from kohakuterrarium.terrarium.drive.requests import (
    CreateDriveRequest,
    DrivePatch,
    DriveQuery,
)
from kohakuterrarium.terrarium.drive.store import (
    DRIVE_SCHEMA_VERSION,
    DriveRepositoryClosedError,
    SqliteDriveRepository,
    open_session_drive_repository,
)

WORKER = ActorRef("creature", "worker")


def _ids(start=0):
    n = [start]

    def mint() -> str:
        n[0] += 1
        return f"id{n[0]:05d}"

    return mint


def _open(path, id_start=0):
    return SqliteDriveRepository(str(path), id_factory=_ids(id_start))


def _req(**over) -> CreateDriveRequest:
    base = dict(
        kind="generic",
        title="watch",
        scope_type="creature",
        scope_id="worker",
        owner=WORKER,
        owner_scope="creature",
        created_by=WORKER,
    )
    base.update(over)
    return CreateDriveRequest(**base)


# ── fail-first: op after close is a typed error, not a leaked RuntimeError ──


class TestClosedRepositoryFailsTyped:
    async def test_op_after_close_raises_typed_drive_error(self, tmp_path):
        # A late dispatcher/reconcile call after close_blocking() must surface a
        # typed DriveError, never a bare ``RuntimeError: cannot schedule new
        # futures after shutdown`` leaking from the executor.
        repo = _open(tmp_path / "d.kohakutr")
        await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        repo.close_blocking()
        with pytest.raises(DriveRepositoryClosedError):
            await repo.get("id00001")
        with pytest.raises(DriveRepositoryClosedError):
            async with repo.transaction() as txn:
                await txn.all_drives()

    async def test_closed_error_is_a_drive_error(self):
        from kohakuterrarium.terrarium.drive.errors import DriveError

        assert issubclass(DriveRepositoryClosedError, DriveError)


# ── close / reopen recovery ───────────────────────────────────────


class TestReopen:
    async def test_reopen_preserves_state(self, tmp_path):
        path = tmp_path / "d.sqlite"
        repo = _open(path)
        rec = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        await repo.update_drive(
            rec.drive_id, DrivePatch(title="t2"), expected_revision=1, actor=WORKER
        )
        await repo.enqueue_delivery(rec.drive_id, reason="ready")
        d = await repo.enqueue_delivery(rec.drive_id, reason="retry")
        await repo.mark_delivery(d.delivery_id, "retry_wait", error="x")
        repo.close_blocking()

        repo2 = _open(path, id_start=100)
        try:
            got = await repo2.get(rec.drive_id)
            assert got.revision == 2 and got.title == "t2"
            # audit order intact across reopen (create then update)
            assert [a.operation for a in await repo2.list_audit(rec.drive_id)] == [
                "create",
                "update",
            ]
            deliveries = await repo2.list_deliveries(rec.drive_id)
            states = sorted(x.state for x in deliveries)
            assert states == ["pending", "retry_wait"]
            attempts = {x.reason: x.attempt for x in deliveries}
            assert attempts["retry"] == 1
        finally:
            repo2.close_blocking()

    async def test_idempotency_survives_reopen(self, tmp_path):
        path = tmp_path / "d.sqlite"
        repo = _open(path)
        rec = await repo.create_drive(
            _req(idempotency_key="k1"), actor=WORKER, graph_id="g1"
        )
        repo.close_blocking()

        repo2 = _open(path, id_start=100)
        try:
            # same key replays the original result after reopen — no 2nd drive
            again = await repo2.create_drive(
                _req(idempotency_key="k1"), actor=WORKER, graph_id="g1"
            )
            assert again.drive_id == rec.drive_id

            assert len(await repo2.list_drives(DriveQuery())) == 1
        finally:
            repo2.close_blocking()

    async def test_schema_version_recorded(self, tmp_path):
        path = tmp_path / "d.sqlite"
        repo = _open(path)
        await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        repo.close_blocking()
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute(
                "SELECT value FROM drive_meta WHERE key = 'schema_version'"
            ).fetchone()
            assert int(row[0]) == DRIVE_SCHEMA_VERSION
        finally:
            conn.close()


# ── schema-version enforcement (R1-14) ────────────────────────────


class TestSchemaVersionEnforcement:
    async def test_future_schema_version_rejected_before_write(self, tmp_path):
        path = tmp_path / "d.sqlite"
        repo = _open(path)
        await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        repo.close_blocking()
        conn = sqlite3.connect(str(path))
        conn.execute(
            "UPDATE drive_meta SET value = ? WHERE key = 'schema_version'",
            (str(DRIVE_SCHEMA_VERSION + 5),),
        )
        conn.commit()
        conn.close()

        repo2 = _open(path)
        try:
            with pytest.raises(DriveSchemaVersionError):
                await repo2.get("id00001")
        finally:
            repo2.close_blocking()

    async def test_non_integer_schema_version_rejected(self, tmp_path):
        path = tmp_path / "d.sqlite"
        repo = _open(path)
        await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        repo.close_blocking()
        conn = sqlite3.connect(str(path))
        conn.execute(
            "UPDATE drive_meta SET value = 'not-a-number' WHERE key = 'schema_version'"
        )
        conn.commit()
        conn.close()

        repo2 = _open(path)
        try:
            with pytest.raises(DriveSchemaVersionError):
                await repo2.get("id00001")
        finally:
            repo2.close_blocking()

    async def test_current_schema_version_opens(self, tmp_path):
        path = tmp_path / "d.sqlite"
        repo = _open(path)
        record = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        repo.close_blocking()
        repo2 = _open(path, id_start=100)
        try:
            assert await repo2.get(record.drive_id) is not None
        finally:
            repo2.close_blocking()

    async def test_future_schema_not_mutated_before_rejection(self, tmp_path):
        # R1-14: validation runs READ-ONLY before any mutation, so a rejected
        # future-version store is not flipped to WAL (nor otherwise touched).
        path = tmp_path / "d.sqlite"
        repo = _open(path)
        await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        repo.close_blocking()
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode = DELETE")  # non-WAL, observable if flipped
        conn.execute(
            "UPDATE drive_meta SET value = ? WHERE key = 'schema_version'",
            (str(DRIVE_SCHEMA_VERSION + 5),),
        )
        conn.commit()
        conn.close()

        repo2 = _open(path)
        with pytest.raises(DriveSchemaVersionError):
            await repo2.get("id00001")
        repo2.close_blocking()

        conn = sqlite3.connect(str(path))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "delete", "rejected open must not have flipped to WAL (R1-14)"

    async def test_malformed_sidecar_marker_rejected_not_quarantined(self, tmp_path):
        # A sidecar carrying a PRESENT-but-invalid schema marker must be REJECTED
        # read-only (typed error) BEFORE any quarantine/rebuild side effect: the
        # active file is left byte-for-byte intact and no .corrupt is created. A
        # malformed marker misclassified as "incomplete" would be destroyed here,
        # before the repository's read-only schema validation ever runs (R1-14).
        from kohakuterrarium.terrarium.drive.store_migration import (
            _migrate_same_file_drives,
        )

        path = tmp_path / "legacy.kohakutr"
        store = SessionStore(path, writer_lock=True)
        store.init_meta("sid", "agent", "cfg.yaml", str(tmp_path), ["worker"])
        legacy = SqliteDriveRepository(str(path))
        await legacy.create_drive(_req(), actor=WORKER, graph_id="g1")
        legacy.close_blocking()
        store.close()

        sidecar = str(path) + ".drives"
        conn = sqlite3.connect(sidecar)
        conn.execute("CREATE TABLE drive_meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO drive_meta(key, value) VALUES('schema_version', 'not-a-number')"
        )
        conn.commit()
        conn.close()

        before = Path(sidecar).read_bytes()
        before_mtime = os.stat(sidecar).st_mtime_ns

        with pytest.raises(DriveSchemaVersionError):
            _migrate_same_file_drives(str(path), sidecar)

        assert Path(sidecar).read_bytes() == before  # active file untouched
        assert os.stat(sidecar).st_mtime_ns == before_mtime
        assert not Path(sidecar + ".corrupt").exists()  # never quarantined

    async def test_interrupted_migration_destination_is_rebuilt(self, tmp_path):
        # A zero-byte / interrupted sidecar must NOT suppress migration from the
        # intact legacy same-file Drive rows: the destination is treated complete
        # only after its schema/completion marker validates (R1-14).
        path = tmp_path / "legacy.kohakutr"
        store = SessionStore(path, writer_lock=True)
        store.init_meta("sid", "agent", "cfg.yaml", str(tmp_path), ["worker"])
        legacy = SqliteDriveRepository(str(path))
        rec = await legacy.create_drive(_req(), actor=WORKER, graph_id="g1")
        legacy.close_blocking()
        store.close()

        sidecar = str(path) + ".drives"
        Path(sidecar).write_bytes(b"")  # interrupted/zero-byte destination

        store2 = SessionStore(path)
        try:
            repo = open_session_drive_repository(store2)
            got = await repo.get(rec.drive_id)
            assert got is not None and got.title == "watch"
        finally:
            store2.close()


# ── crash atomicity (all-or-nothing across a simulated crash) ─────


class TestCrashAtomicity:
    async def test_materialized_row_without_outbox_rolls_back(self, tmp_path):
        path = tmp_path / "d.sqlite"
        repo = _open(path)
        rec = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        # stage ONLY a materialized drive row (no audit/outbox) then crash.
        with pytest.raises(RuntimeError):
            async with repo.transaction() as txn:
                await txn.apply(
                    Mutation(
                        drives=[
                            type(rec)(
                                **{**rec.__dict__, "title": "GHOST", "revision": 2}
                            )
                        ]
                    )
                )
                raise RuntimeError("crash after materialized row, before outbox")
        repo.close_blocking()

        repo2 = _open(path, id_start=100)
        try:
            got = await repo2.get(rec.drive_id)
            assert got.title == "watch" and got.revision == 1  # nothing committed
        finally:
            repo2.close_blocking()

    async def test_full_mutation_before_commit_rolls_back(self, tmp_path):
        path = tmp_path / "d.sqlite"
        repo = _open(path)
        await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        # stage a COMPLETE create (drive+assignment+audit+outbox) then crash
        # before commit — reopen must show none of it.
        with pytest.raises(RuntimeError):
            async with repo.transaction() as txn:
                mutation, ghost = build_create(
                    _req(title="ghost"),
                    actor=WORKER,
                    graph_id="g1",
                    status=DriveStatus.ACTIVE,
                    now=repo._clock(),
                    mint=repo._mint,
                )
                await txn.apply(mutation)
                raise RuntimeError("crash after outbox construction, before commit")
        ghost_id = ghost.drive_id
        repo.close_blocking()

        repo2 = _open(path, id_start=200)
        try:

            assert await repo2.get(ghost_id) is None
            # only the original create survived
            assert len(await repo2.list_drives(DriveQuery())) == 1
        finally:
            repo2.close_blocking()


# ── concurrency (real tasks, real connection) ─────────────────────


class TestConcurrency:
    async def test_concurrent_update_one_loser(self, tmp_path):
        repo = _open(tmp_path / "d.sqlite")
        try:
            rec = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")

            async def upd():
                return await repo.update_drive(
                    rec.drive_id,
                    DrivePatch(title="c"),
                    expected_revision=1,
                    actor=WORKER,
                )

            results = await asyncio.gather(upd(), upd(), upd(), return_exceptions=True)
            losers = [r for r in results if isinstance(r, DriveConflictError)]
            winners = [r for r in results if not isinstance(r, Exception)]
            assert len(winners) == 1 and len(losers) == 2
            assert (await repo.get(rec.drive_id)).revision == 2
        finally:
            repo.close_blocking()


# ── lazy table creation on a pre-existing file ────────────────────


class TestLazyTables:
    async def test_opens_file_without_drive_tables(self, tmp_path):
        path = tmp_path / "legacy.sqlite"
        # simulate a pre-existing session file: real tables, NO drive_* tables.
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE legacy (k TEXT)")
        conn.execute("INSERT INTO legacy VALUES ('keep-me')")
        conn.commit()
        conn.close()

        repo = _open(path)
        try:
            rec = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
            assert rec.revision == 1
        finally:
            repo.close_blocking()

        # the pre-existing table is untouched alongside the new drive_* tables.
        conn = sqlite3.connect(str(path))
        try:
            assert conn.execute("SELECT k FROM legacy").fetchone()[0] == "keep-me"
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert {"drives", "drive_audit", "drive_outbox"} <= names
        finally:
            conn.close()


# ── Windows file-handle release (WinError 32 regression) ──────────


class TestFileHandleRelease:
    async def test_file_movable_after_close(self, tmp_path):
        path = tmp_path / "d.sqlite"
        repo = _open(path)
        await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        repo.close_blocking()
        # an open Drive connection would keep the file locked on Windows.
        moved = str(path) + ".moved"
        os.replace(str(path), moved)
        assert os.path.exists(moved)

    async def test_close_idempotent(self, tmp_path):
        repo = _open(tmp_path / "d.sqlite")
        await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        repo.close_blocking()
        repo.close_blocking()  # second close is a no-op


# ── SessionStore attach seam (terrarium -> session, never up) ──────


class TestAttachToSessionStore:
    async def test_attach_opens_and_closes_with_store(self, tmp_path):
        # Terrarium opens the Drive repo over a dedicated sidecar file paired
        # with the session and registers its closer; store.close() drains BOTH
        # so each file is movable (WinError 32 regression). A session move must
        # carry its sidecar for the Drive rows to survive.
        path = tmp_path / "s.kohakutr"
        sidecar = str(path) + ".drives"
        store = SessionStore(path, writer_lock=True)
        store.init_meta("sid", "agent", "cfg.yaml", str(tmp_path), ["worker"])
        store.append_event("worker", "user_message", {"content": "hello there world"})
        repo = open_session_drive_repository(store)
        rec = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        store.close()

        # The Drive rows live in a SEPARATE sidecar file, not the .kohakutr.
        assert os.path.exists(sidecar)

        moved = str(path) + ".moved"
        moved_sidecar = moved + ".drives"
        os.replace(str(path), moved)  # WinError 32 if the .kohakutr handle leaked
        os.replace(sidecar, moved_sidecar)  # ...or the sidecar handle leaked
        assert os.path.exists(moved) and os.path.exists(moved_sidecar)

        store2 = SessionStore(moved)
        try:
            repo2 = open_session_drive_repository(store2)
            got = await repo2.get(rec.drive_id)
            assert got is not None and got.title == "watch"
            assert len(store2.get_events("worker")) == 1  # KVault side intact
        finally:
            store2.close()

    async def test_attach_old_file_without_drive_tables(self, tmp_path):
        # A session created before Drives existed gains the sidecar tables on
        # first attach (additive; no session format bump).
        path = tmp_path / "old.kohakutr"
        s = SessionStore(path)
        s.init_meta("sid", "agent", "cfg.yaml", str(tmp_path), ["w"])
        s.close()  # never touched the Drive runtime

        s2 = SessionStore(path)
        try:
            rec = await open_session_drive_repository(s2).create_drive(
                _req(), actor=WORKER, graph_id="g1"
            )
            assert rec.revision == 1
        finally:
            s2.close()

    async def test_attach_migrates_legacy_same_file_drives(self, tmp_path):
        # A pre-sidecar session holds drive_* rows INSIDE the .kohakutr. First
        # attach migrates them once into the sidecar and serves them from there.
        path = tmp_path / "legacy.kohakutr"
        store = SessionStore(path, writer_lock=True)
        store.init_meta("sid", "agent", "cfg.yaml", str(tmp_path), ["worker"])
        legacy = SqliteDriveRepository(str(path))  # old same-file layout
        rec = await legacy.create_drive(_req(), actor=WORKER, graph_id="g1")
        legacy.close_blocking()
        store.close()

        sidecar = str(path) + ".drives"
        assert not os.path.exists(sidecar)

        store2 = SessionStore(path)
        try:
            repo = open_session_drive_repository(store2)
            assert os.path.exists(sidecar)  # migration created the sidecar
            assert not os.path.exists(sidecar + ".migrating")  # temp cleaned up
            got = await repo.get(rec.drive_id)
            assert got is not None and got.title == "watch"
        finally:
            store2.close()

        # Idempotent: a later attach sees the sidecar and does not re-migrate.
        store3 = SessionStore(path)
        try:
            again = await open_session_drive_repository(store3).get(rec.drive_id)
            assert again is not None and again.title == "watch"
        finally:
            store3.close()

    def test_legacy_probe_leaves_live_writer_wal_intact(self, tmp_path):
        # The probe runs while kohakuvault holds live connections; a CPython
        # SQLite open of the same file must not touch its locks or WAL/SHM
        # (POSIX: same-pid fcntl locks never conflict, so a non-immutable
        # close checkpoints + unlinks them under the writer). A fresh reader
        # after the probe must still see every write.
        path = tmp_path / "live.kohakutr"
        store = SessionStore(path, writer_lock=True)
        try:
            store.init_meta("sid", "agent", "cfg.yaml", str(tmp_path), ["worker"])
            for i in range(3):
                store.append_event("worker", "user_message", {"content": f"m{i}"})
            store.flush()

            assert store_migration._legacy_same_file_drives(str(path)) is False

            for i in range(3, 6):
                store.append_event("worker", "user_message", {"content": f"m{i}"})
            store.flush()

            reader = SessionStore(path)
            try:
                assert len(reader.get_events("worker")) == 6
            finally:
                reader.close(update_status=False)
        finally:
            store.close()

    async def test_sidecar_isolates_drive_and_session_writes(self, tmp_path):
        # Steady-state (32d/32g root cause): concurrent Drive writes and session
        # conversation appends must not share one sqlite lock. The sidecar makes
        # them SEPARATE files — the structural guarantee — and the interleaved
        # load proves both sides land intact with no starvation.
        path = tmp_path / "s.kohakutr"
        store = SessionStore(path, writer_lock=True)
        store.init_meta("sid", "agent", "cfg.yaml", str(tmp_path), ["worker"])
        repo = open_session_drive_repository(store)

        assert repo._path == str(path) + ".drives"  # sidecar, not the .kohakutr
        assert repo._path != str(path)

        async def _drive_writes():
            ids = []
            for _ in range(20):
                rec = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
                ids.append(rec.drive_id)
            return ids

        async def _session_writes():
            for i in range(20):
                await asyncio.to_thread(
                    store.append_event, "worker", "user_message", {"content": f"m{i}"}
                )

        try:
            drive_ids, _ = await asyncio.gather(_drive_writes(), _session_writes())
            assert len(drive_ids) == 20
            for did in drive_ids:
                assert await repo.get(did) is not None  # every Drive write landed
            assert len(store.get_events("worker")) == 20  # every append landed
        finally:
            repo.close_blocking()
            store.close()


# ── concurrent legacy-migration race (R1-14) ──────────────────────


class TestConcurrentMigration:
    def test_racing_opens_migrate_exactly_once(self, tmp_path):
        # R1-14: two+ opens racing the same legacy migration must not collide on
        # a shared temp. A unique temp + inter-process lock makes exactly one
        # migrator win; the rest see the completed sidecar. No errors, one
        # sidecar with every legacy row, no leftover temp/lock files.
        import threading

        from kohakuterrarium.terrarium.drive.store_migration import (
            _migrate_same_file_drives,
        )

        path = tmp_path / "legacy.kohakutr"
        store = SessionStore(path, writer_lock=True)
        store.init_meta("sid", "agent", "cfg.yaml", str(tmp_path), ["worker"])
        legacy = SqliteDriveRepository(str(path))

        async def _seed():
            for _ in range(4):
                await legacy.create_drive(_req(), actor=WORKER, graph_id="g1")

        asyncio.run(_seed())
        legacy.close_blocking()
        store.close()

        sidecar = str(path) + ".drives"
        assert not Path(sidecar).exists()

        n = 8
        barrier = threading.Barrier(n)
        errors: list[Exception] = []

        def race() -> None:
            try:
                barrier.wait()
                _migrate_same_file_drives(str(path), sidecar)
            except Exception as exc:  # noqa: BLE001 — collected for the assert
                errors.append(exc)

        threads = [threading.Thread(target=race) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"racing migration raised: {errors!r}"
        assert Path(sidecar).exists()
        conn = sqlite3.connect(sidecar)
        try:
            drive_count = conn.execute("SELECT COUNT(*) FROM drives").fetchone()[0]
            version = conn.execute(
                "SELECT value FROM drive_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert drive_count == 4  # every legacy row copied exactly once
        assert int(version) == DRIVE_SCHEMA_VERSION
        # No leftover per-attempt temp file. The OS-lock sidecar file is left in
        # place on purpose (unlinking it would reintroduce the takeover race).
        assert list(Path(tmp_path).glob("legacy.kohakutr.drives.migrating.*")) == []
        assert Path(sidecar + ".migrate-lock").exists()


# ── OS-backed migration lock held for the whole migration (R1-14) ──


class TestMigrationLockOwnership:
    """R1-14: the migration lock is an OS-level exclusive lock (fcntl.flock /
    msvcrt.locking) held via an open handle for the entire migration. Mutual
    exclusion is enforced by the kernel and release happens on handle close (or
    process death), so there is no pathname-unlink stale takeover for a
    replacement / unstamped lock file to race (round-3b races (a) and (b))."""

    def test_migration_lock_holds_os_lock_for_its_body(self, tmp_path):
        # The reviewer's core demand: the lock is genuinely held by the OS for the
        # whole critical section — an independent acquirer of the same lock path is
        # refused, so no second migrator can enter concurrently. (Fails on the old
        # scheme, which closed its fd immediately and held nothing.)
        from kohakuterrarium.terrarium.drive import store_migration as sm
        from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy

        sidecar = str(tmp_path / "s.kohakutr.drives")
        lock_path = sidecar + ".migrate-lock"
        with sm._migration_lock(sidecar) as acquired:
            assert acquired is True
            with pytest.raises(FileLockBusy):
                FileLock(lock_path).acquire()

    def test_live_holders_lock_is_never_deleted_by_a_waiter(
        self, tmp_path, monkeypatch
    ):
        # Round-3b races (a)/(b) both ended in a foreign lock being DELETED. With
        # an OS lock held via a live handle a contending acquirer can neither break
        # nor delete it: it times out, and the holder's lock file stays intact. The
        # holder writes an unstamped (no-token) lock — exactly the shape the old
        # code misclassified stale-by-empty-token and unlinked out from under it.
        from kohakuterrarium.terrarium.drive import store_migration as sm
        from kohakuterrarium.utils.file_lock import FileLock

        monkeypatch.setattr(sm, "_MIGRATE_LOCK_TIMEOUT_S", 0.05)
        monkeypatch.setattr(sm, "_MIGRATE_POLL_S", 0.001)
        sidecar = str(tmp_path / "s.kohakutr.drives")  # no sidecar → not complete
        lock_path = Path(sidecar + ".migrate-lock")
        holder = FileLock(lock_path)
        holder.acquire()
        try:
            with pytest.raises(TimeoutError):
                with sm._migration_lock(sidecar):
                    pass  # a live OS lock is never broken, so acquire never happens
            assert lock_path.exists()  # the live holder's lock was never deleted
            assert holder.held is True
        finally:
            holder.release()

    def test_migration_lock_release_does_not_unlink(self, tmp_path):
        # No unlink on release → no unlink-based takeover to race. The OS lock, not
        # the file's existence, is the token, so a leftover file is harmless. (The
        # old release unlinked its own token, the race window (a) exploited.)
        from kohakuterrarium.terrarium.drive import store_migration as sm

        sidecar = str(tmp_path / "s.kohakutr.drives")
        lock_path = Path(sidecar + ".migrate-lock")
        with sm._migration_lock(sidecar) as acquired:
            assert acquired is True
            assert lock_path.exists()
        assert lock_path.exists()  # released the OS lock but kept the file

    def test_freed_lock_is_reacquired(self, tmp_path):
        # Handle close frees the OS lock so a subsequent acquirer succeeds on the
        # leftover file (no unlink needed).
        from kohakuterrarium.terrarium.drive import store_migration as sm
        from kohakuterrarium.utils.file_lock import FileLock

        sidecar = str(tmp_path / "s.kohakutr.drives")  # no sidecar → not complete
        lock_path = Path(sidecar + ".migrate-lock")
        holder = FileLock(lock_path)
        holder.acquire()
        holder.release()  # holder gone; OS lock freed, file left behind
        with sm._migration_lock(sidecar) as acquired:
            assert acquired is True  # the freed lock was reacquired in place

    def test_os_lock_released_on_holder_process_death(self, tmp_path):
        # The OS-backed guarantee: a lock held by a PROCESS is freed by the kernel
        # when that process dies, so a later acquirer succeeds with no unlink-based
        # takeover. This is exactly what the round-3b unlink scheme could not do
        # safely.
        from kohakuterrarium.terrarium.drive import store_migration as sm
        from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy

        sidecar = str(tmp_path / "s.kohakutr.drives")
        lock_path = sidecar + ".migrate-lock"
        code = (
            "import sys, time\n"
            "from kohakuterrarium.utils.file_lock import FileLock\n"
            f"lk = FileLock({lock_path!r})\n"
            "lk.acquire()\n"
            "sys.stdout.write('locked\\n'); sys.stdout.flush()\n"
            "time.sleep(60)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True
        )
        try:
            assert proc.stdout.readline().strip() == "locked"  # child holds the lock
            with pytest.raises(FileLockBusy):
                FileLock(lock_path).acquire()  # kernel refuses while child holds it
        finally:
            proc.kill()
            proc.wait()  # reap so the OS has torn the lock down before we re-acquire
        with sm._migration_lock(sidecar) as acquired:
            assert acquired is True  # the dead child's lock was freed by the OS


# ── strict timeout bound: deadline enforced before the slow probe (R1-14) ──


class TestMigrationLockTimeoutBound:
    def test_slow_probe_cannot_exceed_fifty_ms_deadline(self, tmp_path, monkeypatch):
        from kohakuterrarium.terrarium.drive import store_migration as sm
        from kohakuterrarium.utils.file_lock import FileLock

        def slow_probe(_path, *, timeout_s=None):
            time.sleep(0.5 if timeout_s is None else timeout_s)
            return False

        monkeypatch.setattr(sm, "_sidecar_is_complete", slow_probe)
        monkeypatch.setattr(sm, "_MIGRATE_LOCK_TIMEOUT_S", 0.05)
        sidecar = str(tmp_path / "bounded.kohakutr.drives")
        holder = FileLock(sidecar + ".migrate-lock")
        holder.acquire()
        try:
            start = time.monotonic()
            with pytest.raises(TimeoutError):
                with sm._migration_lock(sidecar):
                    pass
            assert time.monotonic() - start < 0.4
        finally:
            holder.release()

    def test_deadline_enforced_before_slow_completeness_probe(
        self, tmp_path, monkeypatch
    ):
        # Round-3c (a): _MIGRATE_LOCK_TIMEOUT_S must be a STRICT upper bound on
        # how long acquisition blocks. A slow SQLite completeness probe cannot be
        # allowed to overshoot it: the deadline is checked BEFORE the probe, so
        # once the budget is spent the waiter fails loudly WITHOUT probing again.
        # (Fails on the pre-fix order, which ran the slow probe first and only
        # then noticed the deadline — a single probe overshoots the whole
        # timeout.)
        from kohakuterrarium.terrarium.drive import store_migration as sm
        from kohakuterrarium.utils.file_lock import FileLock

        probe_calls: list[float] = []

        def slow_probe(_path, *, timeout_s=None):
            probe_calls.append(time.monotonic())
            time.sleep(0.5 if timeout_s is None else timeout_s)
            return False

        monkeypatch.setattr(sm, "_sidecar_is_complete", slow_probe)
        monkeypatch.setattr(sm, "_MIGRATE_LOCK_TIMEOUT_S", 0.0)
        monkeypatch.setattr(sm, "_MIGRATE_POLL_S", 0.001)

        sidecar = str(tmp_path / "s.kohakutr.drives")
        holder = FileLock(sidecar + ".migrate-lock")
        holder.acquire()  # keep the lock busy so acquire always contends
        try:
            start = time.monotonic()
            with pytest.raises(TimeoutError):
                with sm._migration_lock(sidecar):
                    pass
            elapsed = time.monotonic() - start
            assert probe_calls == []  # deadline enforced BEFORE the slow probe
            assert elapsed < 0.4  # bounded well under one 0.5s probe
        finally:
            holder.release()


class TestAbandonedMigrationTempSweep:
    def test_next_migration_sweeps_orphan_temp_family(self, tmp_path):
        from kohakuterrarium.terrarium.drive import store_migration as sm

        path = tmp_path / "legacy.kohakutr"
        sidecar = str(path) + ".drives"
        attempt = sidecar + ".migrating." + ("a" * 32)
        orphans = [
            Path(attempt + suffix) for suffix in ("", "-journal", "-wal", "-shm")
        ]
        for orphan in orphans:
            orphan.write_bytes(b"killed process")

        sm._migrate_same_file_drives(str(path), sidecar)

        assert all(not orphan.exists() for orphan in orphans)
        assert Path(sidecar + ".migrate-lock").exists()


# ── failed migration leaves no temp artifact (R1-14) ──────────────


class TestMigrationTempCleanup:
    async def test_failed_migration_leaves_no_migrating_temp(
        self, tmp_path, monkeypatch
    ):
        # Round-3c (b): a migration that fails mid-way (here the atomic rename
        # raises) must clean up its own per-attempt temp DB, stranding no
        # `.migrating.<uuid>` artifact. (Fails pre-fix: the fully built temp was
        # left on disk when the rename raised.)
        from kohakuterrarium.terrarium.drive import store_migration as sm

        path = tmp_path / "legacy.kohakutr"
        store = SessionStore(path, writer_lock=True)
        store.init_meta("sid", "agent", "cfg.yaml", str(tmp_path), ["worker"])
        legacy = SqliteDriveRepository(str(path))
        await legacy.create_drive(_req(), actor=WORKER, graph_id="g1")
        legacy.close_blocking()
        store.close()

        sidecar = str(path) + ".drives"

        def boom(self, target, *, _real=Path.replace):
            if ".migrating." in self.name:  # only the temp -> sidecar rename
                raise OSError(13, "simulated crash mid-migration")
            return _real(self, target)

        monkeypatch.setattr(Path, "replace", boom)

        with pytest.raises(OSError):
            sm._migrate_same_file_drives(str(path), sidecar)

        assert not Path(sidecar).exists()  # the rename never landed
        leftovers = list(Path(tmp_path).glob("legacy.kohakutr.drives.migrating.*"))
        assert leftovers == []  # the failed migration cleaned up its own temp


# ── lock-file lifecycle: survives session/sidecar teardown (R1-14) ─


class TestMigrationLockFileLifecycle:
    def test_migrate_lock_survives_sidecar_family_teardown(self, tmp_path, monkeypatch):
        # Round-3c (c): the persistent OS-lock file
        # `<name>.kohakutr.drives.migrate-lock` must NOT be swept up by the
        # session/sidecar teardown path — unlinking its pathname is the
        # split-inode hazard. It is absent from the canonical sidecar family and
        # survives a real delete_session_files; the OS lock still works after.
        from kohakuterrarium.studio.persistence import store as persistence_store
        from kohakuterrarium.terrarium.drive.store_migration import (
            drive_sidecar_family,
        )
        from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy

        session = tmp_path / "alice.kohakutr"
        store = SessionStore(str(session))
        store.init_meta("sid", "agent", "cfg.yaml", str(tmp_path), ["worker"])
        store.append_event("worker", "user_message", {"content": "hi"})
        store.flush()
        store.close()

        sidecar = str(session) + ".drives"
        Path(sidecar).write_bytes(b"drive rows")
        lock_path = Path(sidecar + ".migrate-lock")
        holder = FileLock(lock_path)  # a real held-then-freed lock file
        holder.acquire()
        holder.release()
        assert lock_path.exists()

        # The canonical family the teardown is built on never names the lock.
        family_names = {Path(p).name for p in drive_sidecar_family(session)}
        assert lock_path.name not in family_names

        monkeypatch.setattr(persistence_store, "_SESSION_DIR", tmp_path)
        persistence_store.delete_session_files("alice")

        assert not session.exists()  # session removed
        assert not Path(sidecar).exists()  # .drives family swept
        assert lock_path.exists()  # ...but the OS-lock file survives

        # OS-lock invariants from round-3c still hold on the surviving file.
        lk = FileLock(lock_path)
        lk.acquire()
        try:
            with pytest.raises(FileLockBusy):
                FileLock(lock_path).acquire()
        finally:
            lk.release()


# ── cancellation must never poison the connection ───────────────────


class TestCancellationSafety:
    async def test_cancel_during_begin_rolls_back_and_next_txn_works(self, tmp_path):
        # A task cancelled while BEGIN is in flight used to leave the
        # transaction open forever, so every later call failed with
        # "cannot start a transaction within a transaction".
        repo = _open(tmp_path / "s.kohakutr.drives")
        await repo._ensure_open()
        orig_run = repo._run

        async def slow_run(fn):
            def wrapped(conn):
                time.sleep(0.2)
                return fn(conn)

            return await orig_run(wrapped)

        repo._run = slow_run
        victim = asyncio.ensure_future(repo.list_drives(DriveQuery()))
        await asyncio.sleep(0.05)
        victim.cancel()
        with pytest.raises(asyncio.CancelledError):
            await victim
        repo._run = orig_run
        assert repo._conn.in_transaction is False
        record = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        assert (await repo.get(record.drive_id)) is not None
        await repo.close()

    async def test_stale_open_transaction_is_healed_on_next_begin(self, tmp_path):
        # The connection state is authoritative: a BEGIN nobody rolled back
        # (a cancelled rollback, an older build) is rolled back before the
        # next transaction starts instead of failing every call afterwards.
        repo = _open(tmp_path / "s.kohakutr.drives")
        await repo._ensure_open()
        repo._conn.execute("BEGIN IMMEDIATE")
        assert repo._conn.in_transaction is True
        record = await repo.create_drive(_req(), actor=WORKER, graph_id="g1")
        assert (await repo.get(record.drive_id)) is not None
        assert repo._conn.in_transaction is False
        await repo.close()

    async def test_sqlite_failure_is_a_typed_storage_error(self, tmp_path):
        repo = _open(tmp_path / "s.kohakutr.drives")
        await repo._ensure_open()
        repo._conn.close()
        with pytest.raises(DriveStorageError):
            await repo.list_drives(DriveQuery())
        await repo.close()
