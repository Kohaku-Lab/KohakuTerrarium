"""Settings IO + schema-coercion behaviour for the launcher."""

import json

import pytest

from kohakuterrarium.launcher import settings as _s


@pytest.fixture(autouse=True)
def _isolate_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestLoadDefaults:
    def test_missing_file_creates_defaults(self, _isolate_config_dir):
        s = _s.load()
        assert s.source.kind == "pypi"
        assert s.source.spec is None
        assert s.source.extras == []
        assert s.update.mode == "notify-on-launch"
        assert s.update.check_cache_hours == 24
        assert s.runtime.venv_path  # populated with default

    def test_default_file_is_written_on_first_load(self, _isolate_config_dir):
        _s.load()
        path = _isolate_config_dir / "app-settings.json"
        assert path.is_file()
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["source"]["kind"] == "pypi"
        assert raw["update"]["mode"] == "notify-on-launch"


class TestSaveRoundTrip:
    def test_save_then_load_round_trip(self, _isolate_config_dir):
        original = _s.AppSettings(
            source=_s.SourceConfig(kind="git", spec="git+https://x@main"),
            update=_s.UpdateConfig(mode="auto-on-launch", check_cache_hours=6),
        )
        _s.save(original)
        loaded = _s.load()
        assert loaded.source.kind == "git"
        assert loaded.source.spec == "git+https://x@main"
        assert loaded.update.mode == "auto-on-launch"
        assert loaded.update.check_cache_hours == 6


class TestInvalidFieldsFallBack:
    def test_invalid_kind_falls_back_to_defaults(
        self, _isolate_config_dir, monkeypatch
    ):
        (_isolate_config_dir / "app-settings.json").write_text(
            json.dumps(
                {
                    "source": {"kind": "bogus", "spec": None, "extras": []},
                    "update": {"mode": "manual", "check-cache-hours": 24},
                    "runtime": {},
                }
            ),
            encoding="utf-8",
        )
        s = _s.load()
        # Source resets entirely, update retained.
        assert s.source.kind == "pypi"
        assert s.update.mode == "manual"

    def test_invalid_mode_falls_back_to_defaults(self, _isolate_config_dir):
        (_isolate_config_dir / "app-settings.json").write_text(
            json.dumps(
                {
                    "source": {"kind": "pypi", "spec": None, "extras": []},
                    "update": {"mode": "fortnightly", "check-cache-hours": 24},
                    "runtime": {},
                }
            ),
            encoding="utf-8",
        )
        s = _s.load()
        assert s.update.mode == "notify-on-launch"

    def test_garbage_json_falls_back_to_full_defaults(self, _isolate_config_dir):
        (_isolate_config_dir / "app-settings.json").write_text(
            "not-json-at-all", encoding="utf-8"
        )
        s = _s.load()
        assert s.source.kind == "pypi"
        assert s.update.mode == "notify-on-launch"


class TestReset:
    def test_reset_overwrites_with_defaults(self, _isolate_config_dir):
        _s.save(_s.AppSettings(source=_s.SourceConfig(kind="git", spec="git+x@v1")))
        out = _s.reset()
        assert out.source.kind == "pypi"
        on_disk = _s.load()
        assert on_disk.source.kind == "pypi"
