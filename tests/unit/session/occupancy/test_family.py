"""Conventional session family identities participate in mutation admission."""

import os

import pytest

import kohakuterrarium.session.occupancy.family as family_module
from kohakuterrarium.session.occupancy.family import (
    FamilyLease,
    acquire_family,
    family_identities,
)
from kohakuterrarium.session.occupancy.gate import GATE, ResourceReleaseError, try_fast


@pytest.mark.parametrize(
    "suffix",
    [
        "-wal",
        "-shm",
        "-journal",
        ".drives",
        ".drives-wal",
        ".drives-shm",
        ".drives-journal",
        ".drives.split-intent.json",
        ".drives.split-intent.json.tmp",
    ],
)
@pytest.mark.parametrize("version", ["", ".v2"])
def test_sidecar_enrolls_exact_and_owner(tmp_path, suffix, version):
    owner = tmp_path / ("session.kohakutr" + version)
    sidecar = owner.with_name(owner.name + suffix)
    lease = acquire_family([sidecar], "test")
    try:
        assert try_fast(owner, eligible=True).reason == "legacy_enrolled"
        assert try_fast(sidecar, eligible=True).reason == "legacy_enrolled"
        assert GATE.snapshot(owner).legacy_active == 1
    finally:
        lease.finish(source_released=True)
    assert not GATE.snapshot(owner).registered
    assert not GATE.snapshot(sidecar).registered


@pytest.mark.parametrize(
    "name",
    [
        "notes.txt-wal",
        "x.kohakutr.backup",
        "x.kohakutr-wal.extra",
        "x.kohakutr.vx-wal",
        "x.kohakutr.drives-extra",
    ],
)
def test_unrecognized_suffix_does_not_infer_owner(tmp_path, name):
    path = tmp_path / name
    assert family_identities([path]) == (os.path.normcase(str(path.resolve())),)


def test_duplicates_and_versions_remain_distinct(tmp_path):
    main = tmp_path / "x.kohakutr"
    version = tmp_path / "x.kohakutr.v2"
    lease = acquire_family([main, main, version, str(version) + "-wal"], "test")
    try:
        assert GATE.snapshot(main).legacy_active == 1
        assert GATE.snapshot(version).legacy_active == 1
    finally:
        lease.finish(source_released=True)
        lease.finish(source_released=True)
    assert not GATE.snapshot(main).registered
    assert not GATE.snapshot(version).registered


def test_failed_later_admission_releases_earlier(tmp_path):
    first, last = tmp_path / "a.kohakutr", tmp_path / "z.kohakutr"
    fast = try_fast(last, eligible=True).lease
    fast.finish(source_released=False)
    with pytest.raises(ResourceReleaseError):
        acquire_family([last, first], "test")
    assert not GATE.snapshot(first).registered


def test_ledger_allocation_failure_releases_new_and_previous(tmp_path, monkeypatch):
    paths = [tmp_path / "a.kohakutr", tmp_path / "z.kohakutr"]

    class FailSecond(list):
        def append(self, lease):
            if self:
                raise MemoryError("tracking failed")
            super().append(lease)

    class FailingFamily(FamilyLease):
        def __init__(self):
            super().__init__()
            self._leases = FailSecond()

    monkeypatch.setattr(family_module, "FamilyLease", FailingFamily)
    with pytest.raises(MemoryError, match="tracking failed"):
        acquire_family(paths, "test")
    for path in paths:
        assert not GATE.snapshot(path).registered


def test_uncertain_close_retains_every_alias(tmp_path):
    main = tmp_path / "x.kohakutr"
    sidecar = str(main) + "-wal"
    lease = acquire_family([sidecar], "test")
    lease.finish(source_released=False)
    lease.finish(source_released=True)
    for path in (main, sidecar):
        assert try_fast(path, eligible=True).reason == "legacy_release_uncertain"


def test_sidecar_symlink_enrolls_lexical_and_resolved_owners(tmp_path):
    real = tmp_path / "real.kohakutr-wal"
    real.touch()
    alias = tmp_path / "alias.kohakutr-wal"
    try:
        alias.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unavailable")
    lease = acquire_family([alias], "test")
    try:
        for path in (tmp_path / "real.kohakutr", tmp_path / "alias.kohakutr", real):
            assert try_fast(path, eligible=True).reason == "legacy_enrolled"
    finally:
        lease.finish(source_released=True)
