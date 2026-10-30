"""Mutation worker cancellation and adapter output occupancy tests."""

import asyncio
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.laboratory.adapters.terrarium import file_mutations as mut
from kohakuterrarium.laboratory.adapters.terrarium import files as files_mod
from kohakuterrarium.laboratory.adapters.terrarium.file_mutations import (
    run_mutation_worker,
    write_bytes_enrolled,
)
from kohakuterrarium.session.occupancy import family
from kohakuterrarium.session.occupancy.gate import GATE
from tests.unit.laboratory.file_mutation_enrollment_helpers import (
    assert_idle,
    b64,
    make_files_adapter,
    msg,
    sha,
)


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    return make_files_adapter(monkeypatch, tmp_path)


class TestCancellationHoldsLease:
    def test_paused_replace_does_not_cleanup_on_cancel(
        self, adapter, tmp_path, monkeypatch
    ):
        payload = b"streamed"
        begin = asyncio.run(
            adapter._dispatch(
                msg(
                    "write_begin",
                    {
                        "scope": "recipe://cancel",
                        "path": "sess.kohakutr",
                        "total_size": len(payload),
                        "sha256": sha(payload),
                    },
                )
            )
        )
        tid = begin["transfer_id"]
        asyncio.run(
            adapter._dispatch(
                msg(
                    "write_chunk",
                    {"transfer_id": tid, "seq": 0, "bytes_b64": b64(payload)},
                )
            )
        )
        transfer = adapter._transfers[tid]
        target = transfer.target
        staging = transfer.staging
        hold, entered = threading.Event(), threading.Event()
        cleanup_calls: list[str] = []
        real_replace = os.replace
        real_cleanup = adapter._cleanup_after_commit

        def hold_replace(source, dest):
            entered.set()
            hold.wait(5)
            return real_replace(source, dest)

        async def spy_cleanup(transfer_id, staging_path):
            cleanup_calls.append(transfer_id)
            return await real_cleanup(transfer_id, staging_path)

        monkeypatch.setattr(mut.os, "replace", hold_replace)
        monkeypatch.setattr(adapter, "_cleanup_after_commit", spy_cleanup)

        async def run():
            task = asyncio.create_task(
                adapter._dispatch(msg("write_commit", {"transfer_id": tid}))
            )
            assert await asyncio.to_thread(entered.wait, 3)
            assert GATE.snapshot(target).legacy_active == 1
            assert GATE.snapshot(staging).legacy_active == 1
            task.cancel()
            await asyncio.sleep(0.05)
            assert GATE.snapshot(target).legacy_active == 1
            assert cleanup_calls == []
            assert tid in adapter._transfers
            hold.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert target.read_bytes() == payload
            assert tid not in adapter._transfers
            assert cleanup_calls == []
            assert_idle(target)
            assert_idle(staging)

        try:
            asyncio.run(run())
        finally:
            hold.set()

    def test_double_cancel_still_awaits_worker(self, adapter, tmp_path, monkeypatch):
        payload = b"again"
        begin = asyncio.run(
            adapter._dispatch(
                msg(
                    "write_begin",
                    {
                        "scope": "recipe://dbl",
                        "path": "sess.kohakutr",
                        "total_size": len(payload),
                        "sha256": sha(payload),
                    },
                )
            )
        )
        tid = begin["transfer_id"]
        asyncio.run(
            adapter._dispatch(
                msg(
                    "write_chunk",
                    {"transfer_id": tid, "seq": 0, "bytes_b64": b64(payload)},
                )
            )
        )
        transfer = adapter._transfers[tid]
        target = transfer.target
        hold, entered = threading.Event(), threading.Event()
        real_replace = os.replace

        def hold_replace(source, dest):
            entered.set()
            hold.wait(5)
            return real_replace(source, dest)

        monkeypatch.setattr(mut.os, "replace", hold_replace)

        async def run():
            outer = asyncio.create_task(
                adapter._dispatch(msg("write_commit", {"transfer_id": tid}))
            )
            assert await asyncio.to_thread(entered.wait, 3)
            outer.cancel()
            await asyncio.sleep(0)
            outer.cancel()
            await asyncio.sleep(0.05)
            assert GATE.snapshot(target).legacy_active == 1
            hold.set()
            with pytest.raises(asyncio.CancelledError):
                await outer
            assert target.read_bytes() == payload
            assert tid not in adapter._transfers
            assert_idle(target)

        try:
            asyncio.run(run())
        finally:
            hold.set()

    def test_run_mutation_worker_preserves_cancelled_error_with_worker_cause(
        self, tmp_path
    ):
        hold = threading.Event()
        entered = threading.Event()

        def boom():
            entered.set()
            hold.wait(5)
            raise OSError("worker boom")

        async def run():
            task = asyncio.create_task(run_mutation_worker(boom))
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            hold.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert isinstance(caught.value.__cause__, OSError)
            assert "worker boom" in str(caught.value.__cause__)

        try:
            asyncio.run(run())
        finally:
            hold.set()


class TestAdapterOutputsUnchanged:
    async def test_stream_commit_output(self, adapter, tmp_path):
        payload = b"chunked-body"
        begin = await adapter._dispatch(
            msg(
                "write_begin",
                {
                    "scope": "recipe://stream",
                    "path": "sess.kohakutr",
                    "total_size": len(payload),
                    "sha256": sha(payload),
                },
            )
        )
        tid = begin["transfer_id"]
        await adapter._dispatch(
            msg(
                "write_chunk",
                {"transfer_id": tid, "seq": 0, "bytes_b64": b64(payload)},
            )
        )
        commit = await adapter._dispatch(msg("write_commit", {"transfer_id": tid}))
        assert commit == {"written": len(payload), "sha256": sha(payload)}
        target = tmp_path / "kt-config" / "recipes" / "stream" / "sess.kohakutr"
        assert target.read_bytes() == payload
        assert_idle(target)

    async def test_active_store_rejection_does_not_gate_saved_files(
        self, adapter, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        saved = tmp_path / "resume" / "saved.kohakutr"
        saved.parent.mkdir()
        saved.write_bytes(b"saved")
        active = tmp_path / "resume" / "active.kohakutr"
        active.write_bytes(b"active")
        adapter._engine._session_stores["sid"] = SimpleNamespace(path=active)
        enrolled: list[str] = []
        real_acquire = family.acquire_legacy

        def spy_acquire(target, operation):
            enrolled.append(Path(target).name)
            return real_acquire(target, operation)

        monkeypatch.setattr(family, "acquire_legacy", spy_acquire)
        out = await adapter._dispatch(
            msg(
                "write",
                {
                    "scope": "config://",
                    "path": "resume/saved.kohakutr",
                    "bytes_b64": b64(b"next"),
                },
            )
        )
        assert out == {"written": 4, "sha256": sha(b"next")}
        assert enrolled == ["saved.kohakutr"]
        denied = await adapter._dispatch(
            msg(
                "write",
                {
                    "scope": "config://",
                    "path": "resume/active.kohakutr",
                    "bytes_b64": b64(b"next"),
                },
            )
        )
        assert denied["error"]["kind"] == "invalid"
        assert "active session store" in denied["error"]["message"]
        assert enrolled == ["saved.kohakutr"]
        assert_idle(saved)

    async def test_permissionerror_idempotent_commit_still_skips(
        self, adapter, monkeypatch, tmp_path
    ):
        payload = b"same"
        target_dir = tmp_path / "kt-config" / "recipes" / "idem"
        target_dir.mkdir(parents=True)
        target = target_dir / "sess.kohakutr"
        target.write_bytes(payload)
        begin = await adapter._dispatch(
            msg(
                "write_begin",
                {
                    "scope": "recipe://idem",
                    "path": "sess.kohakutr",
                    "total_size": len(payload),
                    "sha256": sha(payload),
                },
            )
        )
        tid = begin["transfer_id"]
        await adapter._dispatch(
            msg(
                "write_chunk",
                {"transfer_id": tid, "seq": 0, "bytes_b64": b64(payload)},
            )
        )

        async def not_idempotent(self, transfer, actual_sha):
            return False

        monkeypatch.setattr(
            files_mod.TerrariumFilesAdapter,
            "_commit_is_idempotent",
            not_idempotent,
        )

        def boom_replace(*args, **kwargs):
            raise PermissionError(13, "locked")

        monkeypatch.setattr(mut.os, "replace", boom_replace)
        commit = await adapter._dispatch(msg("write_commit", {"transfer_id": tid}))
        assert commit["written"] == len(payload)
        assert commit["sha256"] == sha(payload)
        assert "skipped_replace" in commit
        assert target.read_bytes() == payload
        assert_idle(target)


class TestWorkerOwnsLeaseNotEventLoop:
    def test_run_mutation_worker_does_not_acquire_on_loop(self, tmp_path, monkeypatch):
        path = tmp_path / "loop.bin"
        threads: list[str] = []
        real_acquire = family.acquire_legacy
        loop_ident = None

        def spy_acquire(target, operation):
            threads.append(threading.current_thread().name)
            return real_acquire(target, operation)

        monkeypatch.setattr(family, "acquire_legacy", spy_acquire)

        async def run():
            nonlocal loop_ident
            loop_ident = threading.current_thread().name
            await run_mutation_worker(write_bytes_enrolled, path, b"x")

        asyncio.run(run())
        assert threads
        assert loop_ident is not None
        assert all(name != loop_ident for name in threads)
        assert_idle(path)
