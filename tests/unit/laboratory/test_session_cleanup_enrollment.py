"""Occupancy around transfer delete and resume-rollback unlinks.

Uses real files under the config resume directory. Directory trees, editor
swap files, and unknown sidecars are out of scope and must remain untouched.
Production code does not call ``try_fast``.
"""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.laboratory._internal.app.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium import session as sess_mod
from kohakuterrarium.laboratory.adapters.terrarium.session import (
    TerrariumSessionAdapter,
)
from kohakuterrarium.session.occupancy.family import acquire_family
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_LEGACY_ENROLLED,
    REASON_LEGACY_PENDING,
    ResourceReleaseError,
    try_fast,
)
from kohakuterrarium.session.store import SessionStore
from tests.unit.laboratory.file_mutation_enrollment_helpers import (
    assert_idle,
    wait_pending,
)


class _FakeNode:
    def __init__(self):
        self.handlers = {}
        self.unregistered = []

    def register_app_extension(self, ns, handler):
        self.handlers[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        return self.handlers.pop(ns, None) is not None


class _FakeEngine:
    def __init__(self):
        self._session_stores = {}
        self._owned_sessions = set()
        self._creatures = {}
        self._removed = []

    def list_creatures(self):
        return list(self._creatures.values())

    async def remove_creature(self, creature_id):
        self._removed.append(creature_id)
        self._creatures.pop(creature_id, None)


def _msg(type_, body=None):
    return AppMessage(
        namespace="terrarium.session",
        type=type_,
        body=body or {},
        sender_node="ctrl",
        request_id=None,
        in_reply_to=None,
    )


def _adapter(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    engine = _FakeEngine()
    adapter = TerrariumSessionAdapter(engine, _FakeNode())
    return adapter, engine


def _transfer_file(tmp_path, name: str = "sess.kohakutr", data: bytes = b"session"):
    path = tmp_path / "resume" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _open_transfer_store(
    tmp_path, name: str = "sess.kohakutr"
) -> tuple[Path, SessionStore]:
    path = tmp_path / "resume" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path, SessionStore(path)


class TestDeleteTransferEnrollment:
    def test_enrolls_explicit_main_wal_shm_before_unlink(self, tmp_path, monkeypatch):
        adapter, _engine = _adapter(monkeypatch, tmp_path)
        path = _transfer_file(tmp_path)
        wal, shm = Path(f"{path}-wal"), Path(f"{path}-shm")
        wal.write_bytes(b"wal")
        shm.write_bytes(b"shm")
        unknown = path.with_name(path.name + ".bak")
        unknown.write_bytes(b"keep")
        order: list[str] = []
        real_acquire = sess_mod.acquire_family
        real_unlink = Path.unlink

        def spy_acquire(paths, operation):
            order.append(f"enroll:{operation}")
            lease = real_acquire(paths, operation)
            assert GATE.snapshot(path).legacy_active >= 1
            assert GATE.snapshot(wal).legacy_active >= 1
            assert GATE.snapshot(shm).legacy_active >= 1
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_ENROLLED
            return lease

        def spy_unlink(self, *args, **kwargs):
            target = Path(self)
            if target in {path, wal, shm}:
                order.append(f"unlink:{target.name}")
                assert GATE.snapshot(target).legacy_active >= 1
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(sess_mod, "acquire_family", spy_acquire)
        monkeypatch.setattr(Path, "unlink", spy_unlink)
        result = adapter._op_delete_transfer({"session_path": str(path)})
        assert result["ok"] is True
        assert result["session_path"] == str(path.resolve())
        assert order[0] == "enroll:delete_transfer"
        assert any(item.startswith("unlink:") for item in order)
        assert order.index("enroll:delete_transfer") < order.index(
            f"unlink:{path.name}"
        )
        assert not path.exists() and not wal.exists() and not shm.exists()
        assert unknown.read_bytes() == b"keep"
        assert_idle(path)
        assert_idle(wal)
        assert_idle(shm)

    def test_missing_wal_shm_still_succeeds(self, tmp_path, monkeypatch):
        adapter, _engine = _adapter(monkeypatch, tmp_path)
        path = _transfer_file(tmp_path)
        result = adapter._op_delete_transfer({"session_path": str(path)})
        assert result["ok"] is True
        assert not path.exists()
        assert not Path(f"{path}-wal").exists()
        assert_idle(path)

    def test_active_store_rejected_without_enroll(self, tmp_path, monkeypatch):
        adapter, engine = _adapter(monkeypatch, tmp_path)
        path, store = _open_transfer_store(tmp_path)
        engine._session_stores["live"] = store
        enrolled: list[object] = []

        def boom_acquire(*args, **kwargs):
            enrolled.append(args)
            raise AssertionError("must not enroll an active transfer")

        monkeypatch.setattr(sess_mod, "acquire_family", boom_acquire)
        try:
            with pytest.raises(ValueError, match="active session store"):
                adapter._op_delete_transfer({"session_path": str(path)})
            assert path.exists()
            assert enrolled == []
        finally:
            store.close(update_status=False)

    def test_outside_transfer_directory_rejected_without_enroll(
        self, tmp_path, monkeypatch
    ):
        adapter, _engine = _adapter(monkeypatch, tmp_path)
        outside = tmp_path / "unrelated.kohakutr"
        outside.write_bytes(b"keep")
        enrolled: list[object] = []

        def boom_acquire(*args, **kwargs):
            enrolled.append(args)
            raise AssertionError("must not enroll an escaped path")

        monkeypatch.setattr(sess_mod, "acquire_family", boom_acquire)
        with pytest.raises(ValueError, match="transfer directory"):
            adapter._op_delete_transfer({"session_path": str(outside)})
        assert outside.read_bytes() == b"keep"
        assert enrolled == []

    def test_new_occupant_blocks_before_unlink(self, tmp_path, monkeypatch):
        adapter, _engine = _adapter(monkeypatch, tmp_path)
        path = _transfer_file(tmp_path)
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        unlinks: list[str] = []
        errors: list[BaseException] = []
        done = threading.Event()
        real_unlink = Path.unlink

        def spy_unlink(self, *args, **kwargs):
            target = Path(self)
            if target == path:
                unlinks.append(target.name)
                assert GATE.snapshot(path).legacy_active >= 1
                assert GATE.snapshot(path).new_active == 0
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", spy_unlink)

        def run_delete():
            try:
                adapter._op_delete_transfer({"session_path": str(path)})
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_delete)
        worker.start()
        try:
            wait_pending(path, 1)
            assert unlinks == []
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_PENDING
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert not path.exists()
            assert unlinks
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert_idle(path)

    def test_new_fault_declines_before_unlink(self, tmp_path, monkeypatch):
        adapter, _engine = _adapter(monkeypatch, tmp_path)
        path = _transfer_file(tmp_path)
        admitted = try_fast(path, eligible=True)
        assert admitted.lease is not None
        admitted.lease.finish(source_released=False)
        unlinks: list[object] = []

        def boom_unlink(*args, **kwargs):
            unlinks.append(args)
            raise AssertionError("must not unlink after NEW fault")

        monkeypatch.setattr(Path, "unlink", boom_unlink)
        with pytest.raises(ResourceReleaseError):
            adapter._op_delete_transfer({"session_path": str(path)})
        assert path.exists()
        assert unlinks == []

    def test_unlink_error_still_releases(self, tmp_path, monkeypatch):
        adapter, _engine = _adapter(monkeypatch, tmp_path)
        path = _transfer_file(tmp_path)

        def boom_unlink(self, *args, **kwargs):
            if Path(self) == path:
                raise PermissionError(13, "locked")
            return Path.unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", boom_unlink)
        with pytest.raises(PermissionError):
            adapter._op_delete_transfer({"session_path": str(path)})
        assert path.exists()
        assert_idle(path)

    def test_unrelated_transfer_progresses_while_target_waits(
        self, tmp_path, monkeypatch
    ):
        adapter, _engine = _adapter(monkeypatch, tmp_path)
        blocked = _transfer_file(tmp_path, "blocked.kohakutr")
        other = _transfer_file(tmp_path, "other.kohakutr")
        occupying = try_fast(blocked, eligible=True)
        assert occupying.lease is not None
        waiting = threading.Event()
        other_done = threading.Event()
        errors: list[BaseException] = []
        real_acquire = sess_mod.acquire_family

        def spy_acquire(paths, operation):
            materialized = [Path(item) for item in paths]
            if any(item.resolve() == blocked.resolve() for item in materialized):
                waiting.set()
            return real_acquire(paths, operation)

        monkeypatch.setattr(sess_mod, "acquire_family", spy_acquire)

        def delete_blocked():
            try:
                adapter._op_delete_transfer({"session_path": str(blocked)})
            except BaseException as exc:
                errors.append(exc)

        def delete_other():
            waiting.wait(2)
            wait_pending(blocked, 1)
            try:
                adapter._op_delete_transfer({"session_path": str(other)})
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
        assert_idle(blocked)
        assert_idle(other)


class TestDeleteTransferDispatchOffload:
    def test_dispatch_enrolls_off_loop(self, tmp_path, monkeypatch):
        adapter, _engine = _adapter(monkeypatch, tmp_path)
        path = _transfer_file(tmp_path)
        threads: list[str] = []
        real_acquire = sess_mod.acquire_family
        loop_name = {"value": None}

        def spy_acquire(paths, operation):
            threads.append(threading.current_thread().name)
            return real_acquire(paths, operation)

        monkeypatch.setattr(sess_mod, "acquire_family", spy_acquire)

        async def run():
            loop_name["value"] = threading.current_thread().name
            out = await adapter._dispatch(
                _msg("delete_transfer", {"session_path": str(path)})
            )
            assert out == {"ok": True, "session_path": str(path.resolve())}

        asyncio.run(run())
        assert threads
        assert loop_name["value"] is not None
        assert all(name != loop_name["value"] for name in threads)
        assert not path.exists()
        assert_idle(path)

    def test_dispatch_active_guard_stays_on_loop(self, tmp_path, monkeypatch):
        adapter, engine = _adapter(monkeypatch, tmp_path)
        path, store = _open_transfer_store(tmp_path)
        engine._session_stores["live"] = store
        loop_calls: list[str] = []
        enrolled: list[object] = []

        def boom_acquire(*args, **kwargs):
            enrolled.append(args)
            raise AssertionError("dispatch must not enroll an active store")

        monkeypatch.setattr(sess_mod, "acquire_family", boom_acquire)

        async def run():
            loop_calls.append(threading.current_thread().name)
            out = await adapter._dispatch(
                _msg("delete_transfer", {"session_path": str(path)})
            )
            assert out["error"]["kind"] == "invalid"
            assert "active session store" in out["error"]["message"]

        try:
            asyncio.run(run())
            assert path.exists()
            assert enrolled == []
            assert loop_calls
        finally:
            store.close(update_status=False)

    def test_repeat_cancel_releases_after_admission_without_deleting(
        self, tmp_path, monkeypatch
    ):
        adapter, _engine = _adapter(monkeypatch, tmp_path)
        path = _transfer_file(tmp_path)
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        unlinks: list[str] = []
        real_unlink = Path.unlink

        def spy_unlink(self, *args, **kwargs):
            target = Path(self)
            if target == path or str(target).startswith(str(path)):
                unlinks.append(target.name)
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", spy_unlink)

        async def run():
            outer = asyncio.create_task(
                adapter._dispatch(_msg("delete_transfer", {"session_path": str(path)}))
            )
            await asyncio.to_thread(wait_pending, path, 1)
            assert path.exists()
            assert GATE.snapshot(path).legacy_active == 0
            outer.cancel()
            await asyncio.sleep(0)
            outer.cancel()
            await asyncio.sleep(0.05)
            assert not outer.done()
            assert path.exists()
            assert unlinks == []
            occupying.lease.finish(source_released=True)
            with pytest.raises(asyncio.CancelledError):
                await outer
            assert path.exists()
            assert unlinks == []
            assert_idle(path)

        try:
            asyncio.run(run())
        finally:
            occupying.lease.finish(source_released=True)


class TestRollbackResumeUnlinkEnrollment:
    async def test_graph_rollback_enrolls_before_unlink(self, tmp_path, monkeypatch):
        adapter, engine = _adapter(monkeypatch, tmp_path)
        path, store = _open_transfer_store(tmp_path)
        store.init_meta("target", "agent", "x", str(tmp_path), ["one"])
        store.close(update_status=False)
        wal = Path(f"{path}-wal")
        wal.write_bytes(b"wal")
        engine._session_stores = {"target": store}
        engine._creatures = {
            "one": SimpleNamespace(creature_id="one", graph_id="target"),
        }
        order: list[str] = []
        real_acquire = sess_mod.acquire_family
        real_unlink = Path.unlink

        def spy_acquire(paths, operation):
            order.append("enroll")
            lease = real_acquire(paths, operation)
            assert GATE.snapshot(path).legacy_active >= 1
            return lease

        def spy_unlink(self, *args, **kwargs):
            if Path(self) in {path, wal}:
                order.append("unlink")
                assert GATE.snapshot(path).legacy_active >= 1
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(sess_mod, "acquire_family", spy_acquire)
        monkeypatch.setattr(Path, "unlink", spy_unlink)
        result = await adapter._op_rollback_resume({"graph_id": "target"})
        assert result["ok"] is True
        assert "enroll" in order and "unlink" in order
        assert order.index("enroll") < order.index("unlink")
        assert not path.exists() and not wal.exists()
        assert_idle(path)

    async def test_expired_token_path_rollback_uses_enrolled_delete(
        self, tmp_path, monkeypatch
    ):
        adapter, _engine = _adapter(monkeypatch, tmp_path)
        path = _transfer_file(tmp_path)
        called: list[str] = []
        real_acquire = acquire_family

        def spy_acquire(paths, operation):
            called.append(operation)
            return real_acquire(paths, operation)

        monkeypatch.setattr(sess_mod, "acquire_family", spy_acquire)
        result = await adapter._op_rollback_resume(
            {
                "session_path": str(path),
                "resume_token": "unrecognized-attempt",
            }
        )
        assert result["ok"] is True
        assert called == ["delete_transfer"]
        assert not path.exists()
        assert_idle(path)
