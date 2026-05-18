"""``kt self-update`` (06b): launcher-only, no pip fallback."""

import argparse

import pytest

from kohakuterrarium.cli import self_update as _u
from kohakuterrarium.launcher import migration as _migration
from kohakuterrarium.launcher.update_runner import UpdateResult


@pytest.fixture(autouse=True)
def cfg_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _args(**kw):
    defaults = {
        "check_only": False,
        "dry_run": False,
        "rollback": False,
        "channel": None,
        "feed_url": None,
        "pin": None,
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestRefusesOutsideLauncher:
    def test_no_launcher_install_prints_hint(self, monkeypatch, capsys):
        monkeypatch.setattr(_migration, "is_launcher_install", lambda: False)
        # The CLI imports the symbol at module load — patch the binding
        # the CLI sees.
        monkeypatch.setattr(_u, "is_launcher_install", lambda: False)
        rc = _u.self_update_cli(_args())
        out = capsys.readouterr().out
        assert rc == 2
        assert "not running inside a launcher install" in out

    def test_check_only_outside_launcher_returns_2(self, monkeypatch, capsys):
        monkeypatch.setattr(_u, "is_launcher_install", lambda: False)
        rc = _u.self_update_cli(_args(check_only=True))
        out = capsys.readouterr().out
        assert rc == 2
        assert "current=" in out


class TestLauncherInstall:
    @pytest.fixture(autouse=True)
    def _pretend_launcher(self, monkeypatch):
        monkeypatch.setattr(_u, "is_launcher_install", lambda: True)

    def test_check_only_up_to_date_returns_1(self, monkeypatch, capsys):
        monkeypatch.setattr(
            _u,
            "probe_only",
            lambda: UpdateResult(ok=True, version="1.5.1", skipped_reason="up-to-date"),
        )
        rc = _u.self_update_cli(_args(check_only=True))
        out = capsys.readouterr().out
        assert rc == 1
        assert "latest=1.5.1" in out

    def test_check_only_update_available_returns_0(self, monkeypatch, capsys):
        monkeypatch.setattr(
            _u,
            "probe_only",
            lambda: UpdateResult(ok=True, version="1.5.2"),
        )
        rc = _u.self_update_cli(_args(check_only=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert "latest=1.5.2" in out

    def test_dry_run_does_not_install(self, monkeypatch, capsys):
        class _T:
            version = "1.5.2"
            build_id = "b"
            url = "u"
            sha256 = "s"
            size_bytes = 0
            platform = "linux-x64"
            py_abi = "cp313"
            release_notes_url = None

        monkeypatch.setattr(_u, "resolve_feed", lambda cfg, **k: _T())
        called = {"hit": False}
        monkeypatch.setattr(
            _u,
            "run_update",
            lambda: called.__setitem__("hit", True) or UpdateResult(ok=True),
        )
        rc = _u.self_update_cli(_args(dry_run=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert "--dry-run" in out
        assert called["hit"] is False

    def test_update_happy_path(self, monkeypatch, capsys):
        monkeypatch.setattr(
            _u,
            "run_update",
            lambda: UpdateResult(ok=True, version="1.5.2", restart_required=True),
        )
        rc = _u.self_update_cli(_args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "updated to 1.5.2" in out

    def test_update_already_up_to_date(self, monkeypatch, capsys):
        monkeypatch.setattr(
            _u,
            "run_update",
            lambda: UpdateResult(ok=True, version="1.5.1", skipped_reason="up-to-date"),
        )
        rc = _u.self_update_cli(_args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "already on 1.5.1" in out

    def test_update_failure_returns_1(self, monkeypatch, capsys):
        monkeypatch.setattr(
            _u, "run_update", lambda: UpdateResult(ok=False, error="boom")
        )
        rc = _u.self_update_cli(_args())
        out = capsys.readouterr().out
        assert rc == 1
        assert "boom" in out

    def test_rollback_dispatches(self, monkeypatch, capsys):
        monkeypatch.setattr(
            _u, "rollback", lambda: UpdateResult(ok=True, version="1.5.0")
        )
        rc = _u.self_update_cli(_args(rollback=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert "1.5.0" in out


class TestOverrideApplication:
    def test_channel_override_is_sticky(self, monkeypatch, cfg_home):
        monkeypatch.setattr(_u, "is_launcher_install", lambda: True)
        monkeypatch.setattr(_u, "run_update", lambda: UpdateResult(ok=True))
        rc = _u.self_update_cli(_args(channel="nightly"))
        assert rc == 0
        from kohakuterrarium.launcher import settings as _s

        cfg = _s.load()
        assert cfg.channel == "nightly"

    def test_feed_url_override_switches_to_custom_kind(self, monkeypatch, cfg_home):
        monkeypatch.setattr(_u, "is_launcher_install", lambda: True)
        monkeypatch.setattr(_u, "run_update", lambda: UpdateResult(ok=True))
        rc = _u.self_update_cli(_args(feed_url="https://my.mirror"))
        assert rc == 0
        from kohakuterrarium.launcher import settings as _s

        cfg = _s.load()
        assert cfg.feed.kind == "custom"
        assert cfg.feed.url == "https://my.mirror"
