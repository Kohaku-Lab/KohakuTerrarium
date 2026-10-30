"""Studio delete occupancy around resolved session versions."""

import asyncio
import os
import threading
import time
from pathlib import Path

import pytest

from kohakuterrarium.errors import SessionLockedError
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_LEGACY_ENROLLED,
    REASON_LEGACY_PENDING,
    REASON_OK,
    ResourceReleaseError,
    acquire_legacy,
    try_fast,
)
from kohakuterrarium.session.store.lifecycle.lock import (
    acquire_writer_lock,
    release_writer_lock,
)
from kohakuterrarium.studio.persistence import store as store_mod
from kohakuterrarium.studio.persistence.delete_family import detach_file_family
from kohakuterrarium.terrarium.drive.store import migration
from kohakuterrarium.utils.file.lock import FileLock


def _plain_session(tmp_path, name: str = "alice") -> Path:
    path = tmp_path / f"{name}.kohakutr"
    path.write_bytes(b"session")
    return path


def _closed_store(tmp_path, name: str = "alice") -> Path:
    path = tmp_path / f"{name}.kohakutr"
    path.write_bytes(b"session")
    return path


def _wait_pending(path, count: int = 1, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if GATE.snapshot(path).legacy_pending == count:
            return
        time.sleep(0.01)
    raise AssertionError(f"pending occupancy did not reach {count}")


def _wait_active(path, count: int = 1, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if GATE.snapshot(path).legacy_active == count:
            return
        time.sleep(0.01)
    raise AssertionError(f"active occupancy did not reach {count}")


def _assert_idle(path) -> None:
    snap = GATE.snapshot(path)
    assert snap.legacy_active == 0
    assert snap.legacy_pending == 0
    assert snap.legacy_uncertain is False
    admitted = try_fast(path, eligible=True)
    assert admitted.reason == REASON_OK
    assert admitted.lease is not None
    admitted.lease.finish(source_released=True)


def _capture_purge(monkeypatch) -> list[list[Path]]:
    captured: list[list[Path]] = []
    real_purge = store_mod._purge_index_entries

    def spy_purge(paths):
        captured.append(list(paths))
        return real_purge(paths)

    monkeypatch.setattr(store_mod, "_purge_index_entries", spy_purge)
    return captured


def _symlink_alias(real: Path, alias: Path) -> Path | None:
    try:
        alias.symlink_to(real)
    except (OSError, NotImplementedError):
        return None
    return alias


class TestDeleteEnrollmentOrder:
    def test_enrolls_before_writer_lock_and_rename(self, tmp_path, monkeypatch):
        path = _plain_session(tmp_path)
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        order: list[str] = []
        real_acquire = store_mod.acquire_legacy
        real_lock = store_mod.acquire_writer_lock
        real_replace = Path.replace

        def spy_acquire(target, operation):
            order.append(f"enroll:{operation}")
            lease = real_acquire(target, operation)
            assert GATE.snapshot(path).legacy_active == 1
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_ENROLLED
            return lease

        def spy_lock(lock_path):
            order.append("writer_lock")
            assert GATE.snapshot(path).legacy_active == 1
            return real_lock(lock_path)

        def spy_replace(self, target):
            order.append("rename")
            assert GATE.snapshot(path).legacy_active == 1
            return real_replace(self, target)

        monkeypatch.setattr(store_mod, "acquire_legacy", spy_acquire)
        monkeypatch.setattr(store_mod, "acquire_writer_lock", spy_lock)
        monkeypatch.setattr(Path, "replace", spy_replace)

        deleted = store_mod.delete_session_files("alice")
        assert deleted == [path]
        assert not path.exists()
        assert order[0] == "enroll:delete"
        assert "writer_lock" in order and "rename" in order
        assert order.index("enroll:delete") < order.index("writer_lock")
        assert order.index("writer_lock") < order.index("rename")
        _assert_idle(path)

    def test_new_occupant_blocks_before_writer_lock(self, tmp_path, monkeypatch):
        path = _plain_session(tmp_path, "wait")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        locks: list[object] = []
        errors: list[BaseException] = []
        done = threading.Event()
        real_lock = store_mod.acquire_writer_lock

        def spy_lock(lock_path):
            locks.append(lock_path)
            assert GATE.snapshot(path).legacy_active == 1
            assert GATE.snapshot(path).new_active == 0
            return real_lock(lock_path)

        monkeypatch.setattr(store_mod, "acquire_writer_lock", spy_lock)

        def run_delete():
            try:
                store_mod.delete_session_files("wait")
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_delete)
        worker.start()
        try:
            _wait_pending(path, 1)
            assert locks == []
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_PENDING
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert not path.exists()
            assert locks
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        _assert_idle(path)

    def test_new_fault_blocks_before_writer_lock(self, tmp_path, monkeypatch):
        path = _plain_session(tmp_path, "new-fault")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        admitted = try_fast(path, eligible=True)
        assert admitted.lease is not None
        admitted.lease.finish(source_released=False)
        locks: list[object] = []

        def boom_lock(*args, **kwargs):
            locks.append(args)
            raise AssertionError("must not take writer lock after NEW fault")

        monkeypatch.setattr(store_mod, "acquire_writer_lock", boom_lock)
        with pytest.raises(ResourceReleaseError):
            store_mod.delete_session_files("new-fault")
        assert path.exists()
        assert locks == []
        with pytest.raises(ResourceReleaseError):
            try_fast(path, eligible=True)


class TestDeleteEnrollmentScope:
    def test_legacy_already_active_still_enrolls_own_scope(self, tmp_path, monkeypatch):
        path = _closed_store(tmp_path, "nested")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        holder = acquire_legacy(path, "outer")
        try:
            assert GATE.snapshot(path).legacy_active == 1
            deleted = store_mod.delete_session_files("nested")
            assert deleted
            assert not path.exists()
            snap = GATE.snapshot(path)
            assert snap.legacy_active == 1
            assert snap.legacy_uncertain is False
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_ENROLLED
        finally:
            holder.finish(source_released=True)
        _assert_idle(path)

    def test_plain_duplicate_and_separate_version_purges_listed_targets(
        self, tmp_path, monkeypatch
    ):
        path = _plain_session(tmp_path, "dup")
        v2 = tmp_path / "dup.kohakutr.v2"
        v2.write_bytes(b"v2")
        sidecar = path.with_name(path.name + "-wal")
        sidecar.write_bytes(b"wal")
        duplicate = Path(str(path))
        listed = [duplicate, path, v2]
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        monkeypatch.setattr(
            store_mod, "all_versions_for_session_default", lambda _name: listed
        )
        purged = _capture_purge(monkeypatch)
        enrolled: list[str] = []
        locks: list[str] = []
        real_acquire = store_mod.acquire_legacy
        real_lock = store_mod.acquire_writer_lock

        def spy_acquire(target, operation):
            assert operation == "delete"
            enrolled.append(store_mod._path_identity(Path(target)))
            return real_acquire(target, operation)

        def spy_lock(lock_path):
            locks.append(lock_path)
            return real_lock(lock_path)

        monkeypatch.setattr(store_mod, "acquire_legacy", spy_acquire)
        monkeypatch.setattr(store_mod, "acquire_writer_lock", spy_lock)
        deleted = store_mod.delete_session_files("dup")
        assert set(deleted) == {path, v2, sidecar}
        assert not path.exists() and not v2.exists() and not sidecar.exists()
        assert purged == [listed]
        assert enrolled == [
            store_mod._path_identity(path),
            store_mod._path_identity(v2),
        ]
        assert locks == [
            store_mod._path_identity(path),
            store_mod._path_identity(v2),
        ]
        _assert_idle(path)
        _assert_idle(v2)

    def test_alias_enumerated_first_removes_real_and_sidecars(
        self, tmp_path, monkeypatch
    ):
        real = _plain_session(tmp_path, "real")
        alias = tmp_path / "alias.kohakutr"
        if _symlink_alias(real, alias) is None:
            if os.name != "nt":
                pytest.skip("symlink creation not permitted on this platform")
            alias = tmp_path / "REAL.kohakutr"
        alias_wal = alias.with_name(alias.name + "-wal")
        alias_wal.write_bytes(b"alias-wal")
        real_wal = real.with_name(real.name + "-wal")
        if real_wal != alias_wal:
            real_wal.write_bytes(b"real-wal")
        listed = [alias, real]
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        monkeypatch.setattr(
            store_mod, "all_versions_for_session_default", lambda _name: listed
        )
        purged = _capture_purge(monkeypatch)
        enrolled: list[str] = []
        locks: list[str] = []
        real_acquire = store_mod.acquire_legacy
        real_lock = store_mod.acquire_writer_lock

        def spy_acquire(target, operation):
            assert operation == "delete"
            enrolled.append(store_mod._path_identity(Path(target)))
            return real_acquire(target, operation)

        def spy_lock(lock_path):
            locks.append(lock_path)
            return real_lock(lock_path)

        monkeypatch.setattr(store_mod, "acquire_legacy", spy_acquire)
        monkeypatch.setattr(store_mod, "acquire_writer_lock", spy_lock)
        monkeypatch.setattr(
            store_mod.drive_migration_lock, "_MIGRATE_LOCK_TIMEOUT_S", 0
        )
        for suffix, error in (
            (".lock", SessionLockedError),
            (".drives.migrate-lock", TimeoutError),
        ):
            holder = FileLock(str(real) + suffix)
            holder.acquire()
            try:
                with pytest.raises(error):
                    store_mod.delete_session_files("alias")
                assert alias.exists() and real.exists()
                assert alias_wal.exists() and real_wal.exists()
                _assert_idle(real)
            finally:
                holder.release()
        enrolled.clear()
        locks.clear()
        deleted = store_mod.delete_session_files("alias")
        assert not alias.exists()
        assert not real.exists()
        assert not alias_wal.exists()
        assert not real_wal.exists()
        assert alias in deleted or any(p.name == alias.name for p in deleted)
        assert real in deleted or any(p.name == real.name for p in deleted)
        assert purged == [listed]
        assert enrolled == [store_mod._path_identity(real)]
        assert locks == sorted(
            {os.path.normcase(os.path.abspath(path)) for path in (alias, real)}
        )
        _assert_idle(alias)
        _assert_idle(real)

    def test_multiversion_enrolls_each_resolved_file(self, tmp_path, monkeypatch):
        v1 = _plain_session(tmp_path, "alice")
        v2 = tmp_path / "alice.kohakutr.v2"
        v2.write_bytes(b"v2")
        other = _plain_session(tmp_path, "bob")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        enrolled: list[str] = []
        real_acquire = store_mod.acquire_legacy

        def spy_acquire(target, operation):
            enrolled.append(Path(target).name)
            return real_acquire(target, operation)

        monkeypatch.setattr(store_mod, "acquire_legacy", spy_acquire)
        deleted = store_mod.delete_session_files("alice")
        assert set(deleted) == {v1, v2}
        assert set(enrolled) == {"alice.kohakutr", "alice.kohakutr.v2"}
        assert other.exists()
        _assert_idle(v1)
        _assert_idle(v2)
        _assert_idle(other)

    def test_unrelated_file_is_not_blocked(self, tmp_path, monkeypatch):
        blocked = _plain_session(tmp_path, "blocked")
        other = _plain_session(tmp_path, "other")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        # Index startup reconcile can open sibling files; occupancy routing
        # is independent of that post-delete bookkeeping path.
        monkeypatch.setattr(store_mod, "_purge_index_entries", lambda _paths: None)
        occupying = try_fast(blocked, eligible=True)
        assert occupying.lease is not None
        waiting = threading.Event()
        other_done = threading.Event()
        errors: list[BaseException] = []
        real_acquire = store_mod.acquire_legacy

        def spy_acquire(target, operation):
            if Path(target).resolve() == blocked.resolve():
                waiting.set()
            return real_acquire(target, operation)

        monkeypatch.setattr(store_mod, "acquire_legacy", spy_acquire)

        def delete_blocked():
            try:
                store_mod.delete_session_files("blocked")
            except BaseException as exc:
                errors.append(exc)

        def delete_other():
            waiting.wait(2)
            _wait_pending(blocked, 1)
            try:
                store_mod.delete_session_files("other")
                other_done.set()
            except BaseException as exc:
                errors.append(exc)
                other_done.set()

        waiter = threading.Thread(target=delete_blocked)
        sibling = threading.Thread(target=delete_other)
        waiter.start()
        sibling.start()
        try:
            assert other_done.wait(5)
            assert not other.exists()
            occupying.lease.finish(source_released=True)
            waiter.join(5)
            sibling.join(5)
        finally:
            occupying.lease.finish(source_released=True)
        assert not waiter.is_alive() and not sibling.is_alive()
        assert not errors
        assert not blocked.exists()
        _assert_idle(blocked)
        _assert_idle(other)


class TestDeleteEnrollmentCleanup:
    def test_failed_writer_lock_releases_without_uncertainty(
        self, tmp_path, monkeypatch
    ):
        path = _plain_session(tmp_path, "lockfail")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        holder = acquire_writer_lock(str(path))
        try:
            with pytest.raises(SessionLockedError):
                store_mod.delete_session_files("lockfail")
            assert path.exists()
            snap = GATE.snapshot(path)
            assert snap.legacy_active == 0
            assert snap.legacy_uncertain is False
            _assert_idle(path)
        finally:
            release_writer_lock(holder)

    def test_failed_second_lock_releases_first_enrollment(self, tmp_path, monkeypatch):
        a = _plain_session(tmp_path, "a")
        b = _plain_session(tmp_path, "b")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        monkeypatch.setattr(
            store_mod, "all_versions_for_session_default", lambda _name: [a, b]
        )
        first = min(a, b, key=str)
        second = max(a, b, key=str)
        holder = acquire_writer_lock(str(second))
        try:
            with pytest.raises(SessionLockedError):
                store_mod.delete_session_files("pair")
            assert a.exists() and b.exists()
            _assert_idle(first)
            _assert_idle(second)
        finally:
            release_writer_lock(holder)

    @pytest.mark.parametrize("alias_lock", [False, True])
    def test_failed_lock_on_canonical_alias_releases_enrollment(
        self, tmp_path, monkeypatch, alias_lock
    ):
        real = _plain_session(tmp_path, "held")
        alias = tmp_path / "alias.kohakutr"
        if _symlink_alias(real, alias) is None:
            alias = Path(str(real))
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        monkeypatch.setattr(
            store_mod, "all_versions_for_session_default", lambda _name: [alias, real]
        )
        holder = acquire_writer_lock(
            str(alias) if alias_lock else store_mod._path_identity(real)
        )
        try:
            with pytest.raises(SessionLockedError):
                store_mod.delete_session_files("held")
            assert real.exists()
            assert alias.exists()
            _assert_idle(alias)
            _assert_idle(real)
        finally:
            release_writer_lock(holder)

    def test_busy_migration_releases_enrollment(self, tmp_path, monkeypatch):
        path = _plain_session(tmp_path, "busy")
        sidecar = Path(str(path) + ".drives")
        sidecar.write_bytes(b"not yet complete")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        monkeypatch.setattr(migration, "_MIGRATE_LOCK_TIMEOUT_S", 0.05)
        monkeypatch.setattr(migration, "_MIGRATE_POLL_S", 0.005)
        monkeypatch.setattr(
            store_mod.drive_migration_lock, "_MIGRATE_LOCK_TIMEOUT_S", 0.05
        )
        monkeypatch.setattr(store_mod.drive_migration_lock, "_MIGRATE_POLL_S", 0.005)
        holder = FileLock(str(sidecar) + ".migrate-lock")
        holder.acquire()
        try:
            with pytest.raises(TimeoutError):
                store_mod.delete_session_files("busy")
            assert path.exists()
            assert sidecar.exists()
            _assert_idle(path)
        finally:
            holder.release()

    def test_legacy_holds_through_rename_rollback(self, tmp_path, monkeypatch):
        path = _plain_session(tmp_path, "rollback")
        wal = path.with_name(path.name + "-wal")
        wal.write_bytes(b"wal")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        occupancy: list[int] = []
        real_replace = Path.replace

        def spy_replace(self, target):
            occupancy.append(GATE.snapshot(path).legacy_active)
            if self == wal:
                raise PermissionError(13, "cannot rename wal")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", spy_replace)
        with pytest.raises(PermissionError):
            store_mod.delete_session_files("rollback")
        assert path.exists()
        assert wal.exists()
        assert occupancy and all(count == 1 for count in occupancy)
        _assert_idle(path)

    def test_quarantine_return_does_not_mark_uncertain(self, tmp_path, monkeypatch):
        path = _closed_store(tmp_path, "alice")
        drives = path.with_name(path.name + ".drives")
        drives.write_bytes(b"sensitive drive rows")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        real_unlink = store_mod._unlink_with_retry

        def fake_unlink(p, *a, **kw):
            if ".drives" in p.name:
                raise PermissionError(13, "simulated locked drive sidecar")
            return real_unlink(p, *a, **kw)

        monkeypatch.setattr(store_mod, "_unlink_with_retry", fake_unlink)
        deleted = store_mod.delete_session_files("alice")
        assert not path.exists()
        assert not drives.exists()
        assert any(".orphan-" in p.name for p in deleted)
        snap = GATE.snapshot(path)
        assert snap.legacy_uncertain is False
        _assert_idle(path)

    def test_canceled_worker_keeps_lease_until_rename_finishes(
        self, tmp_path, monkeypatch
    ):
        path = _plain_session(tmp_path, "cancel")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        hold, entered, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        real_detach = detach_file_family

        def hold_detach(family):
            entered.set()
            hold.wait(5)
            return real_detach(family)

        monkeypatch.setattr(store_mod, "detach_file_family", hold_detach)

        def run_delete():
            try:
                store_mod.delete_session_files("cancel")
            finally:
                finished.set()

        async def run():
            task = asyncio.create_task(asyncio.to_thread(run_delete))
            assert await asyncio.to_thread(entered.wait, 3)
            assert GATE.snapshot(path).legacy_active == 1
            task.cancel()
            await asyncio.sleep(0.05)
            assert not finished.is_set()
            assert GATE.snapshot(path).legacy_active == 1
            hold.set()
            await asyncio.gather(task, return_exceptions=True)
            assert finished.wait(2)
            assert not path.exists()
            _assert_idle(path)

        asyncio.run(run())

    def test_canceled_worker_holds_through_rollback(self, tmp_path, monkeypatch):
        path = _plain_session(tmp_path, "cancel-roll")
        wal = path.with_name(path.name + "-wal")
        wal.write_bytes(b"wal")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        hold, entered, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        errors: list[BaseException] = []
        real_replace = Path.replace

        def hold_replace(self, target):
            if self == path:
                entered.set()
                hold.wait(5)
            if self == wal:
                raise PermissionError(13, "cannot rename wal")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", hold_replace)

        def run_delete():
            try:
                store_mod.delete_session_files("cancel-roll")
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()

        async def run():
            task = asyncio.create_task(asyncio.to_thread(run_delete))
            assert await asyncio.to_thread(entered.wait, 3)
            assert GATE.snapshot(path).legacy_active == 1
            task.cancel()
            await asyncio.sleep(0.05)
            assert not finished.is_set()
            assert GATE.snapshot(path).legacy_active == 1
            hold.set()
            await asyncio.gather(task, return_exceptions=True)
            assert finished.wait(2)
            assert errors and isinstance(errors[0], PermissionError)
            assert path.exists()
            _assert_idle(path)

        asyncio.run(run())
