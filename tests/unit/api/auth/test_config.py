"""Unit tests for ``api.auth.config``.

The config is read fresh on every call; tests verify precedence
(env > ``*_FILE`` > TOML > default), permissive coercion, malformed
input rejection, and the capabilities-shape projection used by the
``/api/auth/capabilities`` route.
"""

import pytest

from kohakuterrarium.api.auth.config import AuthConfig, load_auth_config


@pytest.fixture(autouse=True)
def _clear_auth_env(monkeypatch):
    """Strip every ``KT_AUTH_*`` env var so each test starts clean."""
    for name in list(os_environ_snapshot_keys()):
        if name.startswith("KT_AUTH_"):
            monkeypatch.delenv(name, raising=False)


def os_environ_snapshot_keys() -> list[str]:
    """Helper — separate function so the autouse fixture doesn't import
    ``os`` at module top (the test file pulls it indirectly through
    monkeypatch but explicit is friendlier to ruff)."""
    import os

    return list(os.environ.keys())


class TestDefaults:
    def test_blank_environment_yields_all_off(self):
        cfg = load_auth_config()
        assert cfg.host_token == ""
        assert cfg.admin_token == ""
        assert cfg.multi_user == "off"
        assert cfg.registration == "admin_only"
        assert cfg.loopback_bypass is True
        assert cfg.session_expire_hours == 168
        assert cfg.session_idle_minutes == 0
        assert cfg.bcrypt_rounds == 12
        # Layer flags
        assert cfg.host_token_enabled is False
        assert cfg.admin_token_enabled is False
        assert cfg.multi_user_enabled is False


class TestEnvVarPrecedence:
    def test_env_var_sets_host_token(self, monkeypatch):
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN", "abc123")
        cfg = load_auth_config()
        assert cfg.host_token == "abc123"
        assert cfg.host_token_enabled is True

    def test_env_var_sets_admin_token(self, monkeypatch):
        monkeypatch.setenv("KT_AUTH_ADMIN_TOKEN", "xyz789")
        cfg = load_auth_config()
        assert cfg.admin_token == "xyz789"
        assert cfg.admin_token_enabled is True

    def test_env_var_strips_surrounding_whitespace(self, monkeypatch):
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN", "   tokenish   ")
        cfg = load_auth_config()
        assert cfg.host_token == "tokenish"

    def test_empty_string_env_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN", "")
        cfg = load_auth_config()
        assert cfg.host_token == ""
        assert cfg.host_token_enabled is False

    def test_empty_env_overrides_toml(self, monkeypatch):
        # Audit-caught: prior "or"-chained fallback let
        # ``KT_AUTH_HOST_TOKEN=""`` silently revive the TOML value.
        # Operators who set the env to empty expect the gate OFF.
        import os
        from pathlib import Path

        cfg_dir = os.environ["KT_CONFIG_DIR"]
        Path(cfg_dir).mkdir(parents=True, exist_ok=True)
        (Path(cfg_dir) / "config.toml").write_text(
            '[auth]\nhost_token = "from-toml"\n', encoding="utf-8"
        )
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN", "")
        cfg = load_auth_config()
        # Explicit empty env wins over TOML — gate is off.
        assert cfg.host_token == ""
        assert cfg.host_token_enabled is False

    def test_unset_env_falls_through_to_toml(self, monkeypatch):
        # Counterpart: env NOT in os.environ → use TOML.
        import os
        from pathlib import Path

        cfg_dir = os.environ["KT_CONFIG_DIR"]
        Path(cfg_dir).mkdir(parents=True, exist_ok=True)
        (Path(cfg_dir) / "config.toml").write_text(
            '[auth]\nhost_token = "from-toml"\n', encoding="utf-8"
        )
        monkeypatch.delenv("KT_AUTH_HOST_TOKEN", raising=False)
        cfg = load_auth_config()
        assert cfg.host_token == "from-toml"


class TestSecretFileFallback:
    def test_file_provides_token_when_env_unset(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "host_token"
        secret_file.write_text("file-token\n", encoding="utf-8")
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN_FILE", str(secret_file))
        cfg = load_auth_config()
        assert cfg.host_token == "file-token"

    def test_env_wins_over_file(self, monkeypatch, tmp_path):
        secret_file = tmp_path / "host_token"
        secret_file.write_text("file-token", encoding="utf-8")
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN_FILE", str(secret_file))
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN", "env-token")
        cfg = load_auth_config()
        assert cfg.host_token == "env-token"

    def test_missing_file_falls_back_to_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN_FILE", str(tmp_path / "does-not-exist"))
        cfg = load_auth_config()
        # No raise; logged warning; stays empty (gate off).
        assert cfg.host_token == ""

    def test_file_first_nonempty_line_is_used(self, monkeypatch, tmp_path):
        # Common shape: blank lines / comments at top, secret on a later
        # line.  We document "first nonempty line" so this is a contract test.
        secret_file = tmp_path / "host_token"
        secret_file.write_text(
            "\n\nactual-secret\nignored-second-line\n", encoding="utf-8"
        )
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN_FILE", str(secret_file))
        cfg = load_auth_config()
        assert cfg.host_token == "actual-secret"


class TestTomlFallback:
    def test_toml_provides_token_when_env_and_file_unset(self, monkeypatch, tmp_path):
        # KT_CONFIG_DIR is honoured by config_dir(); the autouse
        # _default_isolated_config_dir fixture already redirected it.
        # Write a config.toml under that dir and verify the loader
        # picks the [auth] section up.
        import os

        cfg_dir = os.environ["KT_CONFIG_DIR"]
        from pathlib import Path

        toml_path = Path(cfg_dir) / "config.toml"
        toml_path.parent.mkdir(parents=True, exist_ok=True)
        toml_path.write_text(
            '[auth]\nhost_token = "toml-token"\nmulti_user = "optional"\n',
            encoding="utf-8",
        )
        cfg = load_auth_config()
        assert cfg.host_token == "toml-token"
        assert cfg.multi_user == "optional"

    def test_env_wins_over_toml(self, monkeypatch):
        import os
        from pathlib import Path

        cfg_dir = os.environ["KT_CONFIG_DIR"]
        Path(cfg_dir).mkdir(parents=True, exist_ok=True)
        (Path(cfg_dir) / "config.toml").write_text(
            '[auth]\nhost_token = "toml-token"\n', encoding="utf-8"
        )
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN", "env-token")
        cfg = load_auth_config()
        assert cfg.host_token == "env-token"

    def test_malformed_toml_does_not_raise(self, monkeypatch):
        import os
        from pathlib import Path

        cfg_dir = os.environ["KT_CONFIG_DIR"]
        Path(cfg_dir).mkdir(parents=True, exist_ok=True)
        (Path(cfg_dir) / "config.toml").write_text(
            "[auth\nnot really toml", encoding="utf-8"
        )
        # No raise; falls through to defaults.
        cfg = load_auth_config()
        assert cfg.host_token == ""

    def test_missing_toml_is_silent(self):
        # KT_CONFIG_DIR exists from the autouse fixture but has no
        # config.toml — should not log noisily, should not raise.
        cfg = load_auth_config()
        assert cfg.host_token == ""


class TestModeValidation:
    @pytest.mark.parametrize("value", ["off", "optional", "required"])
    def test_valid_multi_user_values_accepted(self, monkeypatch, value):
        monkeypatch.setenv("KT_AUTH_MULTI_USER", value)
        cfg = load_auth_config()
        assert cfg.multi_user == value

    @pytest.mark.parametrize("value", ["enabled", "yes", "true", "garbage"])
    def test_invalid_multi_user_falls_back_to_default(self, monkeypatch, value):
        monkeypatch.setenv("KT_AUTH_MULTI_USER", value)
        cfg = load_auth_config()
        # Defaults
        assert cfg.multi_user == "off"

    @pytest.mark.parametrize("value", ["open", "invite_only", "admin_only"])
    def test_valid_registration_values_accepted(self, monkeypatch, value):
        monkeypatch.setenv("KT_AUTH_REGISTRATION", value)
        cfg = load_auth_config()
        assert cfg.registration == value

    def test_invalid_registration_falls_back(self, monkeypatch):
        monkeypatch.setenv("KT_AUTH_REGISTRATION", "wide_open")
        cfg = load_auth_config()
        assert cfg.registration == "admin_only"


class TestBoolCoercion:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1", True),
            ("true", True),
            ("True", True),
            ("YES", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("no", False),
            ("OFF", False),
        ],
    )
    def test_loopback_bypass_coerces(self, monkeypatch, raw, expected):
        monkeypatch.setenv("KT_AUTH_LOOPBACK_BYPASS", raw)
        cfg = load_auth_config()
        assert cfg.loopback_bypass is expected

    def test_garbage_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("KT_AUTH_LOOPBACK_BYPASS", "definitely_not_bool")
        cfg = load_auth_config()
        assert cfg.loopback_bypass is True  # default


class TestIntCoercion:
    def test_session_expire_hours_from_env(self, monkeypatch):
        monkeypatch.setenv("KT_AUTH_SESSION_EXPIRE_HOURS", "24")
        cfg = load_auth_config()
        assert cfg.session_expire_hours == 24

    def test_session_expire_hours_garbage_falls_back(self, monkeypatch):
        monkeypatch.setenv("KT_AUTH_SESSION_EXPIRE_HOURS", "many")
        cfg = load_auth_config()
        assert cfg.session_expire_hours == 168

    def test_bcrypt_rounds_from_env(self, monkeypatch):
        monkeypatch.setenv("KT_AUTH_BCRYPT_ROUNDS", "10")
        cfg = load_auth_config()
        assert cfg.bcrypt_rounds == 10


class TestCapabilitiesProjection:
    def test_all_off_shape(self):
        cfg = AuthConfig()
        caps = cfg.as_capabilities_dict()
        assert caps == {
            "host_token": {"enabled": False, "loopback_bypass": True},
            "admin_token": {"enabled": False},
            "multi_user": {
                "enabled": False,
                "mode": "off",
                "registration": "admin_only",
            },
        }

    def test_partial_on_shape(self):
        cfg = AuthConfig(
            host_token="x",
            multi_user="required",
            registration="invite_only",
            loopback_bypass=False,
        )
        caps = cfg.as_capabilities_dict()
        assert caps["host_token"] == {"enabled": True, "loopback_bypass": False}
        assert caps["admin_token"] == {"enabled": False}
        assert caps["multi_user"] == {
            "enabled": True,
            "mode": "required",
            "registration": "invite_only",
        }

    def test_capabilities_dict_carries_no_secrets(self):
        cfg = AuthConfig(host_token="super-secret-do-not-leak", admin_token="also")
        caps = cfg.as_capabilities_dict()
        flat = repr(caps)
        # Tokens themselves must NEVER appear in the projection.
        assert "super-secret-do-not-leak" not in flat
        assert "also" not in flat


class TestFrozen:
    def test_config_is_frozen(self):
        cfg = AuthConfig()
        with pytest.raises(Exception):  # FrozenInstanceError
            cfg.host_token = "modified"  # type: ignore[misc]
