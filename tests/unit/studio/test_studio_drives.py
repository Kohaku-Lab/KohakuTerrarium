"""Studio-owned engine resolves managed Drive settings; the drives façade.

When Studio owns its engine it is the managed surface and resolves the node's
Drive settings into explicit constructor args (design §8.4/§8.5). An engine
handed in explicitly is never re-resolved. The ``identity.drives`` façade exposes
load/save/resolve/status/apply.
"""

from kohakuterrarium.studio import Studio
from kohakuterrarium.studio.identity import drive_settings as ds
from kohakuterrarium.studio.identity.drive_settings import (
    DriveSettings,
    RegistrationSetting,
)
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.engine import Terrarium


def _enable_generic():
    ds.save_settings(
        DriveSettings(
            runtime=DriveRuntimeConfig(enabled=True),
            registrations={"generic": RegistrationSetting(enabled=True)},
        )
    )


async def test_owned_engine_enabled_by_default():
    async with Studio() as s:
        assert s.engine.drives is not None


async def test_owned_engine_resolves_enabled_settings():
    _enable_generic()
    async with Studio() as s:
        assert s.engine.drives is not None
        assert s.engine.drives.config.enabled is True


async def test_explicit_engine_is_never_resolved():
    _enable_generic()
    engine = Terrarium()
    async with Studio(engine=engine) as s:
        assert s.engine.drives is engine.drives
        assert s.engine.drives is not None


async def test_drives_facade_save_status_apply():
    async with Studio() as s:
        s.identity.drives.save(
            {
                "runtime": {"enabled": True},
                "registrations": {"generic": {"enabled": True}},
            }
        )
        status = s.identity.drives.status()
        assert status["runtime"]["enabled"] is True
        names = {r["name"] for r in status["registrations"]}
        assert "generic" in names
        # Apply is distinct from save. Removing the default goal registration
        # requires a restart because it changes the enabled kind set.
        result = s.identity.drives.apply()
        assert result["result"] == ds.RESTART_REQUIRED
        assert result["desired_revision"] == ds.current_revision()
