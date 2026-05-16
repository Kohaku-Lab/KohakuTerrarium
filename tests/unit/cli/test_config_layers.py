"""Unit tests for the layered config loader.

Pins the precedence contract: built-in defaults < YAML < env < CLI.
"""

import pytest

from kohakuterrarium.cli._config_layers import (
    apply_cli_to_overrides,
    load_layered_config,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip every layered-config env-var so each test starts blank."""
    for key in (
        "KT_HTTP_HOST",
        "KT_HTTP_PORT",
        "KT_LAB_BIND",
        "KT_LAB_TOKEN",
        "KT_HOST_TOKEN",
        "KT_HOST_URL",
        "KT_CLIENT_NAME",
        "KT_CONFIG_FILE",
        "KT_LOG_LEVEL",
        "KT_SESSION_DIR",
        "KT_HEARTBEAT_INTERVAL",
    ):
        monkeypatch.delenv(key, raising=False)
    # KT_CONFIG_DIR is set by the autouse fixture in conftest.py to a
    # tmp path; the loader will look for ``<that>/host.yaml`` which
    # doesn't exist, so YAML layer is naturally empty.


class TestDefaults:
    def test_host_defaults(self):
        cfg = load_layered_config("host")
        assert cfg["http"]["host"] == "127.0.0.1"
        assert cfg["http"]["port"] == 8001
        assert cfg["lab"]["bind"] == "127.0.0.1:8100"

    def test_client_defaults(self):
        cfg = load_layered_config("client")
        assert cfg["heartbeat_interval"] == 5.0
        assert cfg["host_url"] == ""

    def test_all_defaults(self):
        cfg = load_layered_config("all")
        assert cfg["http"]["host"] == "0.0.0.0"
        assert cfg["client_name"] == "local-1"

    def test_unknown_role_raises(self):
        with pytest.raises(ValueError, match="unknown config role"):
            load_layered_config("worker-deluxe")


class TestYamlLayer:
    def test_yaml_overrides_defaults(self, tmp_path, monkeypatch):
        yml = tmp_path / "host.yaml"
        yml.write_text(
            "http:\n  port: 9000\nlab:\n  bind: 1.2.3.4:9100\n", encoding="utf-8"
        )
        monkeypatch.setenv("KT_CONFIG_FILE", str(yml))
        cfg = load_layered_config("host")
        assert cfg["http"]["port"] == 9000
        assert cfg["lab"]["bind"] == "1.2.3.4:9100"
        # Untouched keys keep defaults.
        assert cfg["http"]["host"] == "127.0.0.1"

    def test_invalid_yaml_raises(self, tmp_path, monkeypatch):
        yml = tmp_path / "host.yaml"
        yml.write_text(":\n: -", encoding="utf-8")
        monkeypatch.setenv("KT_CONFIG_FILE", str(yml))
        with pytest.raises(ValueError, match="invalid YAML"):
            load_layered_config("host")


class TestEnvLayer:
    def test_env_overrides_yaml(self, tmp_path, monkeypatch):
        yml = tmp_path / "host.yaml"
        yml.write_text("http:\n  port: 9000\n", encoding="utf-8")
        monkeypatch.setenv("KT_CONFIG_FILE", str(yml))
        monkeypatch.setenv("KT_HTTP_PORT", "9001")
        cfg = load_layered_config("host")
        assert cfg["http"]["port"] == 9001

    def test_env_int_coercion(self, monkeypatch):
        monkeypatch.setenv("KT_HTTP_PORT", "9999")
        cfg = load_layered_config("host")
        assert cfg["http"]["port"] == 9999
        assert isinstance(cfg["http"]["port"], int)


class TestCliLayer:
    def test_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("KT_HTTP_PORT", "9001")
        cfg = load_layered_config("host", {"http": {"port": 9002}})
        assert cfg["http"]["port"] == 9002


class TestApplyCliToOverrides:
    def test_skips_falsy_by_default(self):
        import argparse

        ns = argparse.Namespace(port=0, host="")
        out = apply_cli_to_overrides(
            ns, {"port": ("http", "port"), "host": ("http", "host")}
        )
        assert out == {}

    def test_includes_truthy(self):
        import argparse

        ns = argparse.Namespace(port=9000, host="0.0.0.0")
        out = apply_cli_to_overrides(
            ns, {"port": ("http", "port"), "host": ("http", "host")}
        )
        assert out == {"http": {"port": 9000, "host": "0.0.0.0"}}
