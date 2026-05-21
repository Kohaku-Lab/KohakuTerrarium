"""Unit tests for :mod:`api.auth.namespace`."""

import pytest

from kohakuterrarium.api.auth.namespace import (
    shared_session_dir,
    shared_ui_prefs_path,
    user_config_dir,
    user_session_dir,
    user_ui_prefs_path,
)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    yield tmp_path


class TestUserPaths:
    def test_user_config_dir_layout(self, cfg):
        path = user_config_dir(42)
        assert path == cfg / "users" / "42"
        assert path.is_dir()

    def test_user_session_dir_layout(self, cfg):
        path = user_session_dir(7)
        assert path == cfg / "users" / "7" / "sessions"
        assert path.is_dir()

    def test_user_ui_prefs_path(self, cfg):
        path = user_ui_prefs_path(3)
        assert path == cfg / "users" / "3" / "ui_prefs.json"
        # Parent dir is created on first access.
        assert path.parent.is_dir()

    def test_string_user_id_coerced(self, cfg):
        # Defensive: id-coercion to int prevents path traversal via
        # weird ids.  Even though the type hint is int, we run the
        # actual cast inside user_config_dir.
        path = user_config_dir("11")  # type: ignore[arg-type]
        assert path.name == "11"


class TestSharedPaths:
    def test_shared_session_dir(self, cfg):
        assert shared_session_dir() == cfg / "sessions"

    def test_shared_ui_prefs_path(self, cfg):
        assert shared_ui_prefs_path() == cfg / "ui_prefs.json"


class TestPerUserIsolation:
    def test_two_users_get_distinct_dirs(self, cfg):
        a = user_config_dir(1)
        b = user_config_dir(2)
        assert a != b
        assert a.parent == b.parent  # both under users/
