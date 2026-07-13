"""Drive-runtime resolution in :class:`EnginePool` (design §8.4).

Each per-user engine must get a FRESH IMMUTABLE resolution of the shared operator
policy — no registration/config instance may be shared across users — while its
Drive records/session repository stay per-user. A pool with no resolver inherits
fresh default generic + goal runtimes from ``Terrarium``.
"""

import pytest

from kohakuterrarium.api.auth.engine_pool import EnginePool
from kohakuterrarium.studio.identity import drive_settings as ds
from kohakuterrarium.studio.identity.drive_settings import (
    DriveSettings,
    RegistrationSetting,
)
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.drive.registration import GenericDriveRegistration


@pytest.fixture
def fresh_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    yield tmp_path


def test_no_resolver_builds_default_drive_engines(fresh_dirs):
    pool = EnginePool(max_active=4, idle_timeout_s=0)
    try:
        runtime = pool.get_or_create(1).drives
        assert runtime is not None
        assert [entry.descriptor.name for entry in runtime.snapshot.entries] == [
            "generic",
            "goal",
        ]
    finally:
        pool.evict_all()


def test_resolver_is_called_per_build_with_fresh_instances(fresh_dirs):
    calls = {"n": 0}

    def resolver():
        calls["n"] += 1
        # A fresh config + fresh registration instance every call.
        return {
            "drive_config": DriveRuntimeConfig(enabled=True),
            "drive_registrations": (GenericDriveRegistration(),),
            "drive_store": None,
        }

    pool = EnginePool(max_active=4, idle_timeout_s=0, drive_resolver=resolver)
    try:
        e1 = pool.get_or_create(1)
        e2 = pool.get_or_create(2)
        # Resolved once per engine, not once per pool.
        assert calls["n"] == 2
        assert e1.drives is not None and e2.drives is not None
        # No registration instance leaks between users.
        reg1 = e1.drives.snapshot.entries[0].registration
        reg2 = e2.drives.snapshot.entries[0].registration
        assert reg1 is not reg2
        # A cache hit does NOT re-resolve.
        assert pool.get_or_create(1) is e1
        assert calls["n"] == 2
    finally:
        pool.evict_all()


def test_real_studio_resolver_gives_per_user_immutable_runtime(fresh_dirs):
    # Enable generic in the shared operator policy; every per-user engine resolves
    # its own instances from that one file.
    ds.save_settings(
        DriveSettings(
            runtime=DriveRuntimeConfig(enabled=True),
            registrations={"generic": RegistrationSetting(enabled=True)},
        )
    )
    pool = EnginePool(
        max_active=4, idle_timeout_s=0, drive_resolver=ds.resolve_drive_kwargs
    )
    try:
        e1 = pool.get_or_create(1)
        e2 = pool.get_or_create(2)
        assert e1.drives is not None and e2.drives is not None
        assert (
            e1.drives.snapshot.entries[0].registration
            is not e2.drives.snapshot.entries[0].registration
        )
        # config is a frozen (immutable) DTO — sharing its value is safe.
        assert e1.drives.config.enabled is True
    finally:
        pool.evict_all()
