"""Laboratory exact-path mutation occupancy around file publication."""

import os
import threading
from pathlib import Path

import pytest

from kohakuterrarium.laboratory.adapters.terrarium import file_mutations as mut
from kohakuterrarium.laboratory.adapters.terrarium.file_mutations import (
    replace_enrolled,
    unlink_enrolled,
    write_bytes_enrolled,
)
from kohakuterrarium.session.occupancy import family
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_LEGACY_ENROLLED,
    REASON_LEGACY_PENDING,
    REASON_NOT_ELIGIBLE,
    ResourceReleaseError,
    try_fast,
)
from tests.unit.laboratory.file_mutation_enrollment_helpers import (
    assert_idle,
    plain_file,
    wait_pending,
)


class TestNewOccupancyDefaultOff:
    def test_try_fast_defaults_ineligible_for_plain_files(self, tmp_path):
        path = plain_file(tmp_path)
        denied = try_fast(path)
        assert denied.lease is None
        assert denied.reason == REASON_NOT_ELIGIBLE
        snap = GATE.snapshot(path)
        assert snap.registered is False
        assert snap.new_active == 0


class TestEnrollmentOrder:
    def test_write_enrolls_before_open(self, tmp_path, monkeypatch):
        path = tmp_path / "target.bin"
        order: list[str] = []
        real_acquire = family.acquire_legacy
        real_open = open

        def spy_acquire(target, operation):
            order.append(f"enroll:{operation}")
            lease = real_acquire(target, operation)
            assert GATE.snapshot(path).legacy_active == 1
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_ENROLLED
            return lease

        def spy_open(target, mode="r", *args, **kwargs):
            order.append(f"open:{mode}")
            assert GATE.snapshot(path).legacy_active == 1
            return real_open(target, mode, *args, **kwargs)

        monkeypatch.setattr(family, "acquire_legacy", spy_acquire)
        monkeypatch.setattr(mut, "open", spy_open, raising=False)
        write_bytes_enrolled(path, b"hello")
        assert order[0] == "enroll:files_write"
        assert "open:wb" in order
        assert order.index("enroll:files_write") < order.index("open:wb")
        assert path.read_bytes() == b"hello"
        assert_idle(path)

    def test_replace_enrolls_before_syscall(self, tmp_path, monkeypatch):
        src = plain_file(tmp_path, "staging.bin", b"new")
        dst = plain_file(tmp_path, "target.bin", b"old")
        order: list[str] = []
        real_acquire = family.acquire_legacy
        real_replace = os.replace

        def spy_acquire(target, operation):
            order.append(f"enroll:{Path(target).name}:{operation}")
            lease = real_acquire(target, operation)
            assert GATE.snapshot(target).legacy_active >= 1
            return lease

        def spy_replace(source, dest):
            order.append("replace")
            assert GATE.snapshot(src).legacy_active == 1
            assert GATE.snapshot(dst).legacy_active == 1
            return real_replace(source, dest)

        monkeypatch.setattr(family, "acquire_legacy", spy_acquire)
        monkeypatch.setattr(mut.os, "replace", spy_replace)
        replace_enrolled(src, dst)
        assert order[0].startswith("enroll:")
        assert "enroll:staging.bin:files_replace" in order
        assert "enroll:target.bin:files_replace" in order
        assert "replace" in order
        staging_i = order.index("enroll:staging.bin:files_replace")
        target_i = order.index("enroll:target.bin:files_replace")
        replace_i = order.index("replace")
        assert staging_i < target_i < replace_i
        assert dst.read_bytes() == b"new"
        assert not src.exists()
        assert_idle(src)
        assert_idle(dst)

    def test_unlink_enrolls_before_syscall(self, tmp_path, monkeypatch):
        path = plain_file(tmp_path, "gone.bin")
        order: list[str] = []
        real_acquire = family.acquire_legacy
        real_unlink = os.unlink

        def spy_acquire(target, operation):
            order.append(f"enroll:{operation}")
            lease = real_acquire(target, operation)
            assert GATE.snapshot(path).legacy_active == 1
            return lease

        def spy_unlink(target):
            order.append("unlink")
            assert GATE.snapshot(path).legacy_active == 1
            return real_unlink(target)

        monkeypatch.setattr(family, "acquire_legacy", spy_acquire)
        monkeypatch.setattr(mut.os, "unlink", spy_unlink)
        unlink_enrolled(path)
        assert order[0] == "enroll:files_delete"
        assert "unlink" in order
        assert order.index("enroll:files_delete") < order.index("unlink")
        assert not path.exists()
        assert_idle(path)


class TestNewOccupancyBlocksWorker:
    def test_new_occupant_blocks_write_before_open(self, tmp_path, monkeypatch):
        path = plain_file(tmp_path, "wait.bin", b"old")
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        opens: list[str] = []
        errors: list[BaseException] = []
        done = threading.Event()
        real_open = open

        def spy_open(target, mode="r", *args, **kwargs):
            opens.append(mode)
            assert GATE.snapshot(path).legacy_active == 1
            assert GATE.snapshot(path).new_active == 0
            return real_open(target, mode, *args, **kwargs)

        monkeypatch.setattr(mut, "open", spy_open, raising=False)

        def run_write():
            try:
                write_bytes_enrolled(path, b"new")
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_write)
        worker.start()
        try:
            wait_pending(path, 1)
            assert opens == []
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_PENDING
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert path.read_bytes() == b"new"
            assert opens == ["wb"]
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert_idle(path)

    def test_new_fault_declines_before_open(self, tmp_path, monkeypatch):
        path = plain_file(tmp_path, "fault.bin", b"old")
        admitted = try_fast(path, eligible=True)
        assert admitted.lease is not None
        admitted.lease.finish(source_released=False)
        opens: list[object] = []

        def boom_open(*args, **kwargs):
            opens.append(args)
            raise AssertionError("must not open after NEW fault")

        monkeypatch.setattr(mut, "open", boom_open, raising=False)
        with pytest.raises(ResourceReleaseError):
            write_bytes_enrolled(path, b"new")
        assert path.read_bytes() == b"old"
        assert opens == []
        with pytest.raises(ResourceReleaseError):
            try_fast(path, eligible=True)

    def test_new_occupant_blocks_replace_before_syscall(self, tmp_path, monkeypatch):
        src = plain_file(tmp_path, "src.bin", b"new")
        dst = plain_file(tmp_path, "dst.bin", b"old")
        occupying = try_fast(dst, eligible=True)
        assert occupying.lease is not None
        replaces: list[object] = []
        errors: list[BaseException] = []
        done = threading.Event()
        real_replace = os.replace

        def spy_replace(source, dest):
            replaces.append((source, dest))
            assert GATE.snapshot(dst).legacy_active == 1
            assert GATE.snapshot(dst).new_active == 0
            return real_replace(source, dest)

        monkeypatch.setattr(mut.os, "replace", spy_replace)

        def run_replace():
            try:
                replace_enrolled(src, dst)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_replace)
        worker.start()
        try:
            wait_pending(dst, 1)
            assert replaces == []
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert dst.read_bytes() == b"new"
            assert replaces
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert_idle(src)
        assert_idle(dst)

    def test_new_fault_declines_replace_before_syscall(self, tmp_path, monkeypatch):
        src = plain_file(tmp_path, "src.bin", b"new")
        dst = plain_file(tmp_path, "dst.bin", b"old")
        admitted = try_fast(dst, eligible=True)
        assert admitted.lease is not None
        admitted.lease.finish(source_released=False)
        replaces: list[object] = []

        def boom_replace(*args, **kwargs):
            replaces.append(args)
            raise AssertionError("must not replace after NEW fault")

        monkeypatch.setattr(mut.os, "replace", boom_replace)
        with pytest.raises(ResourceReleaseError):
            replace_enrolled(src, dst)
        assert dst.read_bytes() == b"old"
        assert src.exists()
        assert replaces == []


class TestIdentityAndSiblingProgress:
    def test_unrelated_file_progresses_while_target_waits(self, tmp_path, monkeypatch):
        blocked = plain_file(tmp_path, "blocked.bin", b"old")
        other = plain_file(tmp_path, "other.bin", b"old")
        occupying = try_fast(blocked, eligible=True)
        assert occupying.lease is not None
        waiting = threading.Event()
        other_done = threading.Event()
        errors: list[BaseException] = []
        real_acquire = family.acquire_legacy

        def spy_acquire(target, operation):
            if Path(target).resolve() == blocked.resolve():
                waiting.set()
            return real_acquire(target, operation)

        monkeypatch.setattr(family, "acquire_legacy", spy_acquire)

        def write_blocked():
            try:
                write_bytes_enrolled(blocked, b"new-blocked")
            except BaseException as exc:
                errors.append(exc)

        def write_other():
            waiting.wait(2)
            wait_pending(blocked, 1)
            try:
                write_bytes_enrolled(other, b"new-other")
                other_done.set()
            except BaseException as exc:
                errors.append(exc)
                other_done.set()

        waiter = threading.Thread(target=write_blocked)
        sibling = threading.Thread(target=write_other)
        waiter.start()
        sibling.start()
        try:
            assert other_done.wait(5)
            assert other.read_bytes() == b"new-other"
            occupying.lease.finish(source_released=True)
            waiter.join(5)
            sibling.join(5)
        finally:
            occupying.lease.finish(source_released=True)
        assert not waiter.is_alive() and not sibling.is_alive()
        assert not errors
        assert blocked.read_bytes() == b"new-blocked"
        assert_idle(blocked)
        assert_idle(other)


class TestSidecarFamilyOccupancy:
    def test_write_sidecar_blocked_by_new_main_then_releases(
        self, tmp_path, monkeypatch
    ):
        main = plain_file(tmp_path, "sess.kohakutr", b"main")
        sidecar = Path(str(main) + "-wal")
        sidecar.write_bytes(b"old-wal")
        occupying = try_fast(main, eligible=True)
        assert occupying.lease is not None
        opens: list[str] = []
        errors: list[BaseException] = []
        done = threading.Event()
        real_open = open

        def spy_open(target, mode="r", *args, **kwargs):
            opens.append(mode)
            assert GATE.snapshot(main).legacy_active == 1
            assert GATE.snapshot(main).new_active == 0
            return real_open(target, mode, *args, **kwargs)

        monkeypatch.setattr(mut, "open", spy_open, raising=False)

        def run_write():
            try:
                write_bytes_enrolled(sidecar, b"new-wal")
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_write)
        worker.start()
        try:
            wait_pending(main, 1)
            assert opens == []
            assert sidecar.read_bytes() == b"old-wal"
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert sidecar.read_bytes() == b"new-wal"
            assert main.read_bytes() == b"main"
            assert opens == ["wb"]
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert_idle(main)
        assert_idle(sidecar)

    def test_replace_sidecar_blocked_by_new_main_then_releases(
        self, tmp_path, monkeypatch
    ):
        main = plain_file(tmp_path, "sess.kohakutr", b"main")
        src = plain_file(tmp_path, "staging.bin", b"new-wal")
        sidecar = Path(str(main) + "-wal")
        sidecar.write_bytes(b"old-wal")
        occupying = try_fast(main, eligible=True)
        assert occupying.lease is not None
        replaces: list[object] = []
        errors: list[BaseException] = []
        done = threading.Event()
        real_replace = os.replace

        def spy_replace(source, dest):
            replaces.append((source, dest))
            assert GATE.snapshot(main).legacy_active == 1
            assert GATE.snapshot(main).new_active == 0
            return real_replace(source, dest)

        monkeypatch.setattr(mut.os, "replace", spy_replace)

        def run_replace():
            try:
                replace_enrolled(src, sidecar)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_replace)
        worker.start()
        try:
            wait_pending(main, 1)
            assert replaces == []
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert sidecar.read_bytes() == b"new-wal"
            assert main.read_bytes() == b"main"
            assert replaces
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert_idle(src)
        assert_idle(sidecar)
        assert_idle(main)

    def test_unlink_sidecar_blocked_by_new_main_then_releases(
        self, tmp_path, monkeypatch
    ):
        main = plain_file(tmp_path, "sess.kohakutr", b"main")
        sidecar = Path(str(main) + "-wal")
        sidecar.write_bytes(b"wal")
        occupying = try_fast(main, eligible=True)
        assert occupying.lease is not None
        unlinks: list[object] = []
        errors: list[BaseException] = []
        done = threading.Event()
        real_unlink = os.unlink

        def spy_unlink(target):
            unlinks.append(target)
            assert GATE.snapshot(main).legacy_active == 1
            assert GATE.snapshot(main).new_active == 0
            return real_unlink(target)

        monkeypatch.setattr(mut.os, "unlink", spy_unlink)

        def run_unlink():
            try:
                unlink_enrolled(sidecar)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_unlink)
        worker.start()
        try:
            wait_pending(main, 1)
            assert unlinks == []
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert not sidecar.exists()
            assert main.read_bytes() == b"main"
            assert unlinks
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert_idle(sidecar)
        assert_idle(main)

    def test_unknown_suffix_does_not_map_owner(self, tmp_path, monkeypatch):
        main = plain_file(tmp_path, "sess.kohakutr", b"main")
        unknown = Path(str(main) + ".backup")
        unknown.write_bytes(b"old")
        occupying = try_fast(main, eligible=True)
        assert occupying.lease is not None
        enrolled: list[str] = []
        real_acquire = family.acquire_legacy

        def spy_acquire(target, operation):
            enrolled.append(Path(target).name)
            return real_acquire(target, operation)

        monkeypatch.setattr(family, "acquire_legacy", spy_acquire)
        write_bytes_enrolled(unknown, b"new")
        assert unknown.read_bytes() == b"new"
        assert main.read_bytes() == b"main"
        assert occupying.lease is not None
        assert GATE.snapshot(main).new_active == 1
        assert "sess.kohakutr" not in enrolled
        assert unknown.name in enrolled
        occupying.lease.finish(source_released=True)
        assert_idle(unknown)
        assert_idle(main)

    def test_family_admission_does_not_expand_mutation_targets(
        self, tmp_path, monkeypatch
    ):
        main = plain_file(tmp_path, "sess.kohakutr", b"main")
        sidecar = Path(str(main) + "-wal")
        sidecar.write_bytes(b"wal")
        unknown = Path(str(main) + ".backup")
        unknown.write_bytes(b"keep")
        staging = plain_file(tmp_path, "staging.bin", b"next")
        enrolled: list[str] = []
        seen_inputs: list[list[object]] = []
        real_acquire = family.acquire_legacy
        real_identities = family.family_identities

        def spy_identities(paths):
            captured = list(paths)
            seen_inputs.append(captured)
            result = real_identities(paths)
            assert list(paths) == captured
            return result

        def spy_acquire(target, operation):
            enrolled.append(Path(target).name)
            return real_acquire(target, operation)

        monkeypatch.setattr(family, "family_identities", spy_identities)
        monkeypatch.setattr(family, "acquire_legacy", spy_acquire)

        write_bytes_enrolled(sidecar, b"written")
        assert sidecar.read_bytes() == b"written"
        assert main.read_bytes() == b"main"
        assert unknown.read_bytes() == b"keep"
        assert "sess.kohakutr" in enrolled
        assert sidecar.name in enrolled
        assert unknown.name not in enrolled

        replace_enrolled(staging, sidecar)
        assert sidecar.read_bytes() == b"next"
        assert main.read_bytes() == b"main"
        assert unknown.read_bytes() == b"keep"
        assert not staging.exists()

        unlink_enrolled(sidecar)
        assert not sidecar.exists()
        assert main.read_bytes() == b"main"
        assert unknown.read_bytes() == b"keep"
        assert seen_inputs
        for captured in seen_inputs:
            assert unknown not in captured
        assert_idle(main)
        assert_idle(sidecar)
        assert_idle(unknown)
