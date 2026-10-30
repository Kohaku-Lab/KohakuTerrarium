"""Cancellation edges for mutation workers, bundle publish, and source acquire."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest

from kohakuterrarium.laboratory.adapters import terrarium_file_mutations as mut
from kohakuterrarium.laboratory.adapters import terrarium_files as files_mod
from kohakuterrarium.laboratory.adapters.terrarium_file_mutations import (
    replace_enrolled,
    run_mutation_worker,
)
from kohakuterrarium.session.occupancy import mutation as worker
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    ResourceReleaseError,
    try_fast,
)
from tests.unit.laboratory.file_mutation_enrollment_helpers import (
    assert_idle,
    b64,
    make_files_adapter,
    msg,
    plain_file,
    sha,
)


def _force_cancel_after_worker_done(monkeypatch) -> None:
    """Raise CancelledError from the first shield after the worker is done."""

    real_shield = worker.asyncio.shield
    first = {"used": False}

    def shield_after_done(aw):
        inner = asyncio.ensure_future(aw)

        async def wait_then_cancel():
            if not inner.done():
                await asyncio.wait({inner})
            if not first["used"]:
                first["used"] = True
                raise asyncio.CancelledError()
            return await real_shield(inner)

        return wait_then_cancel()

    monkeypatch.setattr(worker.asyncio, "shield", shield_after_done)


class TestBundleCancelHoldsPublish:
    def test_repeat_cancel_during_paused_replace_defers_staging_rmtree(
        self, monkeypatch, tmp_path
    ):
        adapter = make_files_adapter(monkeypatch, tmp_path)
        payload = b"published-bundle"
        files = {"a.txt": [sha(payload), b64(payload)]}
        hold, entered = threading.Event(), threading.Event()
        rmtree_calls: list[object] = []
        paths: dict[str, Path] = {}
        real_replace = os.replace
        real_rmtree = files_mod.shutil.rmtree

        def hold_replace(source, dest):
            paths["source"] = Path(source)
            paths["target"] = Path(dest)
            entered.set()
            hold.wait(5)
            return real_replace(source, dest)

        def spy_rmtree(path, *args, **kwargs):
            rmtree_calls.append(path)
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(mut.os, "replace", hold_replace)
        monkeypatch.setattr(files_mod.shutil, "rmtree", spy_rmtree)

        async def run():
            outer = asyncio.create_task(
                adapter._dispatch(
                    msg(
                        "push_bundle",
                        {"scope": "recipe://bndl-cancel", "files": files},
                    )
                )
            )
            assert await asyncio.to_thread(entered.wait, 3)
            source = paths["source"]
            target = paths["target"]
            staging_dir = source.parent
            assert GATE.snapshot(target).legacy_active == 1
            assert GATE.snapshot(source).legacy_active == 1
            assert staging_dir.exists()
            assert rmtree_calls == []
            outer.cancel()
            await asyncio.sleep(0)
            outer.cancel()
            await asyncio.sleep(0.05)
            assert GATE.snapshot(target).legacy_active == 1
            assert GATE.snapshot(source).legacy_active == 1
            assert rmtree_calls == []
            assert staging_dir.exists()
            hold.set()
            with pytest.raises(asyncio.CancelledError):
                await outer
            assert target.read_bytes() == payload
            assert rmtree_calls
            assert not staging_dir.exists()
            assert_idle(target)
            assert_idle(source)

        try:
            asyncio.run(run())
        finally:
            hold.set()


class TestStreamCancelFailedReplace:
    def test_failed_replace_on_cancel_retains_transfer_for_retry(
        self, monkeypatch, tmp_path
    ):
        adapter = make_files_adapter(monkeypatch, tmp_path)
        payload = b"retry-bytes"
        begin = asyncio.run(
            adapter._dispatch(
                msg(
                    "write_begin",
                    {
                        "scope": "recipe://stream-fail",
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

        def fail_replace(source, dest):
            entered.set()
            hold.wait(5)
            raise OSError("replace failed")

        async def boom_cleanup(transfer_id, staging_path):
            raise AssertionError("must not cleanup a failed replace")

        monkeypatch.setattr(mut.os, "replace", fail_replace)
        monkeypatch.setattr(adapter, "_cleanup_after_commit", boom_cleanup)

        async def run():
            outer = asyncio.create_task(
                adapter._dispatch(msg("write_commit", {"transfer_id": tid}))
            )
            assert await asyncio.to_thread(entered.wait, 3)
            assert GATE.snapshot(target).legacy_active == 1
            assert GATE.snapshot(staging).legacy_active == 1
            outer.cancel()
            await asyncio.sleep(0.05)
            assert tid in adapter._transfers
            hold.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await outer
            assert isinstance(caught.value.__cause__, OSError)
            assert "replace failed" in str(caught.value.__cause__)
            # Failed worker skips on_success, so the transfer remains for retry.
            assert tid in adapter._transfers
            assert adapter._transfers[tid] is transfer
            assert staging.exists()
            assert not target.exists() or target.read_bytes() != payload
            assert_idle(target)
            assert_idle(staging)

        try:
            asyncio.run(run())
        finally:
            hold.set()


class TestRunMutationWorkerAlreadyDone:
    def test_cancel_after_success_runs_on_success_once_on_loop(self, monkeypatch):
        calls: list[str] = []
        loop_ids: list[int] = []

        def on_success() -> None:
            calls.append(threading.current_thread().name)
            loop_ids.append(id(asyncio.get_running_loop()))

        _force_cancel_after_worker_done(monkeypatch)

        async def run():
            loop_thread = threading.current_thread().name
            loop_id = id(asyncio.get_running_loop())
            with pytest.raises(asyncio.CancelledError):
                await run_mutation_worker(lambda: "ok", on_success=on_success)
            assert calls == [loop_thread]
            assert loop_ids == [loop_id]

        asyncio.run(run())

    def test_success_on_success_runs_on_loop_once(self):
        calls: list[str] = []

        def on_success() -> None:
            calls.append(threading.current_thread().name)
            asyncio.get_running_loop()

        async def run():
            loop_thread = threading.current_thread().name
            result = await run_mutation_worker(lambda: 7, on_success=on_success)
            assert result == 7
            assert calls == [loop_thread]

        asyncio.run(run())

    def test_cancel_after_failure_skips_on_success_and_keeps_cause(self, monkeypatch):
        calls: list[str] = []

        def on_success() -> None:
            calls.append("on_success")

        def boom() -> None:
            raise OSError("already failed")

        _force_cancel_after_worker_done(monkeypatch)

        async def run():
            with pytest.raises(asyncio.CancelledError) as caught:
                await run_mutation_worker(boom, on_success=on_success)
            assert calls == []
            assert isinstance(caught.value.__cause__, OSError)
            assert "already failed" in str(caught.value.__cause__)

        asyncio.run(run())


class TestSourceAcquireReleasesTarget:
    def test_new_uncertain_source_releases_target(self, tmp_path):
        src = plain_file(tmp_path, "src.bin", b"new")
        dst = plain_file(tmp_path, "dst.bin", b"old")
        occupying = try_fast(src, eligible=True)
        assert occupying.lease is not None
        occupying.lease.finish(source_released=False)
        with pytest.raises(ResourceReleaseError):
            replace_enrolled(src, dst)
        assert dst.read_bytes() == b"old"
        assert src.exists()
        assert_idle(dst)
        snap = GATE.snapshot(src)
        assert snap.new_uncertain is True
        assert snap.legacy_active == 0
        with pytest.raises(ResourceReleaseError):
            try_fast(src, eligible=True)
