"""Unit tests for :mod:`kohakuterrarium.cli.identity_drive` (``kt config drive``).

Exercises show / set / registrations / apply over an isolated ``KT_CONFIG_DIR``
so the canonical ``drive-settings.yaml`` round-trips through the Studio settings
façade without touching a real engine.
"""

import pytest

from kohakuterrarium.cli import identity_drive as cd
from kohakuterrarium.studio.identity import drive_settings as ds


@pytest.fixture
def config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "cfg"))
    return tmp_path


class TestShow:
    def test_show_absent_file_defaults(self, config_home, capsys):
        code = cd.show_cli()
        out = capsys.readouterr().out
        assert code == 0
        assert "runtime.enabled: True" in out
        assert "goal" in out

    def test_registrations_lists_generic(self, config_home, capsys):
        code = cd.registrations_cli()
        out = capsys.readouterr().out
        assert code == 0
        assert "generic" in out


class TestSet:
    def test_set_runtime_enabled(self, config_home, capsys):
        assert cd.set_cli("enabled", "true") == 0
        settings = ds.load_settings()
        assert settings.runtime.enabled is True

    def test_set_int_field(self, config_home):
        cd.set_cli("enabled", "true")
        assert cd.set_cli("max_active_per_creature", "4") == 0
        assert ds.load_settings().runtime.max_active_per_creature == 4

    def test_set_registration_toggle(self, config_home, capsys):
        assert cd.set_cli("registration:generic", "on") == 0
        settings = ds.load_settings()
        assert settings.registrations["generic"].enabled is True
        # It now shows as enabled in the panel.
        cd.show_cli()
        assert "generic" in capsys.readouterr().out

    def test_set_missing_args_usage(self, config_home, capsys):
        assert cd.set_cli(None, None) == 2
        assert "usage" in capsys.readouterr().out.lower()


class TestApply:
    def test_apply_disabled_is_live(self, config_home, capsys):
        cd.set_cli("enabled", "false")
        code = cd.apply_cli()
        out = capsys.readouterr().out
        assert code == 0
        assert "applied_live" in out

    def test_apply_enabled_reports_restart_required(self, config_home, capsys):
        cd.set_cli("enabled", "true")
        cd.set_cli("registration:generic", "on")
        code = cd.apply_cli()
        out = capsys.readouterr().out
        assert code == 0
        assert "restart_required" in out
        assert "generic" in out
