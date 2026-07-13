"""``kt run`` forwards resolved Drive settings into the engine (design §8.4, Q4 #6).

The managed local ``kt run`` path resolves the host Drive settings once and splats
them into ``Terrarium(...)``. Captured at the constructor seam so the test does not
need a real creature/LLM.
"""

import pytest

from kohakuterrarium.cli import run as run_mod
from kohakuterrarium.studio.identity import drive_settings as ds
from kohakuterrarium.studio.identity.drive_settings import (
    DriveSettings,
    RegistrationSetting,
)
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig


class _Abort(Exception):
    pass


class _CapturingTerrarium:
    last_kwargs: dict | None = None

    def __init__(self, **kwargs):
        _CapturingTerrarium.last_kwargs = kwargs

    async def __aenter__(self):
        raise _Abort()

    async def __aexit__(self, *exc):
        return False


async def _drive_run(monkeypatch):
    monkeypatch.setattr(run_mod, "Terrarium", _CapturingTerrarium)
    _CapturingTerrarium.last_kwargs = None
    with pytest.raises(_Abort):
        await run_mod._run(
            "no-such-agent",
            session=None,
            llm=None,
            io_mode=None,
            extra_creatures=[],
            extra_channels=[],
        )
    return _CapturingTerrarium.last_kwargs


async def test_run_forwards_enabled_defaults(monkeypatch):
    kwargs = await _drive_run(monkeypatch)
    assert kwargs["drive_config"].enabled is True
    assert [r.name for r in kwargs["drive_registrations"]] == ["generic", "goal"]


async def test_run_forwards_enabled_settings(monkeypatch):
    ds.save_settings(
        DriveSettings(
            runtime=DriveRuntimeConfig(enabled=True),
            registrations={"generic": RegistrationSetting(enabled=True)},
        )
    )
    kwargs = await _drive_run(monkeypatch)
    assert kwargs["drive_config"] is not None
    assert kwargs["drive_config"].enabled is True
    assert [r.name for r in kwargs["drive_registrations"]] == ["generic"]
