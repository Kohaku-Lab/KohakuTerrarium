"""Unit tests for the studio.identity.* helpers.

Focuses on the wrapper logic + validation; the underlying
``kohakuterrarium.llm.profiles`` operations are mocked.
"""

from pathlib import Path

import pytest

from kohakuterrarium.studio.identity import (
    llm_backends as backends_mod,
    llm_default as default_mod,
    llm_native_tools as native_mod,
    llm_profiles as profiles_mod,
    settings as settings_mod,
)

# ── llm_backends ────────────────────────────────────────────


class TestLlmBackends:
    def test_list_backends(self, monkeypatch):
        from kohakuterrarium.llm.profiles import LLMBackend

        backend = LLMBackend(
            name="openai",
            backend_type="openai",
            base_url="https://api.openai.com",
            api_key_env="OPENAI_API_KEY",
            provider_name="",
            provider_native_tools=("web_search",),
        )
        monkeypatch.setattr(backends_mod, "load_backends", lambda: {"openai": backend})
        monkeypatch.setattr(backends_mod, "get_api_key", lambda n: "secret")
        monkeypatch.setattr(backends_mod, "_is_available", lambda n: True)
        out = backends_mod.list_backends()
        assert out[0]["name"] == "openai"
        assert out[0]["built_in"] is True
        assert out[0]["has_token"] is True

    def test_get_backend(self, monkeypatch):
        monkeypatch.setattr(backends_mod, "load_backends", lambda: {"x": "BACKEND"})
        assert backends_mod.get_backend("x") == "BACKEND"
        assert backends_mod.get_backend("ghost") is None

    def test_save_backend_record_missing_fields(self):
        with pytest.raises(ValueError, match="required"):
            backends_mod.save_backend_record("", "openai")

    def test_save_backend_record_unsupported_type(self):
        with pytest.raises(ValueError, match="Unsupported"):
            backends_mod.save_backend_record("x", "garbage")

    def test_save_backend_record_success(self, monkeypatch):
        captured = []
        monkeypatch.setattr(backends_mod, "save_backend", lambda b: captured.append(b))
        out = backends_mod.save_backend_record(
            "mine", "openai", base_url="https://x", api_key_env="K"
        )
        assert out.name == "mine"
        assert captured

    def test_remove_backend(self, monkeypatch):
        monkeypatch.setattr(backends_mod, "delete_backend", lambda n: True)
        assert backends_mod.remove_backend("x") is True

    def test_is_provider_known_via_backend(self, monkeypatch):
        monkeypatch.setattr(backends_mod, "load_backends", lambda: {"openai": object()})
        assert backends_mod.is_provider_known("openai") is True

    def test_is_provider_known_via_keymap(self, monkeypatch):
        monkeypatch.setattr(backends_mod, "load_backends", lambda: {})
        monkeypatch.setattr(
            backends_mod, "PROVIDER_KEY_MAP", {"openai": "OPENAI_API_KEY"}
        )
        assert backends_mod.is_provider_known("openai") is True

    def test_is_provider_known_no(self, monkeypatch):
        monkeypatch.setattr(backends_mod, "load_backends", lambda: {})
        monkeypatch.setattr(backends_mod, "PROVIDER_KEY_MAP", {})
        assert backends_mod.is_provider_known("ghost") is False


# ── llm_default ─────────────────────────────────────────────


class TestLlmDefault:
    def test_get_default(self, monkeypatch):
        monkeypatch.setattr(default_mod, "get_default_model", lambda: "openai/x")
        assert default_mod.get_default() == "openai/x"

    def test_set_default(self, monkeypatch):
        captured = []
        monkeypatch.setattr(
            default_mod, "set_default_model", lambda i: captured.append(i)
        )
        out = default_mod.set_default("openai/x")
        assert out == "openai/x"
        assert captured == ["openai/x"]

    def test_resolve_and_set_default_value_error(self, monkeypatch):
        def boom(n):
            raise ValueError("ambiguous")

        monkeypatch.setattr(default_mod, "get_profile_for_identifier", boom)
        ident, err = default_mod.resolve_and_set_default("name")
        assert ident == ""
        assert err == "ambiguous"

    def test_resolve_and_set_default_not_found(self, monkeypatch):
        monkeypatch.setattr(default_mod, "get_profile_for_identifier", lambda n: None)
        ident, err = default_mod.resolve_and_set_default("ghost")
        assert ident == ""
        assert "not found" in err

    def test_resolve_and_set_default_success(self, monkeypatch):
        from types import SimpleNamespace

        profile = SimpleNamespace(provider="openai", name="gpt-4")
        monkeypatch.setattr(
            default_mod, "get_profile_for_identifier", lambda n: profile
        )
        captured = []
        monkeypatch.setattr(
            default_mod, "set_default_model", lambda i: captured.append(i)
        )
        ident, err = default_mod.resolve_and_set_default("gpt-4")
        assert ident == "openai/gpt-4"
        assert err is None
        assert captured

    def test_list_all_models_combined(self, monkeypatch):
        monkeypatch.setattr(default_mod, "list_all", lambda: [{"n": "x"}])
        assert default_mod.list_all_models_combined() == [{"n": "x"}]


# ── llm_profiles ────────────────────────────────────────────


class TestLlmProfiles:
    def test_split_identifier_qualified(self):
        assert profiles_mod.split_identifier("openai/x") == ("openai", "x")

    def test_split_identifier_bare(self):
        assert profiles_mod.split_identifier("bare") == ("", "bare")

    def test_get_profile_for_identifier_qualified(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            profiles_mod,
            "get_profile",
            lambda bare, prov: called.append((bare, prov)) or "PROF",
        )
        out = profiles_mod.get_profile_for_identifier("openai/gpt")
        assert out == "PROF"
        assert called == [("gpt", "openai")]

    def test_save_profile_record_missing_fields(self):
        with pytest.raises(ValueError, match="required"):
            profiles_mod.save_profile_record("", "model", "openai")

    def test_save_profile_record_unknown_provider(self, monkeypatch):
        monkeypatch.setattr(profiles_mod, "load_backends", lambda: {})
        with pytest.raises(ValueError, match="Provider not found"):
            profiles_mod.save_profile_record("n", "m", "ghost")

    def test_save_profile_record_success(self, monkeypatch):
        monkeypatch.setattr(profiles_mod, "load_backends", lambda: {"openai": object()})
        captured = []
        monkeypatch.setattr(profiles_mod, "save_profile", lambda p: captured.append(p))
        out = profiles_mod.save_profile_record("n", "m", "openai", temperature=0.5)
        assert out.name == "n"
        assert captured

    def test_remove_profile_delegates(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            profiles_mod,
            "delete_profile",
            lambda n, p="": called.append((n, p)) or True,
        )
        assert profiles_mod.remove_profile("n", "openai") is True

    def test_remove_profile_legacy_delegates(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            profiles_mod,
            "delete_profile",
            lambda n, p="": called.append((n, p)) or False,
        )
        assert profiles_mod.remove_profile_legacy("n") is False

    def test_get_preset_definition_delegates(self, monkeypatch):
        captured = []
        monkeypatch.setattr(
            profiles_mod,
            "_get_preset_definition",
            lambda n, p="": captured.append((n, p)) or "DEF",
        )
        assert profiles_mod.get_preset_definition("n") == "DEF"

    def test_get_default_model_identifier(self, monkeypatch):
        monkeypatch.setattr(profiles_mod, "get_default_model", lambda: "openai/x")
        assert profiles_mod.get_default_model_identifier() == "openai/x"

    def test_set_default_model_identifier(self, monkeypatch):
        captured = []
        monkeypatch.setattr(
            profiles_mod, "set_default_model", lambda i: captured.append(i)
        )
        profiles_mod.set_default_model_identifier("openai/x")
        assert captured == ["openai/x"]

    def test_list_user_profile_keys(self, monkeypatch):
        monkeypatch.setattr(
            profiles_mod,
            "load_profiles",
            lambda: {("openai", "gpt"): "PROF"},
        )
        out = profiles_mod.list_user_profile_keys()
        assert ("openai", "gpt") in out

    def test_list_profiles_payload(self, monkeypatch):
        from types import SimpleNamespace

        # SimpleNamespace is a real collaborator — MagicMock(name=...)
        # would silently NOT set ``.name`` (reserved kwarg).
        profile = SimpleNamespace(
            name="gpt-4",
            model="gpt-4o",
            provider="openai",
            backend_type="openai",
            base_url="https://x",
            api_key_env="K",
            max_context=128000,
            max_output=4096,
            temperature=0.5,
            reasoning_effort="medium",
            service_tier="",
            extra_body={},
            selected_variations={"a": "b"},
        )
        monkeypatch.setattr(
            profiles_mod,
            "load_profiles",
            lambda: {("openai", "gpt-4"): profile},
        )
        monkeypatch.setattr(profiles_mod, "load_presets", lambda: {})
        out = profiles_mod.list_profiles_payload()
        # Every profile field is projected into the payload dict.
        assert out == [
            {
                "name": "gpt-4",
                "model": "gpt-4o",
                "provider": "openai",
                "backend_type": "openai",
                "base_url": "https://x",
                "api_key_env": "K",
                "max_context": 128000,
                "max_output": 4096,
                "temperature": 0.5,
                "reasoning_effort": "medium",
                "service_tier": "",
                "extra_body": {},
                "variation_groups": {},
                "selected_variations": {"a": "b"},
            }
        ]

    def test_list_all_models_passthrough(self, monkeypatch):
        monkeypatch.setattr(profiles_mod, "list_all", lambda: [{"x": 1}])
        assert profiles_mod.list_all_models() == [{"x": 1}]


# ── llm_native_tools ───────────────────────────────────────


class TestLlmNativeTools:
    def test_list_native_tools_passthrough(self, monkeypatch):
        sentinel = [{"name": "web_search", "provider": "openai"}]
        monkeypatch.setattr(native_mod, "list_provider_native_tools", lambda: sentinel)
        # Pure read-side wrapper — returns the catalog result unchanged.
        assert native_mod.list_native_tools() == sentinel


# ── settings ───────────────────────────────────────────────


class TestSettings:
    def test_config_paths(self):
        out = settings_mod.config_paths()
        assert "home" in out
        assert isinstance(out["home"], Path)

    def test_show_paths(self, capsys):
        rc = settings_mod.show_paths()
        assert rc == 0
        cap = capsys.readouterr()
        assert "home" in cap.out

    def test_show_path_none(self, capsys):
        rc = settings_mod.show_path(None)
        assert rc == 0

    def test_show_path_unknown(self, capsys):
        rc = settings_mod.show_path("ghost")
        assert rc == 1

    def test_show_path_known(self, capsys):
        rc = settings_mod.show_path("home")
        assert rc == 0

    def test_edit_config_unknown(self, capsys):
        rc = settings_mod.edit_config("ghost")
        assert rc == 1

    def test_edit_config_no_editor(self, monkeypatch, capsys):
        monkeypatch.delenv("EDITOR", raising=False)
        rc = settings_mod.edit_config("llm_profiles")
        assert rc == 1

    def test_edit_config_with_editor(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EDITOR", "true")
        # Override the config path to a temp location.
        target = tmp_path / "x.yaml"
        monkeypatch.setattr(
            settings_mod,
            "config_paths",
            lambda: {"llm_profiles": target},
        )
        rc = settings_mod.edit_config("llm_profiles")
        # ``EDITOR=true`` exits 0 → edit_config returns that exit code,
        # and the config file is created if it was missing.
        assert rc == 0
        assert target.exists()
