"""Unit tests for :mod:`kohakuterrarium.utils.config_dir`.

The whole module is two functions; both must hit 100% with real
filesystem effects observed (directory creation is the only side
effect, so the test asserts the directory exists after each call).
"""

import os

import pytest

from kohakuterrarium.utils.config_dir import config_dir, config_subdir


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    # Re-home both the env var and the literal ``~`` so neither path
    # touches the real user home.
    monkeypatch.delenv("KT_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    yield


class TestConfigDir:
    def test_default_resolves_under_home_and_creates(self, tmp_path):
        out = config_dir()
        assert out == tmp_path / ".kohakuterrarium"
        # Side effect — directory MUST exist after the call.
        assert out.is_dir()

    def test_env_override_takes_priority(self, monkeypatch, tmp_path):
        override = tmp_path / "elsewhere"
        monkeypatch.setenv("KT_CONFIG_DIR", str(override))
        out = config_dir()
        assert out == override
        assert out.is_dir()

    def test_env_override_with_user_expansion(self, monkeypatch, tmp_path):
        # ``~/foo`` should expand to ``<home>/foo``.
        monkeypatch.setenv("KT_CONFIG_DIR", os.path.join("~", "kt-test-cd"))
        out = config_dir()
        assert out == tmp_path / "kt-test-cd"
        assert out.is_dir()

    def test_idempotent_creation(self, tmp_path):
        a = config_dir()
        b = config_dir()
        assert a == b
        # Calling twice must not raise even though the dir exists.

    def test_empty_env_var_falls_back_to_default(self, monkeypatch, tmp_path):
        # ``setenv`` with empty string — the implementation treats
        # empty as "fall back to default" via ``os.environ.get(...) or _DEFAULT``.
        monkeypatch.setenv("KT_CONFIG_DIR", "")
        out = config_dir()
        assert out == tmp_path / ".kohakuterrarium"


class TestConfigSubdir:
    def test_creates_nested_path(self, tmp_path):
        out = config_subdir("logs")
        assert out == tmp_path / ".kohakuterrarium" / "logs"
        assert out.is_dir()

    def test_multiple_parts_joined(self, tmp_path):
        out = config_subdir("a", "b", "c")
        assert out == tmp_path / ".kohakuterrarium" / "a" / "b" / "c"
        assert out.is_dir()

    def test_zero_parts_returns_root(self, tmp_path):
        out = config_subdir()
        assert out == tmp_path / ".kohakuterrarium"
        assert out.is_dir()

    def test_idempotent(self, tmp_path):
        # Second call when path already exists must not raise.
        a = config_subdir("data")
        b = config_subdir("data")
        assert a == b == tmp_path / ".kohakuterrarium" / "data"
