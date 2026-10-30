"""Metadata candidate: gate first, then one read-only KohakuVault handle."""

import asyncio
import logging
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from kohakuterrarium.session.readonly import candidate as candidate_mod
from kohakuterrarium.session.readonly import metadata as readonly_mod
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.readonly.candidate import (
    MetadataRead,
    MetadataReadError,
    read_metadata_candidate,
)
from kohakuterrarium.session.readonly.metadata import MetaDecodeError, read_session_meta
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    Admission,
    REASON_FAST_ACTIVE,
    REASON_LEGACY_ENROLLED,
    REASON_LEGACY_PENDING,
    REASON_LEGACY_RELEASE_UNCERTAIN,
    REASON_NOT_ELIGIBLE,
    REASON_OK,
    ResourceReleaseError,
    acquire_legacy,
    try_fast,
)
from kohakuterrarium.session.store import SessionStore

_T = 5.0


def _closed_session(path: Path) -> Path:
    with closing(SessionStore(path)) as store:
        store.init_meta("sess", "agent", "/p", "/w", ["alice"])
        store.meta["nested"] = {"items": [None, True, 4.5, "汉字"]}
        store.meta["raw"] = b"\x00\xff payload"
    return path


def _family(path: Path) -> dict[str, tuple[bytes, int]]:
    return {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in path.parent.iterdir()
        if p.name.startswith(path.name)
    }


def _assert_idle(path) -> None:
    admitted = try_fast(path, eligible=True)
    assert admitted.reason == REASON_OK
    assert admitted.lease is not None
    admitted.lease.finish(source_released=True)


def _forbid_admitted_read(monkeypatch) -> list:
    opened = []

    class Forbidden:
        def __init__(self, *args, **kwargs):
            opened.append(args)
            raise AssertionError("admitted read must not start")

    monkeypatch.setattr(candidate_mod, "MetaTableRead", Forbidden)
    return opened


class TestSurface:
    def test_metadata_read_error_is_ordinary_exception(self):
        assert issubclass(MetadataReadError, Exception)
        err = MetadataReadError("sql", RuntimeError("boom"))
        assert err.stage == "sql"
        assert err.cleanup_errors == ()
        assert isinstance(err.__cause__, RuntimeError)


class TestGateBeforeRead:
    def test_ineligible_reads_enrolled_without_admission(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "off.kohakutr")
        opened = _forbid_admitted_read(monkeypatch)
        result = read_metadata_candidate(path)
        assert isinstance(result, MetadataRead)
        assert result.reason == REASON_NOT_ELIGIBLE
        assert result.meta["session_id"] == "sess"
        assert result.meta["nested"]["items"][-1] == "汉字"
        assert opened == []
        assert GATE.snapshot(path).registered is False

    def test_legacy_enrolled_falls_back(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "enrolled.kohakutr")
        opened = _forbid_admitted_read(monkeypatch)
        lease = acquire_legacy(path, "open")
        try:
            result = read_metadata_candidate(path, eligible=True)
            assert result.reason == REASON_LEGACY_ENROLLED
            assert result.meta["session_id"] == "sess"
            assert opened == []
        finally:
            lease.finish(source_released=True)
        _assert_idle(path)

    def test_fast_active_waits_for_the_occupant(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "active.kohakutr")
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        opened = _forbid_admitted_read(monkeypatch)
        results: list[MetadataRead] = []
        worker = threading.Thread(
            target=lambda: results.append(read_metadata_candidate(path, eligible=True))
        )
        worker.start()
        try:
            deadline = time.monotonic() + _T
            while (
                time.monotonic() < deadline and GATE.snapshot(path).legacy_pending != 1
            ):
                time.sleep(0.01)
            assert GATE.snapshot(path).legacy_pending == 1 and not results
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(_T)
        assert results[0].reason == REASON_FAST_ACTIVE
        assert results[0].meta["session_id"] == "sess"
        assert opened == []
        _assert_idle(path)

    def test_legacy_pending_falls_back(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "pending.kohakutr")
        opened = _forbid_admitted_read(monkeypatch)
        monkeypatch.setattr(
            candidate_mod,
            "try_fast",
            lambda *_a, **_k: Admission(None, REASON_LEGACY_PENDING),
        )
        result = read_metadata_candidate(path, eligible=True)
        assert result.reason == REASON_LEGACY_PENDING
        assert result.meta["session_id"] == "sess"
        assert opened == []
        _assert_idle(path)

    def test_legacy_release_uncertain_falls_back(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "legacy-uncertain.kohakutr")
        lease = acquire_legacy(path, "open")
        lease.finish(source_released=False)
        opened = _forbid_admitted_read(monkeypatch)
        result = read_metadata_candidate(path, eligible=True)
        assert result.reason == REASON_LEGACY_RELEASE_UNCERTAIN
        assert result.meta["session_id"] == "sess"
        assert opened == []

    def test_new_uncertain_raises_without_any_read(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "new-uncertain.kohakutr")
        occupying = try_fast(path, eligible=True)
        occupying.lease.finish(source_released=False)
        opened = _forbid_admitted_read(monkeypatch)
        copied = []
        monkeypatch.setattr(
            candidate_mod, "read_session_meta", lambda *a: copied.append(a)
        )
        with pytest.raises(ResourceReleaseError):
            read_metadata_candidate(path, eligible=True)
        assert opened == [] and copied == []


class TestAdmittedRead:
    def test_closed_session_reads_without_touching_the_family(self, tmp_path):
        path = _closed_session(tmp_path / "closed.kohakutr")
        before = _family(path)
        result = read_metadata_candidate(path, eligible=True)
        assert result.reason == REASON_OK
        assert result.meta["session_id"] == "sess"
        assert result.meta["nested"]["items"][-1] == "汉字"
        assert result.meta["raw"] == b"\x00\xff payload"
        assert _family(path) == before
        _assert_idle(path)

    def test_live_writer_committed_rows_are_visible(self, tmp_path):
        path = tmp_path / "live.kohakutr"
        writer = SessionStore(path)
        try:
            writer.init_meta("sess", "agent", "/p", "/w", ["alice"])
            writer.meta["late"] = "committed"
            # Admission is declined while the writer is enrolled in-process.
            result = read_metadata_candidate(path, eligible=True)
            assert result.reason == REASON_LEGACY_ENROLLED
            assert result.meta["late"] == "committed"
        finally:
            writer.close(update_status=False)

    def test_file_uri_and_relative_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = _closed_session(tmp_path / "s %20 #.kohakutr")
        via_uri = read_metadata_candidate(path.as_uri(), eligible=True)
        via_rel = read_metadata_candidate("s %20 #.kohakutr", eligible=True)
        assert via_uri.reason == via_rel.reason == REASON_OK
        assert via_uri.meta["session_id"] == via_rel.meta["session_id"] == "sess"
        assert not (tmp_path / "file:").exists()
        _assert_idle(path)

    def test_missing_field_is_absent_not_defaulted(self, tmp_path):
        path = _closed_session(tmp_path / "absent.kohakutr")
        result = read_metadata_candidate(path, eligible=True)
        assert "missing" not in result.meta
        _assert_idle(path)

    def test_missing_meta_table_is_strict_error(self, tmp_path):
        path = tmp_path / "no-meta.kohakutr"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE unrelated(x INTEGER)")
            conn.commit()
        with pytest.raises(MetadataReadError) as caught:
            read_metadata_candidate(path, eligible=True)
        assert caught.value.stage == "sql"
        assert caught.value.cleanup_errors == ()
        _assert_idle(path)
        # The lenient reader treats the same file as having no metadata.
        assert read_session_meta(path) == {}

    def test_empty_file_is_strict_error(self, tmp_path):
        path = tmp_path / "empty.kohakutr"
        sqlite3.connect(path).close()
        with pytest.raises(MetadataReadError) as caught:
            read_metadata_candidate(path, eligible=True)
        assert caught.value.stage == "sql"
        _assert_idle(path)

    def test_bad_utf8_key_is_decode_error(self, tmp_path):
        path = _closed_session(tmp_path / "bad-utf8.kohakutr")
        with closing(sqlite3.connect(path)) as conn:
            payload = conn.execute(
                "SELECT value FROM meta WHERE key=?", (b"session_id",)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)", (b"\xff\xfe", payload)
            )
            conn.commit()
        with pytest.raises(MetadataReadError) as caught:
            read_metadata_candidate(path, eligible=True)
        assert caught.value.stage == "decode"
        assert isinstance(caught.value.__cause__, MetaDecodeError)
        _assert_idle(path)
        # The legacy-admission reader is just as strict.
        with pytest.raises(MetaDecodeError):
            read_session_meta(path)
        _assert_idle(path)

    def test_undecodable_value_is_strict_error(self, tmp_path):
        path = _closed_session(tmp_path / "bad-value.kohakutr")
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                (b"broken", b"\x89\x4b\x01\x07\x00\x00\x56\x4bgarbage"),
            )
            conn.commit()
        with pytest.raises(MetadataReadError):
            read_metadata_candidate(path, eligible=True)
        _assert_idle(path)
        with pytest.raises(Exception, match="magic"):
            read_session_meta(path)
        _assert_idle(path)

    def test_missing_path_does_not_create_file(self, tmp_path):
        path = tmp_path / "missing.kohakutr"
        with pytest.raises(FileNotFoundError):
            read_metadata_candidate(path, eligible=True)
        assert not path.exists()
        assert GATE.snapshot(path).registered is False


class TestCleanup:
    def test_open_failure_holds_nothing(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "open-fail.kohakutr")
        primary = OSError("open failed")

        def boom(*args, **kwargs):
            raise primary

        monkeypatch.setattr(readonly_mod, "open_read_table", boom)
        with pytest.raises(MetadataReadError) as caught:
            read_metadata_candidate(path, eligible=True)
        assert caught.value.stage == "sql"
        assert caught.value.__cause__ is primary
        _assert_idle(path)

    def test_close_failure_is_resource_error(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "close-fail.kohakutr")
        close_error = RuntimeError("close failed")
        real_dispose = readonly_mod.dispose

        def fail(handle):
            real_dispose(handle)
            raise close_error

        monkeypatch.setattr(readonly_mod, "dispose", fail)
        with pytest.raises(MetadataReadError) as caught:
            read_metadata_candidate(path, eligible=True)
        assert caught.value.stage == "resource"
        assert caught.value.__cause__ is close_error
        assert caught.value.cleanup_errors == (close_error,)
        assert GATE.snapshot(path).new_uncertain is True
        with pytest.raises(ResourceReleaseError):
            try_fast(path, eligible=True)

    def test_read_failure_survives_close_failure(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "both-fail.kohakutr")
        primary = RuntimeError("read failed")
        cleanup = RuntimeError("close failed")
        real_dispose = readonly_mod.dispose

        def fail_read(*args, **kwargs):
            raise primary

        def fail_close(handle):
            real_dispose(handle)
            raise cleanup

        monkeypatch.setattr(readonly_mod, "read_prefix", fail_read)
        monkeypatch.setattr(readonly_mod, "dispose", fail_close)
        with pytest.raises(MetadataReadError) as caught:
            read_metadata_candidate(path, eligible=True)
        assert caught.value.stage == "resource"
        assert caught.value.__cause__ is primary
        assert caught.value.cleanup_errors == (cleanup,)

    def test_admitted_error_does_not_retry_enrolled(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "no-retry.kohakutr")
        retried = []
        monkeypatch.setattr(
            candidate_mod, "read_session_meta", lambda *a: retried.append(a)
        )
        monkeypatch.setattr(
            readonly_mod,
            "read_prefix",
            lambda *a, **k: (_ for _ in ()).throw(OSError("read failed")),
        )
        with pytest.raises(MetadataReadError):
            read_metadata_candidate(path, eligible=True)
        assert retried == []
        _assert_idle(path)

    def test_interrupt_propagates_and_releases(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "interrupt.kohakutr")
        monkeypatch.setattr(
            readonly_mod,
            "read_prefix",
            lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt("stop")),
        )
        with pytest.raises(KeyboardInterrupt):
            read_metadata_candidate(path, eligible=True)
        _assert_idle(path)

    def test_no_source_content_in_logs(self, tmp_path, caplog):
        path = _closed_session(tmp_path / "nolog.kohakutr")
        caplog.set_level(logging.DEBUG)
        result = read_metadata_candidate(path, eligible=True)
        assert result.meta["session_id"] == "sess"
        text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "sess" not in text and "汉字" not in text
        _assert_idle(path)


class TestOccupancy:
    def _hold_close(self, monkeypatch):
        entered, release, closed = (threading.Event() for _ in range(3))
        real_dispose = readonly_mod.dispose

        def held(handle):
            entered.set()
            assert release.wait(_T)
            result = real_dispose(handle)
            closed.set()
            return result

        monkeypatch.setattr(readonly_mod, "dispose", held)
        return entered, release, closed

    def test_writer_open_waits_for_admitted_read(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "held.kohakutr")
        other = tmp_path / "other.kohakutr"
        entered, release, closed = self._hold_close(monkeypatch)
        errors: list[BaseException] = []
        stores: list[SessionStore] = []
        store_ready, other_done = threading.Event(), threading.Event()

        def read():
            try:
                meta = read_metadata_candidate(path, eligible=True).meta
                assert meta["session_id"] == "sess"
            except BaseException as exc:
                errors.append(exc)

        def open_store():
            try:
                stores.append(SessionStore(path))
            except BaseException as exc:
                errors.append(exc)
            store_ready.set()

        def other_file():
            with closing(SessionStore(other)) as store:
                store.init_meta("other", "agent", "/p", "/w", ["bob"])
            other_done.set()

        worker = threading.Thread(target=read)
        waiter = threading.Thread(target=open_store)
        sibling = threading.Thread(target=other_file)
        worker.start()
        try:
            assert entered.wait(_T) and GATE.snapshot(path).new_active == 1
            waiter.start()
            sibling.start()
            deadline = time.monotonic() + _T
            while (
                time.monotonic() < deadline and GATE.snapshot(path).legacy_pending != 1
            ):
                time.sleep(0.01)
            assert GATE.snapshot(path).legacy_pending == 1
            assert other_done.wait(_T)
            assert not store_ready.is_set() and not closed.is_set()
        finally:
            release.set()
            worker.join(_T)
            waiter.join(_T)
            sibling.join(_T)
            for store in stores:
                store.close(update_status=False)
        assert closed.is_set() and store_ready.is_set()
        assert not errors and stores
        _assert_idle(path)
        _assert_idle(other)

    def test_cancelled_to_thread_keeps_lease_until_worker_finishes(
        self, tmp_path, monkeypatch
    ):
        path = _closed_session(tmp_path / "cancel.kohakutr")
        entered, release, closed = self._hold_close(monkeypatch)
        done = threading.Event()
        errors = []

        def hold_read():
            try:
                out = read_metadata_candidate(path, eligible=True)
                assert out.reason == REASON_OK
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        async def run():
            task = asyncio.create_task(asyncio.to_thread(hold_read))
            try:
                assert await asyncio.to_thread(entered.wait, _T)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.sleep(0.05)
                assert not done.is_set() and not closed.is_set()
                assert GATE.snapshot(path).new_active == 1
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                assert await asyncio.to_thread(done.wait, _T)
            assert closed.is_set() and not errors
            _assert_idle(path)

        asyncio.run(run())

    def test_live_store_and_memory_read_enrolled(self, tmp_path):
        path = tmp_path / "live.kohakutr"
        store = SessionStore(path)
        memory = SessionMemory(str(path))
        try:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            store.meta["raw"] = b"\x00\xff payload"
            result = read_metadata_candidate(path, eligible=True)
            assert result.reason == REASON_LEGACY_ENROLLED
            assert result.meta["raw"] == b"\x00\xff payload"
            assert GATE.snapshot(path).legacy_active >= 1
        finally:
            memory.close()
            store.close(update_status=False)
        _assert_idle(path)


def test_legacy_fallback_uses_read_session_meta(tmp_path, monkeypatch):
    path = _closed_session(tmp_path / "legacy-spy.kohakutr")
    called = []
    original = read_session_meta

    def spy(target):
        called.append(target)
        return original(target)

    monkeypatch.setattr(candidate_mod, "read_session_meta", spy)
    result = read_metadata_candidate(path, eligible=False)
    assert called
    assert result.reason == REASON_NOT_ELIGIBLE
    assert result.meta["session_id"] == "sess"
