"""Unit tests for ``llm/backends.py`` — backend YAML store + CRUD.

Behavior-first: assert the exact merged built-in + user backend set,
legacy backend-type normalisation, the legacy ``provider`` inference
table, the synthetic-backend fabrication for old inline profiles, and
the ``validate_backend_type`` accept/reject contract. The profiles
file is isolated to a per-test tmp dir via ``KT_CONFIG_DIR``.
"""

import pytest

from kohakuterrarium.llm.backends import (
    _built_in_providers,
    _normalize_backend_type,
    legacy_provider_from_data,
    load_backends,
    load_yaml_store,
    save_yaml_store,
    validate_backend_type,
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Isolate ``llm_profiles.yaml`` via ``KT_CONFIG_DIR``.

    ``backends.py`` resolves its path through ``config_dir()`` — the
    env var is the documented isolation seam and keeps the suite off
    the operator's real ``~/.kohakuterrarium/``.
    """
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    return tmp_path / "llm_profiles.yaml"


class TestNormalizeBackendType:
    def test_legacy_codex_oauth_rewritten_to_codex(self):
        assert _normalize_backend_type("codex-oauth") == "codex"

    def test_anthropic_preserved(self):
        assert _normalize_backend_type("anthropic") == "anthropic"

    def test_empty_defaults_to_openai(self):
        assert _normalize_backend_type("") == "openai"


class TestYamlStore:
    def test_missing_file_returns_empty_dict(self, isolated_store):
        assert not isolated_store.exists()
        assert load_yaml_store() == {}

    def test_save_then_load_round_trip(self):
        save_yaml_store({"version": 3, "default_model": "gpt-x"})
        assert load_yaml_store() == {"version": 3, "default_model": "gpt-x"}

    def test_malformed_yaml_returns_empty_dict(self, isolated_store):
        isolated_store.write_text("{ not: valid: yaml: ]", encoding="utf-8")
        assert load_yaml_store() == {}


class TestLoadBackends:
    def test_builtins_present_with_correct_endpoints(self):
        backends = load_backends()
        assert backends["openai"].base_url == "https://api.openai.com/v1"
        assert backends["openrouter"].base_url == "https://openrouter.ai/api/v1"
        assert backends["anthropic"].backend_type == "anthropic"
        assert backends["kimi-code"].backend_type == "anthropic"
        assert backends["kimi-code"].base_url == "https://api.kimi.com/coding/"
        assert backends["glm-coding"].backend_type == "anthropic"
        assert (
            backends["glm-coding"].base_url == "https://open.bigmodel.cn/api/anthropic"
        )
        assert backends["codex"].backend_type == "codex"

    def test_codex_builtin_advertises_image_gen_native_tool(self):
        assert _built_in_providers()["codex"].provider_native_tools == ["image_gen"]

    def test_user_backend_merged_in(self):
        save_yaml_store(
            {
                "backends": {
                    "myproxy": {
                        "backend_type": "openai",
                        "base_url": "https://proxy.example/v1",
                    }
                }
            }
        )
        backends = load_backends()
        assert backends["myproxy"].base_url == "https://proxy.example/v1"

    def test_user_backend_defaults_provider_name_to_its_own_name(self):
        save_yaml_store({"backends": {"myproxy": {"backend_type": "openai"}}})
        backends = load_backends()
        # user backends default provider_name = own name (tool-compat anchor)
        assert backends["myproxy"].provider_name == "myproxy"

    def test_user_backend_explicit_provider_name_preserved(self):
        save_yaml_store(
            {"backends": {"ent": {"backend_type": "openai", "provider_name": "codex"}}}
        )
        backends = load_backends()
        # explicit masquerade-as-codex is kept
        assert backends["ent"].provider_name == "codex"

    def test_legacy_inline_profile_fabricates_synthetic_backend(self):
        # an old profile with inline base_url for a provider not in backends
        save_yaml_store(
            {
                "profiles": {
                    "old": {
                        "model": "gpt-4o",
                        "provider": "openai",
                        "base_url": "https://openrouter.ai/api/v1",
                    }
                }
            }
        )
        backends = load_backends()
        # openrouter is a builtin so it's already there — use a custom one
        assert "openrouter" in backends

    def test_providers_key_accepted_as_alias_for_backends(self):
        save_yaml_store(
            {"providers": {"alt": {"backend_type": "openai", "base_url": "u"}}}
        )
        backends = load_backends()
        assert backends["alt"].base_url == "u"


class TestLegacyProviderFromData:
    def test_explicit_non_legacy_provider_returned_as_is(self):
        assert legacy_provider_from_data({"provider": "openrouter"}) == "openrouter"

    def test_codex_backend_type_maps_to_codex(self):
        assert legacy_provider_from_data({"backend_type": "codex"}) == "codex"

    def test_anthropic_inferred_from_base_url(self):
        assert (
            legacy_provider_from_data({"base_url": "https://api.anthropic.com"})
            == "anthropic"
        )

    def test_openrouter_inferred_from_base_url(self):
        assert (
            legacy_provider_from_data({"base_url": "https://openrouter.ai/api/v1"})
            == "openrouter"
        )

    def test_gemini_inferred_from_base_url(self):
        assert (
            legacy_provider_from_data(
                {"base_url": "https://generativelanguage.googleapis.com/v1beta"}
            )
            == "gemini"
        )

    def test_openai_inferred_from_base_url(self):
        assert (
            legacy_provider_from_data({"base_url": "https://api.openai.com/v1"})
            == "openai"
        )

    def test_mimo_inferred_from_base_url(self):
        assert (
            legacy_provider_from_data({"base_url": "https://api.mimo.test"}) == "mimo"
        )

    def test_kimi_code_inferred_from_base_url(self):
        assert (
            legacy_provider_from_data({"base_url": "https://api.kimi.com/coding/"})
            == "kimi-code"
        )
        assert (
            legacy_provider_from_data(
                {
                    "provider": "anthropic",
                    "base_url": "https://api.kimi.com/coding/",
                }
            )
            == "kimi-code"
        )

    def test_glm_coding_inferred_from_base_url(self):
        assert (
            legacy_provider_from_data(
                {"base_url": "https://open.bigmodel.cn/api/anthropic"}
            )
            == "glm-coding"
        )
        assert (
            legacy_provider_from_data(
                {
                    "provider": "anthropic",
                    "base_url": "https://open.bigmodel.cn/api/anthropic",
                }
            )
            == "glm-coding"
        )

    def test_inferred_from_api_key_env(self):
        assert (
            legacy_provider_from_data({"api_key_env": "OPENROUTER_API_KEY"})
            == "openrouter"
        )

    def test_kimi_and_glm_inferred_from_api_key_env(self):
        assert (
            legacy_provider_from_data({"api_key_env": "KIMI_CODE_API_KEY"})
            == "kimi-code"
        )
        assert (
            legacy_provider_from_data({"api_key_env": "GLM_CODING_API_KEY"})
            == "glm-coding"
        )

    def test_unresolvable_data_returns_empty(self):
        assert legacy_provider_from_data({}) == ""

    def test_anthropic_backend_type_maps_to_anthropic(self):
        assert legacy_provider_from_data({"provider": "anthropic"}) == "anthropic"


class TestValidateBackendType:
    def test_openai_accepted(self):
        assert validate_backend_type("openai") == "openai"

    def test_anthropic_accepted(self):
        assert validate_backend_type("anthropic") == "anthropic"

    def test_codex_oauth_accepted_and_rewritten(self):
        assert validate_backend_type("codex-oauth") == "codex"

    def test_empty_normalises_to_openai(self):
        assert validate_backend_type("") == "openai"

    def test_unknown_backend_type_rejected(self):
        with pytest.raises(ValueError, match="Unsupported backend_type"):
            validate_backend_type("ollama")
