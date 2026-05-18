"""Settings IO + coercion for the 06b schema."""

import json

import pytest

from kohakuterrarium.launcher import settings as _s


@pytest.fixture
def cfg_home(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestLoadDefaults:
    def test_load_creates_defaults_when_missing(self, cfg_home):
        s = _s.load()
        assert s.feed.kind == "github_releases"
        assert s.feed.repo == _s.DEFAULT_REPO
        assert s.feed.url is None
        assert s.channel == "stable"
        assert s.pinned_version is None
        assert s.update.mode == "notify-on-launch"
        assert s.update.check_cache_hours == 24
        assert s.update.keep_versions == 3
        # File was written so a second read is identical.
        assert (cfg_home / "app-settings.json").is_file()
        assert _s.load() == s


class TestRoundTrip:
    def test_save_load_round_trip(self, cfg_home):
        original = _s.AppSettings(
            feed=_s.FeedConfig(kind="custom", repo="x/y", url="https://example.test"),
            channel="beta",
            pinned_version="1.5.0",
            update=_s.UpdateConfig(
                mode="auto-on-launch", check_cache_hours=2, keep_versions=5
            ),
            runtime=_s.RuntimeConfig(
                active_version="1.5.0",
                active_build_id="b1",
                last_check_at="2026-05-19T00:00:00+00:00",
                last_check_error=None,
            ),
        )
        _s.save(original)
        loaded = _s.load()
        assert loaded == original


class TestLegacyTolerant:
    def test_legacy_06_source_block_ignored(self, cfg_home):
        path = cfg_home / "app-settings.json"
        path.write_text(
            json.dumps(
                {
                    "source": {
                        "kind": "git",
                        "spec": "git+https://x",
                        "extras": ["full"],
                    },
                    "update": {"mode": "manual", "check-cache-hours": 8},
                    "runtime": {"venv-path": "/old/path"},
                }
            ),
            encoding="utf-8",
        )
        s = _s.load()
        # Source block is dropped; defaults take over.
        assert s.feed.kind == "github_releases"
        # update.mode is salvaged.
        assert s.update.mode == "manual"
        # Cache hours preserved.
        assert s.update.check_cache_hours == 8

    def test_corrupt_json_resets_to_defaults(self, cfg_home):
        (cfg_home / "app-settings.json").write_text("{not json", encoding="utf-8")
        s = _s.load()
        assert s.feed.kind == "github_releases"


class TestCoercionGuards:
    def test_custom_feed_without_url_falls_back(self, cfg_home):
        path = cfg_home / "app-settings.json"
        path.write_text(
            json.dumps({"feed": {"kind": "custom"}, "update": {"mode": "manual"}}),
            encoding="utf-8",
        )
        s = _s.load()
        assert s.feed.kind == "github_releases"

    def test_non_https_url_ignored(self, cfg_home):
        path = cfg_home / "app-settings.json"
        path.write_text(
            json.dumps(
                {
                    "feed": {"kind": "custom", "url": "http://insecure.test"},
                    "channel": "stable",
                }
            ),
            encoding="utf-8",
        )
        s = _s.load()
        # http:// rejected → fallback to github_releases.
        assert s.feed.kind == "github_releases"

    def test_invalid_channel_falls_back(self, cfg_home):
        path = cfg_home / "app-settings.json"
        path.write_text(json.dumps({"channel": "experimental"}), encoding="utf-8")
        assert _s.load().channel == "stable"

    def test_invalid_keep_versions_falls_back(self, cfg_home):
        path = cfg_home / "app-settings.json"
        path.write_text(
            json.dumps({"update": {"mode": "manual", "keep-versions": -1}}),
            encoding="utf-8",
        )
        assert _s.load().update.keep_versions == _s.DEFAULT_KEEP_VERSIONS


class TestPublicDict:
    def test_to_public_dict_uses_canonical_keys(self):
        s = _s.AppSettings()
        out = _s.to_public_dict(s)
        assert "feed" in out and "channel" in out
        assert "check-cache-hours" in out["update"]
        assert "keep-versions" in out["update"]
        assert "active-version" in out["runtime"]

    def test_from_public_dict_round_trips(self):
        s = _s.AppSettings(
            channel="nightly",
            pinned_version="1.5.0",
            feed=_s.FeedConfig(kind="custom", url="https://x.test"),
        )
        payload = _s.to_public_dict(s)
        back = _s.from_public_dict(payload)
        assert back == s
