"""Drive-runtime resolution in the API deps singleton + the file-blind rule.

The managed host singleton resolves the host Drive settings once into explicit
engine args (design §8.4). A programmatic ``Terrarium(...)`` with no Drive args —
the shape the lab-host coordination engine uses — stays file-blind and
Drive-disabled regardless of the settings file (design §8.5).
"""

import pytest

from kohakuterrarium.api.deps import get_service_legacy, set_service
from kohakuterrarium.studio.identity import drive_settings as ds
from kohakuterrarium.studio.identity.drive_settings import (
    DriveSettings,
    RegistrationSetting,
)
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.utils.config_dir import config_dir


@pytest.fixture(autouse=True)
def _reset_singleton():
    set_service(None)
    yield
    set_service(None)


def _enable_generic():
    ds.save_settings(
        DriveSettings(
            runtime=DriveRuntimeConfig(enabled=True),
            registrations={"generic": RegistrationSetting(enabled=True)},
        )
    )


def test_singleton_is_drive_disabled_by_default():
    svc = get_service_legacy()
    assert svc.engine.drives is None


def test_singleton_resolves_enabled_settings():
    _enable_generic()
    svc = get_service_legacy()
    assert svc.engine.drives is not None
    assert svc.engine.drives.config.enabled is True


def test_programmatic_engine_stays_file_blind():
    # The lab-host coordination engine constructs Terrarium(session_dir=...) with
    # NO drive args; even with the runtime enabled in settings it must stay
    # Drive-disabled — a programmatic engine never reads config_dir().
    _enable_generic()
    engine = Terrarium(session_dir=str(config_dir() / "sessions"))
    assert engine.drives is None
