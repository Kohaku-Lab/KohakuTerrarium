"""Unit tests for the rich-CLI Drives settings tab section.

Deterministic + isolated: ``KT_CONFIG_DIR`` points at a tmp dir so
``drive-settings.yaml`` and the registration catalog scan never touch the real
config home. Covers row building, runtime/registration edits, the two-phase
save-then-apply contract, and honest applied_live / restart_required feedback.
"""

import pytest

from kohakuterrarium.builtins.cli_rich.dialogs.settings_drives import (
    DriveSettingsSection,
)
from kohakuterrarium.studio.identity.drive_settings import load_settings


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _row(section, *, type=None, key=None):
    for row in section.rows:
        if type is not None and row["type"] != type:
            continue
        if key is not None and row.get("key") != key:
            continue
        return row
    return None


def _cursor_to(section, row):
    section.cursor = section.rows.index(row)


def test_reload_builds_runtime_and_registration_rows(isolated_config):
    section = DriveSettingsSection()
    section.reload()
    types = {r["type"] for r in section.rows}
    assert {"toggle", "int", "registration", "action"} <= types
    # The builtin generic registration is always discoverable.
    regs = [r for r in section.rows if r["type"] == "registration"]
    assert any(r["name"] == "generic" for r in regs)
    enabled = _row(section, type="toggle", key="enabled")
    assert enabled["value"] is False  # default runtime is disabled


def test_toggle_and_int_edit(isolated_config):
    section = DriveSettingsSection()
    section.reload()
    enabled = _row(section, type="toggle", key="enabled")
    _cursor_to(section, enabled)
    section._activate()
    assert enabled["value"] is True
    field = _row(section, type="int", key="max_active_per_creature")
    _cursor_to(section, field)
    section._activate()
    assert section.editing
    section._edit_buffer = "12"
    section._commit_edit()
    assert field["value"] == 12
    assert not section.editing


def test_save_persists_runtime_and_registration(isolated_config):
    section = DriveSettingsSection()
    section.reload()
    _cursor_to(section, _row(section, type="toggle", key="enabled"))
    section._activate()  # enable runtime
    reg = _row(section, type="registration")
    reg["enabled"] = True  # enable the generic registration
    section._save()
    assert "saved" in section.flash
    persisted = load_settings()
    assert persisted.runtime.enabled is True
    assert persisted.registrations[reg["name"]].enabled is True


def test_apply_reports_restart_required_when_enabling_without_engine(isolated_config):
    section = DriveSettingsSection(get_engine=lambda: None)
    section.reload()
    _cursor_to(section, _row(section, type="toggle", key="enabled"))
    section._activate()  # enable runtime in the rows
    _row(section, type="registration")["enabled"] = True  # runtime needs a registration
    section._save()  # persist so apply reads the enabled file
    section._apply()
    assert section.apply_result["result"] == "restart_required"
    body = section.render_body()
    assert body is not None  # renders the apply-result block without raising


def test_apply_applied_live_when_runtime_stays_disabled(isolated_config):
    section = DriveSettingsSection(get_engine=lambda: None)
    section.reload()
    section._apply()  # nothing enabled, no engine -> applied_live (no-op)
    assert section.apply_result["result"] == "applied_live"
