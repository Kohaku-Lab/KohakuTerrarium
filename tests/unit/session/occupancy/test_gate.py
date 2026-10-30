"""Unit tests for :mod:`kohakuterrarium.session.occupancy.gate`."""

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import kohakuterrarium.session.occupancy.gate as resource_gate_mod
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    KIND_FAST,
    KIND_LEGACY,
    REASON_FAST_ACTIVE,
    REASON_LEGACY_ENROLLED,
    REASON_LEGACY_PENDING,
    REASON_LEGACY_RELEASE_UNCERTAIN,
    REASON_NOT_ELIGIBLE,
    REASON_OK,
    Admission,
    OccupancySnapshot,
    ResourceGate,
    ResourceLease,
    ResourceReleaseError,
    acquire_legacy,
    resource_gate,
    try_fast,
)


@pytest.fixture
def gate():
    return ResourceGate(wait_log_interval=0.05)


def _wait_pending(gate: ResourceGate, path, count: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if gate.snapshot(path).legacy_pending == count:
            return
        time.sleep(0.01)
    raise AssertionError(f"pending occupancy did not reach {count}")


class TestModuleSurface:
    def test_global_gate_and_wrappers(self, tmp_path):
        path = tmp_path / "global.kohakutr"
        assert resource_gate is GATE
        lease = acquire_legacy(path, "open")
        try:
            denied = try_fast(path, eligible=True)
            assert denied.lease is None
            assert denied.reason == REASON_LEGACY_ENROLLED
        finally:
            lease.finish(source_released=True)
        admitted = try_fast(path, eligible=True)
        assert admitted.reason == REASON_OK
        admitted.lease.finish(source_released=True)

    def test_try_fast_defaults_ineligible(self, tmp_path):
        path = tmp_path / "closed.kohakutr"
        denied = try_fast(path)
        assert denied.lease is None
        assert denied.reason == REASON_NOT_ELIGIBLE
        snap = GATE.snapshot(path)
        assert snap.registered is False
        assert snap.new_active == 0


class TestPathIdentity:
    def test_alias_identity_shares_occupancy(self, gate, tmp_path, monkeypatch):
        path = tmp_path / "alias.kohakutr"
        path.write_bytes(b"")
        lease = gate.acquire_legacy(path, "open")
        try:
            monkeypatch.chdir(tmp_path)
            relative = gate.try_fast("alias.kohakutr", eligible=True)
            uri = gate.try_fast(path.as_uri(), eligible=True)
            expanded = gate.try_fast(str(path).replace("\\", "/"), eligible=True)
            assert relative.reason == REASON_LEGACY_ENROLLED
            assert uri.reason == REASON_LEGACY_ENROLLED
            assert expanded.reason == REASON_LEGACY_ENROLLED
            if os.name == "nt":
                flipped = str(path)
                other_case = (
                    flipped.upper() if flipped != flipped.upper() else flipped.lower()
                )
                cased = gate.try_fast(other_case, eligible=True)
                assert cased.reason == REASON_LEGACY_ENROLLED
        finally:
            lease.finish(source_released=True)


class TestLegacyNesting:
    def test_legacy_nests_without_waiting_on_legacy(self, gate, tmp_path):
        path = tmp_path / "nest.kohakutr"
        first = gate.acquire_legacy(path, "open")
        second = gate.acquire_legacy(path, "fork")
        snap = gate.snapshot(path)
        assert snap.legacy_active == 2
        assert snap.legacy_pending == 0
        first.finish(source_released=True)
        assert gate.snapshot(path).legacy_active == 1
        second.finish(source_released=True)
        assert gate.snapshot(path).registered is False


class TestFastIsolation:
    def test_fast_isolated_from_enrolled_legacy(self, gate, tmp_path):
        path = tmp_path / "iso.kohakutr"
        lease = gate.acquire_legacy(path, "open")
        try:
            denied = gate.try_fast(path, eligible=True)
            assert denied == Admission(None, REASON_LEGACY_ENROLLED)
        finally:
            lease.finish(source_released=True)
        admitted = gate.try_fast(path, eligible=True)
        assert admitted.reason == REASON_OK
        assert admitted.lease is not None
        assert admitted.lease.kind == KIND_FAST
        admitted.lease.finish(source_released=True)

    def test_ineligible_never_enrolls(self, gate, tmp_path):
        path = tmp_path / "off.kohakutr"
        denied = gate.try_fast(path, eligible=False)
        assert denied.reason == REASON_NOT_ELIGIBLE
        assert gate.snapshot(path).registered is False

    def test_second_fast_declines_fast_active(self, gate, tmp_path):
        path = tmp_path / "one-new.kohakutr"
        first = gate.try_fast(path, eligible=True)
        assert first.reason == REASON_OK
        assert first.lease is not None
        second = gate.try_fast(path, eligible=True)
        assert second.lease is None
        assert second.reason == REASON_FAST_ACTIVE
        assert gate.snapshot(path).new_active == 1
        first.lease.finish(source_released=True)
        third = gate.try_fast(path, eligible=True)
        assert third.reason == REASON_OK
        assert third.lease is not None
        third.lease.finish(source_released=True)


class TestPendingPriority:
    def test_pending_blocks_new_before_wait(self, gate, tmp_path):
        path = tmp_path / "priority.kohakutr"
        fast = gate.try_fast(path, eligible=True)
        assert fast.lease is not None
        started = threading.Event()
        acquired = threading.Event()
        lease_box: list[ResourceLease] = []

        def enroll():
            started.set()
            lease_box.append(gate.acquire_legacy(path, "open"))
            acquired.set()

        worker = threading.Thread(target=enroll)
        worker.start()
        assert started.wait(2)
        _wait_pending(gate, path, 1)
        denied = gate.try_fast(path, eligible=True)
        assert denied.reason == REASON_LEGACY_PENDING
        fast.lease.finish(source_released=True)
        assert acquired.wait(2)
        worker.join(2)
        assert not worker.is_alive()
        assert lease_box[0].kind == KIND_LEGACY
        lease_box[0].finish(source_released=True)

    def test_two_orders_legacy_then_fast_and_fast_then_legacy(self, gate, tmp_path):
        path = tmp_path / "orders.kohakutr"
        legacy = gate.acquire_legacy(path, "open")
        denied = gate.try_fast(path, eligible=True)
        assert denied.reason == REASON_LEGACY_ENROLLED
        legacy.finish(source_released=True)

        fast = gate.try_fast(path, eligible=True)
        assert fast.lease is not None
        started = threading.Barrier(2)
        done = threading.Event()
        held: list[ResourceLease] = []

        def enroll():
            started.wait()
            held.append(gate.acquire_legacy(path, "resume"))
            done.set()

        worker = threading.Thread(target=enroll)
        worker.start()
        started.wait()
        _wait_pending(gate, path, 1)
        still_denied = gate.try_fast(path, eligible=True)
        assert still_denied.reason == REASON_LEGACY_PENDING
        fast.lease.finish(source_released=True)
        assert done.wait(2)
        worker.join(2)
        held[0].finish(source_released=True)


class TestWaitDoesNotHoldLock:
    def test_wait_releases_lock_so_other_files_proceed(self, gate, tmp_path):
        blocked = tmp_path / "blocked.kohakutr"
        other = tmp_path / "other.kohakutr"
        occupying = gate.try_fast(blocked, eligible=True)
        assert occupying.lease is not None
        waiting = threading.Event()
        acquired = threading.Event()
        other_done = threading.Event()
        held: list[ResourceLease] = []

        def wait_for_new():
            waiting.set()
            held.append(gate.acquire_legacy(blocked, "open"))
            acquired.set()

        def other_file():
            waiting.wait(2)
            lease = gate.acquire_legacy(other, "open")
            other_done.set()
            lease.finish(source_released=True)

        waiter = threading.Thread(target=wait_for_new)
        sibling = threading.Thread(target=other_file)
        waiter.start()
        sibling.start()
        assert other_done.wait(2)
        assert not acquired.is_set()
        occupying.lease.finish(source_released=True)
        assert acquired.wait(2)
        waiter.join(2)
        sibling.join(2)
        held[0].finish(source_released=True)

    def test_wait_log_does_not_hold_lock(self, tmp_path, monkeypatch):
        gate = ResourceGate(wait_log_interval=0.05)
        blocked = tmp_path / "blocked.kohakutr"
        other = tmp_path / "other.kohakutr"
        occupying = gate.try_fast(blocked, eligible=True)
        assert occupying.lease is not None
        in_log = threading.Event()
        proceed = threading.Event()
        other_done = threading.Event()
        held: list[ResourceLease] = []

        def blocking_debug(*args, **kwargs):
            in_log.set()
            proceed.wait(2)

        monkeypatch.setattr(resource_gate_mod.logger, "debug", blocking_debug)

        def wait_for_new():
            held.append(gate.acquire_legacy(blocked, "open"))

        def other_file():
            assert in_log.wait(2)
            lease = gate.acquire_legacy(other, "open")
            other_done.set()
            lease.finish(source_released=True)

        waiter = threading.Thread(target=wait_for_new)
        sibling = threading.Thread(target=other_file)
        waiter.start()
        sibling.start()
        assert in_log.wait(2)
        assert other_done.wait(2)
        assert gate.snapshot(blocked).legacy_pending == 1
        proceed.set()
        occupying.lease.finish(source_released=True)
        waiter.join(2)
        sibling.join(2)
        assert not waiter.is_alive()
        held[0].finish(source_released=True)


class TestFinishSemantics:
    def test_finish_idempotent_and_stale_token_ignored(self, gate, tmp_path):
        path = tmp_path / "stale.kohakutr"
        first = gate.acquire_legacy(path, "open")
        first.finish(source_released=True)
        first.finish(source_released=True)
        first.finish(source_released=False)
        second = gate.acquire_legacy(path, "open")
        assert gate.snapshot(path).legacy_active == 1
        assert gate.snapshot(path).legacy_uncertain is False
        first.finish(source_released=False)
        assert gate.snapshot(path).legacy_active == 1
        second.finish(source_released=True)
        assert gate.snapshot(path).registered is False

    def test_successful_zero_occupancy_retires_record(self, gate, tmp_path):
        path = tmp_path / "retire.kohakutr"
        a = gate.acquire_legacy(path, "open")
        b = gate.try_fast(tmp_path / "other.kohakutr", eligible=True)
        assert b.lease is not None
        assert gate.snapshot(path).registered is True
        a.finish(source_released=True)
        assert gate.snapshot(path).registered is False
        b.lease.finish(source_released=True)
        assert gate.snapshot(tmp_path / "other.kohakutr").registered is False

    def test_finished_tokens_do_not_accumulate_under_long_owner(self, gate, tmp_path):
        path = tmp_path / "owner.kohakutr"
        owner = gate.acquire_legacy(path, "open")
        for _ in range(32):
            companion = gate.acquire_legacy(path, "fork")
            companion.finish(source_released=True)
        with gate._cond:
            rec = gate._records[owner._key]
            assert len(rec.leases) == 1
            assert owner.token in rec.leases
        owner.finish(source_released=True)
        assert gate.snapshot(path).registered is False


class TestUncertainty:
    def test_legacy_uncertainty_declines_fast_but_allows_legacy(self, gate, tmp_path):
        path = tmp_path / "legacy-uncertain.kohakutr"
        lease = gate.acquire_legacy(path, "open")
        lease.finish(source_released=False)
        snap = gate.snapshot(path)
        assert snap.legacy_uncertain is True
        assert snap.legacy_active == 0
        denied = gate.try_fast(path, eligible=True)
        assert denied.reason == REASON_LEGACY_RELEASE_UNCERTAIN
        nested = gate.acquire_legacy(path, "open")
        nested.finish(source_released=True)
        still = gate.try_fast(path, eligible=True)
        assert still.reason == REASON_LEGACY_RELEASE_UNCERTAIN
        lease.finish(source_released=True)
        assert gate.snapshot(path).legacy_uncertain is True

    def test_new_uncertainty_raises_on_legacy_and_fast(self, gate, tmp_path):
        path = tmp_path / "new-uncertain.kohakutr"
        admitted = gate.try_fast(path, eligible=True)
        admitted.lease.finish(source_released=False)
        with pytest.raises(ResourceReleaseError):
            gate.acquire_legacy(path, "open")
        with pytest.raises(ResourceReleaseError):
            gate.try_fast(path, eligible=True)
        snap = gate.snapshot(path)
        assert snap.new_uncertain is True
        assert snap.registered is True
        admitted.lease.finish(source_released=True)
        with pytest.raises(ResourceReleaseError):
            gate.acquire_legacy(path, "open")

    def test_new_uncertainty_during_wait_raises_and_does_not_enroll(
        self, gate, tmp_path
    ):
        path = tmp_path / "wait-uncertain.kohakutr"
        occupying = gate.try_fast(path, eligible=True)
        errors: list[BaseException] = []
        started = threading.Event()

        def enroll():
            started.set()
            try:
                gate.acquire_legacy(path, "open")
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=enroll)
        worker.start()
        assert started.wait(2)
        _wait_pending(gate, path, 1)
        occupying.lease.finish(source_released=False)
        worker.join(2)
        assert not worker.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], ResourceReleaseError)
        snap = gate.snapshot(path)
        assert snap.legacy_active == 0
        assert snap.legacy_pending == 0
        assert snap.new_uncertain is True

    def test_unknown_new_occupancy_stays_blocking(self, gate, tmp_path):
        path = tmp_path / "abandoned.kohakutr"
        admitted = gate.try_fast(path, eligible=True)
        assert admitted.lease is not None
        token = admitted.lease.token
        kind = admitted.lease.kind
        key = admitted.lease._key
        del admitted
        snap = gate.snapshot(path)
        assert snap.new_active == 1
        assert snap.registered is True
        denied = gate.try_fast(path, eligible=True)
        assert denied.lease is None
        assert denied.reason == REASON_FAST_ACTIVE
        ResourceLease(gate, token, kind, key).finish(source_released=True)
        assert gate.snapshot(path).registered is False


class TestCanceledWorkers:
    def test_canceled_to_thread_worker_keeps_lease_until_finally(self, gate, tmp_path):
        path = tmp_path / "cancel.kohakutr"
        hold = threading.Event()
        entered = threading.Event()
        finished = threading.Event()

        def hold_legacy():
            lease = gate.acquire_legacy(path, "open")
            entered.set()
            try:
                hold.wait(5)
            finally:
                lease.finish(source_released=True)
                finished.set()

        async def run():
            task = asyncio.create_task(asyncio.to_thread(hold_legacy))
            assert await asyncio.to_thread(entered.wait, 3)
            assert gate.snapshot(path).legacy_active == 1
            task.cancel()
            await asyncio.sleep(0.05)
            assert not finished.is_set()
            assert gate.snapshot(path).legacy_active == 1
            hold.set()
            await asyncio.gather(task, return_exceptions=True)
            assert finished.wait(2)
            assert gate.snapshot(path).registered is False

        asyncio.run(run())

    def test_legacy_and_fast_canceled_workers(self, gate, tmp_path):
        path = tmp_path / "both.kohakutr"
        fast_path = tmp_path / "fast.kohakutr"
        hold = threading.Event()
        entered = threading.Barrier(3)

        def hold_kind(kind: str):
            if kind == KIND_LEGACY:
                lease = gate.acquire_legacy(path, "open")
            else:
                lease = gate.try_fast(fast_path, eligible=True).lease
            entered.wait()
            try:
                hold.wait(5)
            finally:
                lease.finish(source_released=True)

        async def run():
            legacy_task = asyncio.create_task(asyncio.to_thread(hold_kind, KIND_LEGACY))
            fast_task = asyncio.create_task(asyncio.to_thread(hold_kind, KIND_FAST))
            await asyncio.to_thread(entered.wait, 3)
            assert gate.snapshot(path).legacy_active == 1
            assert gate.snapshot(fast_path).new_active == 1
            legacy_task.cancel()
            fast_task.cancel()
            await asyncio.sleep(0.05)
            assert gate.snapshot(path).legacy_active == 1
            assert gate.snapshot(fast_path).new_active == 1
            hold.set()
            await asyncio.gather(legacy_task, fast_task, return_exceptions=True)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if (
                    not gate.snapshot(path).registered
                    and not gate.snapshot(fast_path).registered
                ):
                    break
                await asyncio.sleep(0.01)
            assert gate.snapshot(path).registered is False
            assert gate.snapshot(fast_path).registered is False

        asyncio.run(run())


class TestNoContextManager:
    def test_lease_has_no_context_manager(self, gate, tmp_path):
        path = tmp_path / "nocontext.kohakutr"
        lease = gate.acquire_legacy(path, "open")
        try:
            assert not hasattr(lease, "__enter__")
            assert not hasattr(ResourceLease, "__enter__")
        finally:
            lease.finish(source_released=True)


class TestDiagnostics:
    def test_wait_log_interval_is_configurable(self, tmp_path, caplog, monkeypatch):
        path = tmp_path / "diag.kohakutr"
        gate = ResourceGate(wait_log_interval=0.05)
        occupying = gate.try_fast(path, eligible=True)
        log = logging.getLogger("kohakuterrarium.session.occupancy.gate")
        log.setLevel(logging.DEBUG)
        caplog.set_level(logging.DEBUG, logger="kohakuterrarium.session.occupancy.gate")
        seen: list[dict] = []
        real_debug = resource_gate_mod.logger.debug

        def capturing_debug(*args, **kwargs):
            seen.append(kwargs)
            return real_debug(*args, **kwargs)

        monkeypatch.setattr(resource_gate_mod.logger, "debug", capturing_debug)
        started = threading.Event()
        done = threading.Event()

        def enroll():
            started.set()
            lease = gate.acquire_legacy(path, "open")
            lease.finish(source_released=True)
            done.set()

        worker = threading.Thread(target=enroll)
        worker.start()
        assert started.wait(2)
        _wait_pending(gate, path, 1)
        time.sleep(0.12)
        occupying.lease.finish(source_released=True)
        assert done.wait(2)
        worker.join(2)
        assert "Waiting for NEW occupancy to drain" in caplog.text
        elapsed = [kwargs["elapsed"] for kwargs in seen if "elapsed" in kwargs]
        assert elapsed
        assert all(value >= 0 for value in elapsed)


class TestConcurrentDistinctFiles:
    def test_executor_across_files_does_not_serialize(self, gate, tmp_path):
        paths = [tmp_path / f"f{i}.kohakutr" for i in range(4)]
        barrier = threading.Barrier(len(paths))

        def cycle(path):
            barrier.wait()
            lease = gate.acquire_legacy(path, "open")
            lease.finish(source_released=True)
            admitted = gate.try_fast(path, eligible=True)
            admitted.lease.finish(source_released=True)

        with ThreadPoolExecutor(len(paths)) as pool:
            list(pool.map(cycle, paths))
        for path in paths:
            assert gate.snapshot(path).registered is False


class TestFaultDistinctions:
    def test_resource_release_error_is_runtime_error(self):
        assert issubclass(ResourceReleaseError, RuntimeError)
        assert ResourceReleaseError is not RuntimeError

    def test_snapshot_defaults_unregistered(self, gate, tmp_path):
        snap = gate.snapshot(tmp_path / "missing.kohakutr")
        assert snap == OccupancySnapshot()
        assert snap.registered is False
