"""Unit tests for :mod:`studio.identity.drive_settings`.

The canonical ``drive-settings.yaml`` owner: path/KT_CONFIG_DIR, atomic save,
optimistic-concurrency conflict, malformed-preserves-last-valid, the
enabled-only-import resolver, missing-registration errors, and the distinct
apply operation. Real files + real registration catalog; no mocks.
"""

import errno
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

from kohakuterrarium.studio.identity import drive_settings as ds
from kohakuterrarium.studio.identity.drive_settings import (
    DriveSettings,
    RegistrationSetting,
)
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.drive.registration import DriveRegistrationDescriptor
from kohakuterrarium.terrarium.drive.errors import (
    DriveRegistrationNotFoundError,
    DriveSettingsConflictError,
    DriveValidationError,
)
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.config_dir import config_dir

# Subprocess body for the cross-process lock test: a SEPARATE process acquires
# the settings write lock, signals it holds it, waits for a go-file, then commits
# a distinct revision while still holding the lock and releases. Kept as a `-c`
# string so it runs in a genuinely separate interpreter (the in-process
# threading.Lock cannot exclude it — only the OS file lock can).
_LOCK_HOLDER_SRC = """
import os, time
from pathlib import Path
from kohakuterrarium.studio.identity import drive_settings as ds
from kohakuterrarium.studio.identity import drive_settings_io as ds_io
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.utils.file_lock import FileLock

held = Path(os.environ["KT_LOCKHOLD_HELD"])
go = Path(os.environ["KT_LOCKHOLD_GO"])
path = ds.drive_settings_path()
lock = FileLock(str(ds_io._save_lock_path(path)))
lock.acquire()
held.write_text("1", encoding="utf-8")
deadline = time.monotonic() + 60
while not go.exists() and time.monotonic() < deadline:
    time.sleep(0.02)
different = ds.DriveSettings(
    runtime=DriveRuntimeConfig(enabled=False), registrations={}
)
ds._atomic_write(path, ds._serialize(different))
lock.release()
"""


def _wait_until(predicate, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")


def _enabled_settings(**regs) -> DriveSettings:
    registrations = regs or {"generic": RegistrationSetting(enabled=True)}
    return DriveSettings(
        runtime=DriveRuntimeConfig(enabled=True), registrations=registrations
    )


class _CfgReg:
    """A configurable package registration for option-resolution tests (R1-17)."""

    name = "cfg"
    kind = "cfg"
    schema_version = 1

    def __init__(self):
        self.mode = "default"

    def descriptor(self):
        return DriveRegistrationDescriptor(
            name="cfg",
            kind="cfg",
            schema_version=1,
            option_defaults={"mode": "default"},
            option_schema={"mode": {"type": "str", "choices": ["default", "fast"]}},
        )

    def configure(self, options):
        self.mode = options["mode"]


def _patch_cfg_package(monkeypatch):
    import kohakuterrarium.studio.catalog.drive_registrations as cat

    monkeypatch.setattr(
        cat,
        "list_packages",
        lambda: [
            {
                "name": "kt-biome",
                "drive_registrations": [
                    {
                        "name": "cfg",
                        "kind": "cfg",
                        "module": __name__,
                        "class": "_CfgReg",
                        "option_defaults": {"mode": "default"},
                        "option_schema": {
                            "mode": {"type": "str", "choices": ["default", "fast"]}
                        },
                    }
                ],
            }
        ],
    )


class TestPathAndDefaults:
    def test_path_honours_config_dir(self):
        assert ds.drive_settings_path() == config_dir() / "drive-settings.yaml"

    def test_absent_file_is_disabled_default(self):
        settings = ds.load_settings()
        assert settings.runtime.enabled is False
        assert settings.registrations == {}
        assert settings.revision is None

    def test_resolve_disabled_yields_disabled_spec(self):
        spec = ds.resolve_runtime()
        assert spec.config.enabled is False
        assert spec.registrations == ()

    def test_resolve_kwargs_disabled_by_default(self):
        kwargs = ds.resolve_drive_kwargs()
        assert kwargs == {
            "drive_config": ds.load_settings().runtime,
            "drive_registrations": (),
            "drive_store": None,
        }


class TestSaveLoad:
    def test_save_then_load_roundtrips_and_stamps_revision(self):
        saved = ds.save_settings(_enabled_settings())
        assert saved.revision is not None
        loaded = ds.load_settings()
        assert loaded.runtime.enabled is True
        assert loaded.registrations["generic"].enabled is True
        # Same on-disk bytes -> same revision.
        assert loaded.revision == saved.revision

    def test_save_is_atomic_no_tmp_left_behind(self):
        ds.save_settings(_enabled_settings())
        leftovers = list(config_dir().glob("drive-settings.yaml.kt-tmp*"))
        assert leftovers == []

    def test_save_accepts_raw_mapping(self):
        saved = ds.save_settings(
            {
                "schema_version": 1,
                "runtime": {"enabled": True},
                "registrations": {"generic": {"enabled": True, "options": {}}},
            }
        )
        assert ds.load_settings().registrations["generic"].enabled is True
        assert saved.revision == ds.current_revision()


class TestOptimisticConcurrency:
    def test_stale_expected_revision_conflicts_and_preserves_file(self):
        first = ds.save_settings(_enabled_settings())
        # A concurrent writer bumps the file.
        ds.save_settings(
            DriveSettings(runtime=DriveRuntimeConfig(enabled=False)),
            expected_revision=first.revision,
        )
        on_disk = ds.drive_settings_path().read_bytes()
        with pytest.raises(DriveSettingsConflictError) as exc:
            ds.save_settings(_enabled_settings(), expected_revision=first.revision)
        assert exc.value.expected_revision == first.revision
        # The losing write left the file untouched.
        assert ds.drive_settings_path().read_bytes() == on_disk

    def test_none_expected_revision_allows_first_write(self):
        # No file yet; expected_revision=None must not conflict.
        saved = ds.save_settings(_enabled_settings(), expected_revision=None)
        assert saved.revision is not None

    def test_concurrent_writers_exactly_one_conflicts(self):
        # R1-28: two writers racing the same base revision must serialize so
        # exactly one wins and the stale one conflicts (no lost update). Each
        # candidate carries DISTINCT registration options so both differ from the
        # baseline AND from each other — otherwise a candidate identical to the
        # baseline leaves the on-disk revision unchanged and the second writer's
        # optimistic compare spuriously passes (the earlier vacuous test).
        first = ds.save_settings(_enabled_settings())
        barrier = threading.Barrier(2)
        results: list[str] = []
        lock = threading.Lock()

        def writer(tag: str) -> None:
            candidate = DriveSettings(
                runtime=DriveRuntimeConfig(enabled=True),
                registrations={
                    "generic": RegistrationSetting(enabled=True, options={"tag": tag})
                },
            )
            barrier.wait()
            try:
                ds.save_settings(candidate, expected_revision=first.revision)
                outcome = "ok"
            except DriveSettingsConflictError:
                outcome = "conflict"
            with lock:
                results.append(outcome)

        threads = [
            threading.Thread(target=writer, args=("a",)),
            threading.Thread(target=writer, args=("b",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == ["conflict", "ok"]
        # No unique temp files left behind by either writer.
        assert list(config_dir().glob("*.kt-tmp")) == []

    def test_cross_process_writer_blocks_and_loser_conflicts(self):
        # R1-28: the OS file lock must exclude a SEPARATE process from the
        # read-compare-write section (the in-process ``threading.Lock`` cannot).
        # A subprocess grabs the settings lock and holds it; while it is held the
        # in-process save must BLOCK (proving cross-process exclusion), then after
        # the subprocess commits a distinct revision and releases, the blocked
        # save observes the new revision and conflicts.
        first = ds.save_settings(_enabled_settings())
        held = config_dir() / "held.signal"
        go = config_dir() / "go.signal"
        env = dict(os.environ)
        env["KT_CONFIG_DIR"] = os.environ["KT_CONFIG_DIR"]
        env["KT_LOCKHOLD_HELD"] = str(held)
        env["KT_LOCKHOLD_GO"] = str(go)
        proc = subprocess.Popen([sys.executable, "-c", _LOCK_HOLDER_SRC], env=env)
        try:
            _wait_until(held.exists, timeout=30.0)
            outcome: dict[str, str] = {}

            def loser() -> None:
                candidate = DriveSettings(
                    runtime=DriveRuntimeConfig(enabled=True),
                    registrations={
                        "generic": RegistrationSetting(enabled=True, options={"t": "x"})
                    },
                )
                try:
                    ds.save_settings(candidate, expected_revision=first.revision)
                    outcome["result"] = "ok"
                except DriveSettingsConflictError:
                    outcome["result"] = "conflict"

            t = threading.Thread(target=loser)
            t.start()
            # The subprocess holds the lock and will not release until `go`; the
            # in-process save is therefore blocked on the OS lock. Without the
            # cross-process lock it would complete immediately (fail-first).
            t.join(timeout=1.0)
            assert t.is_alive(), "save did not block on the cross-process lock"
            go.write_text("1", encoding="utf-8")
            t.join(timeout=30.0)
            assert not t.is_alive()
            assert outcome["result"] == "conflict"
        finally:
            go.write_text("1", encoding="utf-8")
            proc.wait(timeout=30)

    def test_save_lock_timeout_raises_conflict(self, monkeypatch):
        # R1-28: a wedged holder that never releases must not hang the writer
        # forever — the bounded blocking acquire gives up and raises a conflict.
        from kohakuterrarium.studio.identity import drive_settings_io as ds_io
        from kohakuterrarium.utils.file_lock import FileLock

        ds.save_settings(_enabled_settings())  # ensure the file + dir exist
        held = FileLock(str(ds_io._save_lock_path(ds.drive_settings_path())))
        held.acquire()  # a foreign fd holds the lock (conflicts even in-process)
        try:
            monkeypatch.setattr(ds_io, "_SAVE_LOCK_TIMEOUT_S", 0.1)
            with pytest.raises(
                DriveSettingsConflictError, match="locked by another writer"
            ):
                ds.save_settings(_enabled_settings())
        finally:
            held.release()


class TestMalformed:
    def test_malformed_yaml_raises_and_leaves_file(self):
        path = ds.drive_settings_path()
        path.write_text("runtime: {enabled: true\n  broken", encoding="utf-8")
        before = path.read_bytes()
        with pytest.raises(DriveValidationError):
            ds.load_settings()
        assert path.read_bytes() == before

    def test_bad_type_raises_typed_error(self):
        ds.drive_settings_path().write_text(
            yaml.safe_dump({"runtime": {"enabled": "yes"}}), encoding="utf-8"
        )
        with pytest.raises(DriveValidationError):
            ds.load_settings()

    def test_future_schema_version_rejected(self):
        ds.drive_settings_path().write_text(
            yaml.safe_dump({"schema_version": 999, "runtime": {"enabled": False}}),
            encoding="utf-8",
        )
        with pytest.raises(DriveValidationError):
            ds.load_settings()

    def test_status_surfaces_parse_error_without_raising(self):
        ds.drive_settings_path().write_text("{: broken", encoding="utf-8")
        status = ds.settings_status()
        assert status["parse_error"] is not None
        assert status["runtime"] is None


class TestResolve:
    def test_enabled_generic_resolves_instance(self):
        ds.save_settings(_enabled_settings())
        spec = ds.resolve_runtime()
        assert spec.config.enabled is True
        assert [r.name for r in spec.registrations] == ["generic"]

    def test_enabled_runtime_with_no_registration_rejected(self):
        # Enabling the runtime but enabling no registration must fail (design §8.3).
        ds.save_settings(
            DriveSettings(
                runtime=DriveRuntimeConfig(enabled=True),
                registrations={"generic": RegistrationSetting(enabled=False)},
            )
        )
        with pytest.raises(DriveValidationError):
            ds.resolve_runtime()

    def test_unknown_enabled_registration_raises(self):
        ds.save_settings(
            DriveSettings(
                runtime=DriveRuntimeConfig(enabled=True),
                registrations={"nope": RegistrationSetting(enabled=True)},
            )
        )
        with pytest.raises(DriveRegistrationNotFoundError):
            ds.resolve_runtime()

    def test_resolve_kwargs_strict_reraises_but_lenient_disables(self):
        ds.save_settings(
            DriveSettings(
                runtime=DriveRuntimeConfig(enabled=True),
                registrations={"nope": RegistrationSetting(enabled=True)},
            )
        )
        with pytest.raises(DriveRegistrationNotFoundError):
            ds.resolve_drive_kwargs(strict=True)
        # Lenient (construction-path default) degrades to a disabled runtime.
        kwargs = ds.resolve_drive_kwargs()
        assert kwargs["drive_config"] is None

    def test_disabled_settings_skip_catalog_scan(self, monkeypatch):
        # When the runtime is disabled the resolver must not import registrations.
        called = {"n": 0}
        import kohakuterrarium.studio.catalog.drive_registrations as cat

        def boom(name):
            called["n"] += 1
            raise AssertionError("should not instantiate when disabled")

        monkeypatch.setattr(cat, "instantiate_registration", boom)
        # drive_settings imported the name at module load; patch there too.
        monkeypatch.setattr(ds, "instantiate_registration", boom)
        ds.resolve_runtime()
        assert called["n"] == 0


class TestOptionResolution:
    """R1-17: enabled-registration options are validated + injected at resolve."""

    def test_resolve_injects_non_default_option(self, monkeypatch):
        _patch_cfg_package(monkeypatch)
        ds.save_settings(
            DriveSettings(
                runtime=DriveRuntimeConfig(enabled=True),
                registrations={
                    "cfg": RegistrationSetting(enabled=True, options={"mode": "fast"})
                },
            )
        )
        spec = ds.resolve_runtime()
        assert [r.name for r in spec.registrations] == ["cfg"]
        assert spec.registrations[0].mode == "fast"  # option actually took effect

    def test_resolve_rejects_invalid_option(self, monkeypatch):
        _patch_cfg_package(monkeypatch)
        ds.save_settings(
            DriveSettings(
                runtime=DriveRuntimeConfig(enabled=True),
                registrations={
                    "cfg": RegistrationSetting(enabled=True, options={"mode": "warp"})
                },
            )
        )
        with pytest.raises(DriveValidationError):
            ds.resolve_runtime()

    def test_status_reports_effective_options(self, monkeypatch):
        _patch_cfg_package(monkeypatch)
        ds.save_settings(
            DriveSettings(
                runtime=DriveRuntimeConfig(enabled=True),
                registrations={
                    "cfg": RegistrationSetting(enabled=True, options={"mode": "fast"})
                },
            )
        )
        status = ds.settings_status()
        cfg = next(e for e in status["registrations"] if e["name"] == "cfg")
        assert cfg["effective_options"] == {"mode": "fast"}


class TestRunningRevision:
    """R1-29: the running runtime revision must reflect effective implementation
    identity + options, not just registration names + tuning."""

    def _fake_engine(self, descriptor, registration):
        entry = SimpleNamespace(descriptor=descriptor, registration=registration)
        snapshot = SimpleNamespace(entries=(entry,))
        drives = SimpleNamespace(
            snapshot=snapshot, config=DriveRuntimeConfig(enabled=True)
        )
        return SimpleNamespace(drives=drives)

    def _reg_with_options(self, options):
        from kohakuterrarium.terrarium.drive.registration import (
            EFFECTIVE_OPTIONS_ATTR,
            GenericDriveRegistration,
        )

        reg = GenericDriveRegistration()
        setattr(reg, EFFECTIVE_OPTIONS_ATTR, dict(options))
        return reg

    def test_option_change_changes_running_revision(self):
        desc = DriveRegistrationDescriptor(
            name="generic", kind="generic", schema_version=1
        )
        rev_a = ds._engine_runtime_revision(
            self._fake_engine(desc, self._reg_with_options({"mode": "fast"}))
        )
        rev_b = ds._engine_runtime_revision(
            self._fake_engine(desc, self._reg_with_options({"mode": "slow"}))
        )
        assert rev_a is not None
        assert rev_a != rev_b

    def test_same_name_schema_upgrade_changes_running_revision(self):
        v1 = DriveRegistrationDescriptor(name="custom", kind="custom", schema_version=1)
        v2 = DriveRegistrationDescriptor(name="custom", kind="custom", schema_version=2)
        reg = self._reg_with_options({})
        rev_v1 = ds._engine_runtime_revision(self._fake_engine(v1, reg))
        rev_v2 = ds._engine_runtime_revision(self._fake_engine(v2, reg))
        assert rev_v1 != rev_v2

    def test_different_impl_class_same_descriptor_changes_running_revision(self):
        # R1-29: replacing the implementation class behind an identical descriptor
        # (same name/kind/schema/options) must still move the running revision —
        # the fingerprint hashes the resolved module + qualname, not just the
        # descriptor fields.
        desc = DriveRegistrationDescriptor(
            name="custom", kind="custom", schema_version=1
        )

        class _ImplA:
            name = kind = "custom"
            schema_version = 1

        class _ImplB:
            name = kind = "custom"
            schema_version = 1

        rev_a = ds._engine_runtime_revision(self._fake_engine(desc, _ImplA()))
        rev_b = ds._engine_runtime_revision(self._fake_engine(desc, _ImplB()))
        assert rev_a is not None
        assert rev_a != rev_b


class TestApply:
    async def test_apply_enable_on_disabled_engine_is_restart_required(self):
        ds.save_settings(_enabled_settings())
        engine = Terrarium()  # Drive-disabled
        async with engine:
            result = ds.apply_runtime(engine)
        assert result["result"] == ds.RESTART_REQUIRED
        assert result["warnings"]

    async def test_apply_noop_when_disabled_stays_disabled(self):
        engine = Terrarium()
        async with engine:
            result = ds.apply_runtime(engine)
        assert result["result"] == ds.APPLIED_LIVE

    async def test_apply_same_registry_is_live(self):
        ds.save_settings(_enabled_settings())
        kwargs = ds.resolve_drive_kwargs()
        engine = Terrarium(**kwargs)
        async with engine:
            result = ds.apply_runtime(engine)
        assert result["result"] == ds.APPLIED_LIVE
        assert result["desired_revision"] == ds.current_revision()


class TestSaveDurability:
    """Round-3c: the directory-barrier degradation ``_atomic_write`` computes must
    be caller-visible — ``save_settings`` reports it as a typed
    :class:`SaveDurability` and warns when it degrades to FILE_ONLY, never a silent
    ordinary success."""

    _DIR_FD_SENTINEL = -515151

    def _force_dir_barrier(self, monkeypatch, *, fsync_error=None):
        # Route ONLY the atomic write's directory fsync through a sentinel fd so
        # the barrier outcome is deterministic on every platform (reusing the
        # round-3c seam), WITHOUT patching ``sys.platform`` — that would flip
        # ``FileLock`` onto its POSIX branch and break the cross-process save lock
        # on Windows. The temp-file and lock I/O keep hitting real descriptors.
        from kohakuterrarium.studio.identity import drive_settings_io as ds_io

        real_fsync = os.fsync
        real_close = os.close

        def fake_fsync(fd):
            if fd == self._DIR_FD_SENTINEL:
                if fsync_error is not None:
                    raise fsync_error
                return
            real_fsync(fd)

        def fake_close(fd):
            if fd != self._DIR_FD_SENTINEL:
                real_close(fd)

        monkeypatch.setattr(
            ds_io, "_open_dir_fd", lambda directory: self._DIR_FD_SENTINEL
        )
        monkeypatch.setattr(os, "fsync", fake_fsync)
        monkeypatch.setattr(os, "close", fake_close)

    def test_normal_save_reports_full_durability(self, monkeypatch, caplog):
        # Directory barrier succeeds -> FULL, content persisted, no degradation
        # warning.
        self._force_dir_barrier(monkeypatch)
        with caplog.at_level("WARNING", logger="kohakuterrarium"):
            result = ds.save_settings(_enabled_settings())
        assert result.durability is ds.SaveDurability.FULL
        # Still a full DriveSettings carrying the new revision + persisted bytes.
        assert result.revision == ds.current_revision()
        assert ds.load_settings().registrations["generic"].enabled is True
        assert not [
            r for r in caplog.records if getattr(r, "durability", None) == "file_only"
        ]

    def test_dir_barrier_einval_reports_file_only_and_warns(self, monkeypatch, caplog):
        # Directory fsync rejected with EINVAL: file CONTENTS still persist, but the
        # weakened durability must surface as FILE_ONLY + a structured WARNING.
        self._force_dir_barrier(
            monkeypatch, fsync_error=OSError(errno.EINVAL, "dir fsync unsupported")
        )
        with caplog.at_level("WARNING", logger="kohakuterrarium"):
            result = ds.save_settings(_enabled_settings())
        assert result.durability is ds.SaveDurability.FILE_ONLY
        # File contents are crash-durable + persisted despite the missing barrier.
        assert result.revision == ds.current_revision()
        assert ds.load_settings().registrations["generic"].enabled is True
        # A structured, caller-visible WARNING (durability field == file_only) names
        # the degradation — distinct from the io-layer EINVAL note that carries no
        # ``durability`` field, so this asserts the studio-boundary signal.
        degraded = [
            r
            for r in caplog.records
            if r.levelname == "WARNING"
            and getattr(r, "durability", None) == "file_only"
        ]
        assert degraded, "save must warn when durability degrades to FILE_ONLY"
        assert "FILE_ONLY" in degraded[0].getMessage()
