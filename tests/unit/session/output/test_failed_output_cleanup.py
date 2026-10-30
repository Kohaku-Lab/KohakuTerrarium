"""Failed migration/fork output deletion observes NEW admission after Store close."""

import asyncio
import threading
from pathlib import Path

import pytest

from kohakuterrarium.session import migrations
from kohakuterrarium.session.occupancy.gate import GATE, ResourceReleaseError, try_fast
from kohakuterrarium.session.store import SessionStore


@pytest.fixture(params=["migration", "fork"])
def failed_output(request, tmp_path, monkeypatch):
    source_path = tmp_path / "source.kohakutr"
    target = (
        tmp_path / "source.kohakutr.v2"
        if request.param == "migration"
        else tmp_path / "child.kohakutr"
    )
    source = SessionStore(source_path)
    source.init_meta("source", "agent", "", str(tmp_path), ["alice"])
    source.append_event("alice", "user_input", {"content": "keep source"})
    source.meta["format_version"] = 1
    source.flush()
    if request.param == "migration":
        source.close(update_status=False)
    failure = OSError("output flush failed")
    real_flush = SessionStore.flush

    def fail_flush(store):
        if Path(store.path) == target:
            raise failure
        return real_flush(store)

    monkeypatch.setattr(SessionStore, "flush", fail_flush)

    def run():
        if request.param == "migration":
            return migrations.migrate(source_path, 2)
        return source.fork(str(target), at_event_id=1)

    try:
        yield run, source_path, target, failure, request.param
    finally:
        source.close(update_status=False)


def assert_primary(error, primary, kind):
    if kind == "migration":
        assert isinstance(error, RuntimeError)
        assert error.__cause__ is primary
    else:
        assert error is primary


@pytest.mark.asyncio
async def test_new_blocks_failed_output_delete_and_cancel_does_not_end_worker(
    failed_output, monkeypatch
):
    run, source, target, primary, kind = failed_output
    closed, done = threading.Event(), threading.Event()
    leases, errors, deleted = [], [], []
    original_close, original_unlink = SessionStore.close, Path.unlink

    def close(store, *args, **kwargs):
        original_close(store, *args, **kwargs)
        if Path(store.path) == target:
            admission = try_fast(target, eligible=True)
            assert admission.lease is not None
            leases.append(admission.lease)
            closed.set()

    def unlink(path, *args, **kwargs):
        if path == target:
            assert GATE.snapshot(target).new_active == 0, "delete bypassed NEW"
            assert GATE.snapshot(target).legacy_active >= 1
            deleted.append(path)
        return original_unlink(path, *args, **kwargs)

    def work():
        try:
            run()
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    monkeypatch.setattr(SessionStore, "close", close)
    monkeypatch.setattr(Path, "unlink", unlink)
    task = asyncio.create_task(asyncio.to_thread(work))
    try:
        assert await asyncio.to_thread(closed.wait, 10)
        for _ in range(200):
            if GATE.snapshot(target).legacy_pending or done.is_set():
                break
            await asyncio.sleep(0.005)
        assert GATE.snapshot(target).legacy_pending == 1
        assert target.exists() and not deleted
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not done.is_set()
        assert GATE.snapshot(target).new_active == 1
        unrelated = try_fast(target.parent / "other.kohakutr", eligible=True)
        assert unrelated.lease is not None
        unrelated.lease.finish(source_released=True)
    finally:
        for lease in leases:
            lease.finish(source_released=True)
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(done.wait, 10)
    assert len(errors) == 1
    assert_primary(errors[0], primary, kind)
    assert deleted == [target] and not target.exists()
    assert source.exists()
    assert not GATE.snapshot(target).registered


@pytest.mark.parametrize("uncertain", [False, True])
def test_delete_failure_or_uncertain_admission_preserves_output(
    failed_output, monkeypatch, uncertain
):
    run, source, target, primary, kind = failed_output
    original_close, original_unlink = SessionStore.close, Path.unlink
    attempts = []

    def close(store, *args, **kwargs):
        original_close(store, *args, **kwargs)
        if uncertain and Path(store.path) == target:
            admission = try_fast(target, eligible=True)
            assert admission.lease is not None
            admission.lease.finish(source_released=False)

    def unlink(path, *args, **kwargs):
        if path == target:
            attempts.append(path)
            assert GATE.snapshot(target).legacy_active >= 1
            raise PermissionError("output retained")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(SessionStore, "close", close)
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(Exception) as caught:
        run()
    if uncertain:
        assert isinstance(caught.value, ResourceReleaseError)
        assert not attempts
        assert GATE.snapshot(target).new_uncertain
        assert caught.value.__context__ is primary
    else:
        assert_primary(caught.value, primary, kind)
        assert attempts == [target]
        assert not GATE.snapshot(target).registered
    assert source.exists() and target.exists()
