"""Occupancy around recipe session-family quarantine and unlink.

Uses real files. Directory trees, editor swap files, and unknown sidecars
are out of scope and must remain untouched. Production does not call
``try_fast``.
"""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_LEGACY_ENROLLED,
    REASON_LEGACY_PENDING,
    ResourceReleaseError,
    try_fast,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.recipe import discard as discard_mod
from kohakuterrarium.terrarium.recipe.discard import (
    _discard_session_family,
    discard_recipe_graph_shielded,
)
from tests.unit.laboratory.file_mutation_enrollment_helpers import (
    assert_idle,
    wait_pending,
)


class _Graph:
    def __init__(self, creature_ids):
        self.creature_ids = creature_ids


class _Topology:
    def __init__(self, graph_id: str):
        self.graphs = {graph_id: _Graph([])}
        self.creature_to_graph = {}


class _OwnedEngine:
    """Weakref-capable engine double for shielded discard occupancy tests."""

    def __init__(self, graph_id: str, store: SessionStore):
        self._topology = _Topology(graph_id)
        self._session_stores = {graph_id: store}
        self._owned_sessions = {graph_id}
        self._creatures = {}
        self._environments = {}
        self._drive_runtime = None
        self._recipe_graph_locks = {}

    async def _drain_drive_topology(self):
        return None


def _session_family(tmp_path, name: str = "recipe.kohakutr") -> Path:
    path = tmp_path / name
    path.write_bytes(b"session")
    path.with_name(path.name + "-wal").write_bytes(b"wal")
    path.with_name(path.name + "-shm").write_bytes(b"shm")
    path.with_name(path.name + ".drives").write_bytes(b"drives")
    path.with_name(path.name + ".drives-wal").write_bytes(b"dwal")
    path.with_name(path.name + ".drives-shm").write_bytes(b"dshm")
    (tmp_path / f"{path.name}.drives.split-intent.json").write_bytes(b"intent")
    (tmp_path / f"{path.name}.drives.split-intent.json.bak").write_bytes(b"bak")
    return path


def _expected_family(path: Path) -> list[Path]:
    return [
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + ".drives"),
        path.with_name(path.name + ".drives-wal"),
        path.with_name(path.name + ".drives-shm"),
        *path.parent.glob(f"{path.name}.drives.split-intent.json*"),
    ]


def _closed_store(path: Path) -> SessionStore:
    store = SessionStore(path)
    store.close(update_status=False)
    return store


class TestDiscardFamilyEnrollment:
    def test_enrolls_explicit_family_before_quarantine(self, tmp_path, monkeypatch):
        path = _session_family(tmp_path)
        unknown = path.with_name(path.name + ".editor-swp")
        unknown.write_bytes(b"keep")
        order: list[str] = []
        real_acquire = discard_mod.acquire_family
        real_replace = Path.replace
        family = _expected_family(path)

        def spy_acquire(paths, operation):
            order.append(f"enroll:{operation}")
            enrolled = [Path(item).resolve() for item in paths]
            assert {item.resolve() for item in family} <= set(enrolled)
            lease = real_acquire(paths, operation)
            assert GATE.snapshot(path).legacy_active >= 1
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_ENROLLED
            return lease

        def spy_replace(self, target):
            if Path(self) in family or Path(self).name.startswith(path.name):
                order.append(f"replace:{self.name}")
                assert GATE.snapshot(path).legacy_active >= 1
            return real_replace(self, target)

        monkeypatch.setattr(discard_mod, "acquire_family", spy_acquire)
        monkeypatch.setattr(Path, "replace", spy_replace)
        _discard_session_family(path)
        assert order[0] == "enroll:recipe_discard"
        assert any(item.startswith("replace:") for item in order)
        assert order.index("enroll:recipe_discard") < next(
            i for i, item in enumerate(order) if item.startswith("replace:")
        )
        for member in family:
            assert not member.exists()
        assert unknown.read_bytes() == b"keep"
        assert_idle(path)

    def test_missing_members_still_succeed(self, tmp_path):
        path = tmp_path / "sparse.kohakutr"
        path.write_bytes(b"session")
        _discard_session_family(path)
        assert not path.exists()
        assert_idle(path)

    def test_new_occupant_blocks_before_quarantine(self, tmp_path, monkeypatch):
        path = _session_family(tmp_path)
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        replaces: list[str] = []
        errors: list[BaseException] = []
        done = threading.Event()
        real_replace = Path.replace

        def spy_replace(self, target):
            if Path(self) == path:
                replaces.append(self.name)
                assert GATE.snapshot(path).legacy_active >= 1
                assert GATE.snapshot(path).new_active == 0
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", spy_replace)

        def run_discard():
            try:
                _discard_session_family(path)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_discard)
        worker.start()
        try:
            wait_pending(path, 1)
            assert replaces == []
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_PENDING
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert not path.exists()
            assert replaces
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert_idle(path)

    def test_new_fault_declines_before_quarantine(self, tmp_path, monkeypatch):
        path = _session_family(tmp_path)
        admitted = try_fast(path, eligible=True)
        assert admitted.lease is not None
        admitted.lease.finish(source_released=False)
        replaces: list[object] = []

        def boom_replace(*args, **kwargs):
            replaces.append(args)
            raise AssertionError("must not quarantine after NEW fault")

        monkeypatch.setattr(Path, "replace", boom_replace)
        with pytest.raises(ResourceReleaseError):
            _discard_session_family(path)
        assert path.exists()
        assert replaces == []

    def test_failed_quarantine_rolls_back_and_releases(self, tmp_path, monkeypatch):
        path = _session_family(tmp_path)
        wal = path.with_name(path.name + "-wal")
        occupancy: list[int] = []
        real_replace = Path.replace

        def spy_replace(self, target):
            occupancy.append(GATE.snapshot(path).legacy_active)
            if self == wal:
                raise PermissionError(13, "cannot rename wal")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", spy_replace)
        with pytest.raises(PermissionError):
            _discard_session_family(path)
        assert path.exists()
        assert wal.exists()
        assert occupancy and all(count >= 1 for count in occupancy)
        assert_idle(path)

    def test_unlink_failure_keeps_quarantine_and_releases(self, tmp_path, monkeypatch):
        path = tmp_path / "locked.kohakutr"
        path.write_bytes(b"session")
        real_unlink = Path.unlink

        def fake_unlink(self, *args, **kwargs):
            target = Path(self)
            if ".discard-" in target.name:
                raise PermissionError(13, "simulated locked quarantine")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fake_unlink)
        _discard_session_family(path)
        assert not path.exists()
        leftovers = list(tmp_path.glob(f"{path.name}.discard-*"))
        assert leftovers
        assert_idle(path)

    def test_unrelated_file_progresses_while_target_waits(self, tmp_path, monkeypatch):
        blocked = _session_family(tmp_path, "blocked.kohakutr")
        other = tmp_path / "other.kohakutr"
        other.write_bytes(b"other")
        occupying = try_fast(blocked, eligible=True)
        assert occupying.lease is not None
        waiting = threading.Event()
        other_done = threading.Event()
        errors: list[BaseException] = []
        real_acquire = discard_mod.acquire_family

        def spy_acquire(paths, operation):
            materialized = [Path(item) for item in paths]
            if any(item.resolve() == blocked.resolve() for item in materialized):
                waiting.set()
            return real_acquire(paths, operation)

        monkeypatch.setattr(discard_mod, "acquire_family", spy_acquire)

        def discard_blocked():
            try:
                _discard_session_family(blocked)
            except BaseException as exc:
                errors.append(exc)

        def discard_other():
            waiting.wait(2)
            wait_pending(blocked, 1)
            try:
                _discard_session_family(other)
                other_done.set()
            except BaseException as exc:
                errors.append(exc)
                other_done.set()

        waiter = threading.Thread(target=discard_blocked)
        sibling = threading.Thread(target=discard_other)
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


class TestDiscardShieldedWorker:
    def test_shielded_discard_enrolls_off_loop(self, tmp_path, monkeypatch):
        path = tmp_path / "owned.kohakutr"
        store = _closed_store(path)
        threads: list[str] = []
        real_acquire = discard_mod.acquire_family
        loop_name = {"value": None}

        def spy_acquire(paths, operation):
            threads.append(threading.current_thread().name)
            return real_acquire(paths, operation)

        monkeypatch.setattr(discard_mod, "acquire_family", spy_acquire)
        graph_id = "g1"
        engine = _OwnedEngine(graph_id, store)

        async def run():
            loop_name["value"] = threading.current_thread().name
            await discard_recipe_graph_shielded(engine, graph_id)

        asyncio.run(run())
        assert threads
        assert loop_name["value"] is not None
        assert all(name != loop_name["value"] for name in threads)
        assert not path.exists()
        assert_idle(path)

    def test_attach_during_admission_wait_rejects_without_deleting(self, tmp_path):
        path = tmp_path / "attach-wait.kohakutr"
        store = _closed_store(path)
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        graph_id = "g-attach"
        engine = _OwnedEngine(graph_id, store)
        attached = SimpleNamespace(path=path)

        async def run():
            outer = asyncio.create_task(discard_recipe_graph_shielded(engine, graph_id))
            await asyncio.to_thread(wait_pending, path, 1)
            engine._session_stores["new-owner"] = attached
            occupying.lease.finish(source_released=True)
            with pytest.raises(ValueError, match="active session store"):
                await outer
            assert path.exists()
            assert_idle(path)

        try:
            asyncio.run(run())
        finally:
            occupying.lease.finish(source_released=True)

    def test_repeat_cancel_still_finishes_discard_after_admission(self, tmp_path):
        path = tmp_path / "cancel.kohakutr"
        store = _closed_store(path)
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        graph_id = "g-cancel"
        engine = _OwnedEngine(graph_id, store)

        async def run():
            outer = asyncio.create_task(discard_recipe_graph_shielded(engine, graph_id))
            await asyncio.to_thread(wait_pending, path, 1)
            assert path.exists()
            assert GATE.snapshot(path).legacy_active == 0
            outer.cancel()
            await asyncio.sleep(0)
            outer.cancel()
            await asyncio.sleep(0.05)
            assert not outer.done()
            assert path.exists()
            occupying.lease.finish(source_released=True)
            with pytest.raises(asyncio.CancelledError):
                await outer
            assert not path.exists()
            assert_idle(path)

        try:
            asyncio.run(run())
        finally:
            occupying.lease.finish(source_released=True)

    def test_cleanup_failure_preserves_original_cancellation(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "fail-cancel.kohakutr"
        store = _closed_store(path)
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None

        def boom_discard(family):
            raise OSError("cleanup boom")

        monkeypatch.setattr(discard_mod, "_discard_members", boom_discard)
        graph_id = "g-fail"
        engine = _OwnedEngine(graph_id, store)

        async def run():
            outer = asyncio.create_task(discard_recipe_graph_shielded(engine, graph_id))
            await asyncio.to_thread(wait_pending, path, 1)
            outer.cancel()
            await asyncio.sleep(0)
            occupying.lease.finish(source_released=True)
            with pytest.raises(asyncio.CancelledError) as caught:
                await outer
            assert isinstance(caught.value.__cause__, OSError)
            assert "cleanup boom" in str(caught.value.__cause__)
            assert path.exists()
            assert_idle(path)

        try:
            asyncio.run(run())
        finally:
            occupying.lease.finish(source_released=True)
