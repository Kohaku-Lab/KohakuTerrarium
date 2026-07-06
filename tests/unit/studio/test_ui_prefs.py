"""Unit tests for :mod:`kohakuterrarium.studio.identity.ui_prefs`."""

import json

import pytest

from kohakuterrarium.studio.identity.ui_prefs import (
    DEFAULTS,
    load_prefs,
    save_prefs,
    ui_prefs_path,
)


@pytest.fixture(autouse=True)
def _redirect_path(tmp_path, monkeypatch):
    """Isolate ui_prefs via ``KT_CONFIG_DIR`` — never touch the real home.

    ``ui_prefs.py`` resolves its path through ``config_dir()``; the env
    var is the documented isolation seam.
    """
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path / "ui_prefs.json"


# ── ui_prefs_path ────────────────────────────────────────────────


class TestUiPrefsPath:
    def test_returns_module_path(self, _redirect_path):
        assert ui_prefs_path() == _redirect_path


# ── load_prefs ───────────────────────────────────────────────────


class TestLoadPrefs:
    def test_returns_defaults_when_missing(self):
        out = load_prefs()
        assert out == DEFAULTS
        # Returns a copy, not the live dict.
        out["theme"] = "MUTATED"
        assert DEFAULTS["theme"] != "MUTATED"

    def test_merges_over_defaults(self, _redirect_path):
        _redirect_path.write_text(json.dumps({"theme": "dark", "extra": 1}))
        out = load_prefs()
        assert out["theme"] == "dark"
        assert out["extra"] == 1
        # Defaults still present for fields the file didn't override.
        assert out["nav-expanded"] is True

    def test_malformed_file_falls_back_to_defaults(self, _redirect_path):
        _redirect_path.write_text("{not json")
        out = load_prefs()
        assert out == DEFAULTS

    def test_non_dict_root_falls_back(self, _redirect_path):
        _redirect_path.write_text(json.dumps([1, 2, 3]))
        out = load_prefs()
        assert out == DEFAULTS


# ── save_prefs ───────────────────────────────────────────────────


class TestSavePrefs:
    def test_persists_merged_values(self, _redirect_path):
        result = save_prefs({"theme": "light"})
        assert result["theme"] == "light"
        on_disk = json.loads(_redirect_path.read_text(encoding="utf-8"))
        assert on_disk["theme"] == "light"

    def test_creates_parent_dir(self, tmp_path, monkeypatch):
        # A not-yet-existing config dir is created on first write.
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "sub" / "nested"))
        save_prefs({"theme": "dark"})
        assert (tmp_path / "sub" / "nested" / "ui_prefs.json").exists()

    def test_empty_values_writes_defaults(self, _redirect_path):
        result = save_prefs({})
        assert result["theme"] == DEFAULTS["theme"]
        assert _redirect_path.exists()

    def test_null_value_deletes_key(self, _redirect_path):
        # The frontend's ``removeHybridPref`` sends ``null`` — the key
        # must drop out of the file instead of persisting as JSON null
        # litter (one entry per removed chat-draft/tab key, forever).
        save_prefs({"kt.chat.draft.inst.main": "half-typed"})
        save_prefs({"kt.chat.draft.inst.main": None})
        on_disk = json.loads(_redirect_path.read_text(encoding="utf-8"))
        assert "kt.chat.draft.inst.main" not in on_disk

    def test_null_on_default_key_resets_to_default(self, _redirect_path):
        save_prefs({"theme": "dark"})
        result = save_prefs({"theme": None})
        # Dropped from the response/merged view…
        assert "theme" not in result
        # …and the next load falls back to the default.
        assert load_prefs()["theme"] == DEFAULTS["theme"]
