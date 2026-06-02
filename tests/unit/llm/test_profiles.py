"""Unit tests for ``llm/profiles.py`` — preset/backend resolution + CRUD.

Behavior-first: assert exact resolution results (model, provider,
backend_type, base_url, extra_body), override merging in
``resolve_controller_llm``, alias/ambiguity handling, default-model
upgrade, availability checks, and the backend/preset CRUD round-trips.

All file I/O is redirected to a per-test tmp dir; ``CodexTokens.load``
and the package scanner are stubbed so resolution stays deterministic.
"""

import pytest

from kohakuterrarium.llm import api_keys as ak
from kohakuterrarium.llm import presets as presets_mod
from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.llm.profile_types import LLMBackend, LLMPreset
from kohakuterrarium.llm.profiles import (
    _find_profile_by_model,
    _is_available,
    _legacy_model_provider_hint,
    _split_provider_prefix,
    _upgrade_bare_default,
    delete_backend,
    delete_profile,
    get_default_model,
    get_profile,
    list_all,
    load_profiles,
    profile_to_identifier,
    resolve_controller_llm,
    save_backend,
    save_profile,
    set_default_model,
)


@pytest.fixture(autouse=True)
def isolated_llm_store(tmp_path, monkeypatch):
    """Redirect every LLM-config file to tmp; reset preset caches.

    Also stubs ``CodexTokens.load`` (no codex creds by default) and the
    package scanner (no package presets) so resolution is deterministic.
    """
    # Isolate every LLM-config file under one tmp config dir — both
    # ``llm_profiles.yaml`` and ``api_keys.yaml`` resolve through
    # ``config_dir()`` / ``KT_CONFIG_DIR``.
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    profiles_path = tmp_path / "llm_profiles.yaml"
    keys_path = tmp_path / "api_keys.yaml"

    presets_mod._all_presets_cache = None
    presets_mod._package_presets_merged = False
    monkeypatch.setattr(presets_mod, "list_packages", lambda: [])

    monkeypatch.setattr(CodexTokens, "load", classmethod(lambda cls, path=None: None))

    # clear provider env vars so availability checks are deterministic
    for env in ak.PROVIDER_KEY_MAP.values():
        monkeypatch.delenv(env, raising=False)
    ak.clear_api_key_resolver()

    yield {"profiles_path": profiles_path, "keys_path": keys_path}

    presets_mod._all_presets_cache = None
    presets_mod._package_presets_merged = False


# ---------------------------------------------------------------------------
# _split_provider_prefix
# ---------------------------------------------------------------------------


class TestSplitProviderPrefix:
    def test_no_slash_returns_empty_provider(self):
        assert _split_provider_prefix("gpt-5.4") == ("", "gpt-5.4")

    def test_provider_slash_name(self):
        assert _split_provider_prefix("codex/gpt-5.4") == ("codex", "gpt-5.4")

    def test_only_first_slash_splits(self):
        assert _split_provider_prefix("openrouter/openai/gpt-5.4") == (
            "openrouter",
            "openai/gpt-5.4",
        )

    def test_empty_provider_half_raises(self):
        with pytest.raises(ValueError, match="both halves must be non-empty"):
            _split_provider_prefix("/gpt-5.4")

    def test_empty_name_half_raises(self):
        with pytest.raises(ValueError, match="both halves must be non-empty"):
            _split_provider_prefix("codex/")


# ---------------------------------------------------------------------------
# get_profile — resolution against built-in presets
# ---------------------------------------------------------------------------


class TestGetProfile:
    def test_qualified_name_resolves_builtin(self):
        profile = get_profile("codex/gpt-5.4")
        assert profile is not None
        assert profile.name == "gpt-5.4"
        assert profile.model == "gpt-5.4"
        assert profile.provider == "codex"
        assert profile.backend_type == "codex"
        assert profile.max_context == 400000

    def test_provider_arg_disambiguates(self):
        profile = get_profile("gpt-5.4", provider="openrouter")
        assert profile is not None
        assert profile.provider == "openrouter"
        # OR variant has the prefixed model id
        assert profile.model == "openai/gpt-5.4"
        assert profile.base_url == "https://openrouter.ai/api/v1"

    def test_ambiguous_bare_name_raises(self):
        # 'gpt-5.4' exists under codex, openai, openrouter
        with pytest.raises(ValueError, match="exists under multiple providers"):
            get_profile("gpt-5.4")

    def test_alias_resolves(self):
        # 'opus' alias -> (anthropic, claude-opus-4.7)
        profile = get_profile("opus")
        assert profile is not None
        assert profile.provider == "anthropic"
        assert profile.model == "claude-opus-4-7"

    def test_unknown_name_returns_none(self):
        assert get_profile("totally-made-up-model") is None

    def test_unknown_qualified_name_returns_none(self):
        assert get_profile("codex/no-such-model") is None

    def test_resolved_profile_carries_backend_url_and_key_env(self):
        profile = get_profile("openai/gpt-5.4")
        assert profile.base_url == "https://api.openai.com/v1"
        assert profile.api_key_env == "OPENAI_API_KEY"
        assert profile.backend_type == "openai"

    def test_kimi_code_direct_profile_resolves(self):
        profile = get_profile("kimi-code/kimi-for-coding")
        assert profile is not None
        assert profile.provider == "kimi-code"
        assert profile.backend_type == "anthropic"
        assert profile.model == "kimi-for-coding"
        assert profile.base_url == "https://api.kimi.com/coding/"
        assert profile.api_key_env == "KIMI_CODE_API_KEY"

    def test_glm_coding_direct_profile_resolves_with_bearer_auth(self):
        profile = get_profile("glm-coding/glm-5.1")
        assert profile is not None
        assert profile.provider == "glm-coding"
        assert profile.backend_type == "anthropic"
        assert profile.model == "GLM-5.1"
        assert profile.base_url == "https://open.bigmodel.cn/api/anthropic"
        assert profile.api_key_env == "GLM_CODING_API_KEY"
        assert profile.extra_body["auth_as_bearer"] is True


# ---------------------------------------------------------------------------
# Variation selectors
# ---------------------------------------------------------------------------


class TestVariationSelectors:
    def test_selector_applies_variation_patch(self):
        # claude-opus-4.7 reasoning group: 'low' -> extra_body.output_config.effort
        profile = get_profile("anthropic/claude-opus-4.7@reasoning=low")
        assert profile is not None
        assert profile.extra_body["output_config"]["effort"] == "low"
        assert profile.selected_variations == {"reasoning": "low"}

    def test_selector_default_extra_body_preserved_when_unselected(self):
        profile = get_profile("anthropic/claude-opus-4.7")
        # the preset's own default extra_body
        assert profile.extra_body["output_config"]["effort"] == "xhigh"
        assert profile.selected_variations == {}

    def test_unknown_variation_option_raises(self):
        with pytest.raises(ValueError, match="Unknown variation option"):
            get_profile("anthropic/claude-opus-4.7@reasoning=bogus")

    def test_unknown_variation_group_raises(self):
        with pytest.raises(ValueError, match="Unknown variation group"):
            get_profile("anthropic/claude-opus-4.7@nogroup=low")


# ---------------------------------------------------------------------------
# profile_to_identifier
# ---------------------------------------------------------------------------


class TestProfileToIdentifier:
    def test_basic_identifier(self):
        profile = get_profile("codex/gpt-5.4")
        assert profile_to_identifier(profile) == "codex/gpt-5.4"

    def test_identifier_with_variations(self):
        profile = get_profile("anthropic/claude-opus-4.7@reasoning=high")
        assert (
            profile_to_identifier(profile) == "anthropic/claude-opus-4.7@reasoning=high"
        )

    def test_none_profile_returns_empty(self):
        assert profile_to_identifier(None) == ""

    def test_round_trips_through_get_profile(self):
        original = get_profile("anthropic/claude-opus-4.7@reasoning=medium")
        ident = profile_to_identifier(original)
        reparsed = get_profile(ident)
        assert reparsed.selected_variations == {"reasoning": "medium"}
        assert reparsed.extra_body["output_config"]["effort"] == "medium"


# ---------------------------------------------------------------------------
# resolve_controller_llm — override merging
# ---------------------------------------------------------------------------


class TestResolveControllerLlm:
    def test_resolves_by_llm_field(self):
        profile = resolve_controller_llm({"llm": "codex/gpt-5.4"})
        assert profile is not None
        assert profile.model == "gpt-5.4"

    def test_llm_override_arg_wins_over_config(self):
        profile = resolve_controller_llm(
            {"llm": "codex/gpt-5.4"}, llm_override="openai/gpt-4o"
        )
        assert profile.provider == "openai"
        assert profile.model == "gpt-4o"

    def test_provider_field_disambiguates_bare_llm(self):
        profile = resolve_controller_llm({"llm": "gpt-5.4", "provider": "openrouter"})
        assert profile.provider == "openrouter"

    def test_temperature_override_applied(self):
        profile = resolve_controller_llm({"llm": "codex/gpt-5.4", "temperature": 0.3})
        assert profile.temperature == 0.3

    def test_max_tokens_override_maps_to_max_output(self):
        profile = resolve_controller_llm({"llm": "codex/gpt-5.4", "max_tokens": 4096})
        assert profile.max_output == 4096

    def test_none_override_value_ignored(self):
        baseline = resolve_controller_llm({"llm": "codex/gpt-5.4"})
        profile = resolve_controller_llm({"llm": "codex/gpt-5.4", "temperature": None})
        assert profile.temperature == baseline.temperature

    def test_extra_body_override_deep_merged(self):
        profile = resolve_controller_llm(
            {
                "llm": "anthropic/claude-opus-4.7",
                "extra_body": {"output_config": {"max_tokens": 999}},
            }
        )
        # merged: preset's effort key preserved + new key added
        assert profile.extra_body["output_config"]["effort"] == "xhigh"
        assert profile.extra_body["output_config"]["max_tokens"] == 999

    def test_variation_selections_override(self):
        profile = resolve_controller_llm(
            {
                "llm": "anthropic/claude-opus-4.7",
                "variation_selections": {"reasoning": "high"},
            }
        )
        assert profile.selected_variations == {"reasoning": "high"}

    def test_legacy_variation_shorthand_applied(self):
        profile = resolve_controller_llm(
            {"llm": "anthropic/claude-opus-4.7", "variation": "low"}
        )
        assert profile.selected_variations == {"reasoning": "low"}

    def test_resolve_by_raw_model_field(self):
        # 'claude-opus-4-7' is the API model id of the anthropic preset
        profile = resolve_controller_llm({"model": "claude-opus-4-7"})
        assert profile is not None
        assert profile.provider == "anthropic"

    def test_legacy_codex_auth_mode_hint(self):
        # raw model id shared across providers, disambiguated by auth_mode
        profile = resolve_controller_llm(
            {"model": "gpt-5.4", "auth_mode": "codex-oauth"}
        )
        assert profile is not None
        assert profile.provider == "codex"

    def test_unknown_llm_returns_none(self):
        assert resolve_controller_llm({"llm": "nonexistent-xyz"}) is None

    def test_empty_config_with_no_default_returns_none(self):
        # no codex creds, no api keys, no explicit default -> nothing available
        assert resolve_controller_llm({}) is None

    def test_empty_config_uses_explicit_default_model(self):
        set_default_model("anthropic/claude-opus-4.7")
        profile = resolve_controller_llm({})
        assert profile is not None
        assert profile.provider == "anthropic"
        assert profile.name == "claude-opus-4.7"

    def test_retry_policy_override_deep_copied(self):
        policy = {"max_attempts": 5, "backoff": [1, 2]}
        profile = resolve_controller_llm(
            {"llm": "codex/gpt-5.4", "retry_policy": policy}
        )
        assert profile.retry_policy == policy
        # deep-copied, not aliased
        assert profile.retry_policy is not policy
        policy["max_attempts"] = 99
        assert profile.retry_policy["max_attempts"] == 5

    def test_reasoning_effort_override_applied(self):
        profile = resolve_controller_llm(
            {"llm": "codex/gpt-5.4", "reasoning_effort": "low"}
        )
        assert profile.reasoning_effort == "low"


class TestLegacyModelProviderHint:
    def test_codex_oauth_auth_mode(self):
        assert _legacy_model_provider_hint({"auth_mode": "codex-oauth"}) == "codex"

    def test_other_auth_mode_empty(self):
        assert _legacy_model_provider_hint({"auth_mode": "api-key"}) == ""

    def test_missing_auth_mode_empty(self):
        assert _legacy_model_provider_hint({}) == ""


# ---------------------------------------------------------------------------
# _find_profile_by_model
# ---------------------------------------------------------------------------


class TestFindProfileByModel:
    def test_unique_model_resolves(self):
        # 'claude-opus-4-7' is unique to the anthropic preset
        profile = _find_profile_by_model("claude-opus-4-7")
        assert profile is not None
        assert profile.provider == "anthropic"

    def test_unknown_model_returns_none(self):
        assert _find_profile_by_model("no-such-model-id") is None

    def test_provider_filter_narrows_match(self):
        profile = _find_profile_by_model("gpt-4o", provider="openai")
        assert profile.provider == "openai"

    def test_ambiguous_model_uses_preference_order(self):
        # 'gpt-5.4' model id appears under openai + openrouter; preference
        # order has codex first but codex's model id is 'gpt-5.4' too —
        # so the first preferred provider that has it wins, deterministically.
        profile = _find_profile_by_model("gpt-5.4")
        assert profile is not None
        # codex is first in _LEGACY_MODEL_PROVIDER_PREFERENCE and has it
        assert profile.provider == "codex"


# ---------------------------------------------------------------------------
# Default model
# ---------------------------------------------------------------------------


class TestDefaultModel:
    def test_no_default_no_creds_returns_empty(self):
        assert get_default_model() == ""

    def test_explicit_qualified_default_returned_verbatim(self):
        set_default_model("openrouter/mimo-v2-pro")
        assert get_default_model() == "openrouter/mimo-v2-pro"

    def test_explicit_bare_default_upgraded_to_qualified(self):
        # legacy bare default written by old builds. 'claude-opus-4.7'
        # exists under both anthropic and openrouter; _upgrade_bare_default
        # picks the first provider in _PROVIDER_DEFAULT_MODELS order that
        # has it — openrouter precedes anthropic in that list.
        set_default_model("claude-opus-4.7")
        assert get_default_model() == "openrouter/claude-opus-4.7"

    def test_default_picks_first_available_provider(self, monkeypatch):
        # make openrouter available via api key
        from kohakuterrarium.llm.api_keys import save_api_key

        save_api_key("openrouter", "sk-or-key")
        # codex unavailable (no tokens), openrouter is first available
        assert get_default_model() == "openrouter/mimo-v2-pro"

    def test_default_picks_kimi_code_when_only_kimi_key_available(self):
        from kohakuterrarium.llm.api_keys import save_api_key

        save_api_key("kimi-code", "sk-kimi")
        assert get_default_model() == "kimi-code/kimi-for-coding"

    def test_default_picks_glm_coding_when_only_glm_key_available(self):
        from kohakuterrarium.llm.api_keys import save_api_key

        save_api_key("glm-coding", "glm-key")
        assert get_default_model() == "glm-coding/glm-5.1"

    def test_upgrade_bare_default_via_alias(self):
        # 'gpt-5.4-or' is an alias -> (openrouter, gpt-5.4)
        assert _upgrade_bare_default("gpt-5.4-or") == "openrouter/gpt-5.4"

    def test_upgrade_bare_default_unknown_returns_empty(self):
        assert _upgrade_bare_default("not-a-model") == ""

    def test_upgrade_bare_default_prefers_provider_order(self):
        # bare 'gpt-5.4' exists under codex/openai/openrouter; codex first
        assert _upgrade_bare_default("gpt-5.4") == "codex/gpt-5.4"


# ---------------------------------------------------------------------------
# _is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_empty_provider_not_available(self):
        assert _is_available("") is False

    def test_codex_unavailable_without_tokens(self):
        # CodexTokens.load stubbed to None
        assert _is_available("codex") is False

    def test_codex_available_with_tokens(self, monkeypatch):
        monkeypatch.setattr(
            CodexTokens, "load", classmethod(lambda cls, path=None: object())
        )
        assert _is_available("codex") is True

    def test_provider_available_with_stored_key(self):
        from kohakuterrarium.llm.api_keys import save_api_key

        save_api_key("openai", "sk-openai")
        assert _is_available("openai") is True

    def test_provider_available_via_api_key_env(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
        assert _is_available("openrouter") is True

    def test_new_coding_providers_available_via_keys(self, monkeypatch):
        from kohakuterrarium.llm.api_keys import save_api_key

        save_api_key("kimi-code", "sk-kimi")
        monkeypatch.setenv("GLM_CODING_API_KEY", "glm-env-key")
        assert _is_available("kimi-code") is True
        assert _is_available("glm-coding") is True

    def test_provider_unavailable_without_any_key(self):
        assert _is_available("gemini") is False

    def test_unknown_provider_not_available(self):
        assert _is_available("made-up-provider") is False


# ---------------------------------------------------------------------------
# Backend CRUD
# ---------------------------------------------------------------------------


class TestBackendCrud:
    def test_save_then_load_custom_backend(self):
        backend = LLMBackend(
            name="my-proxy",
            backend_type="openai",
            base_url="http://proxy/v1",
            api_key_env="PROXY_KEY",
        )
        save_backend(backend)
        from kohakuterrarium.llm.backends import load_backends

        backends = load_backends()
        assert "my-proxy" in backends
        assert backends["my-proxy"].base_url == "http://proxy/v1"
        assert backends["my-proxy"].backend_type == "openai"

    def test_save_backend_normalizes_legacy_type(self):
        save_backend(LLMBackend(name="legacy", backend_type="codex-oauth"))
        from kohakuterrarium.llm.backends import load_backends

        assert load_backends()["legacy"].backend_type == "codex"

    def test_save_backend_rejects_unknown_type(self):
        with pytest.raises(ValueError, match="Unsupported backend_type"):
            save_backend(LLMBackend(name="bad", backend_type="grpc"))

    def test_delete_builtin_provider_rejected(self):
        with pytest.raises(ValueError, match="Cannot delete built-in provider"):
            delete_backend("codex")

    def test_delete_missing_backend_returns_false(self):
        assert delete_backend("never-existed") is False

    def test_delete_custom_backend_round_trip(self):
        save_backend(LLMBackend(name="temp", backend_type="openai"))
        from kohakuterrarium.llm.backends import load_backends

        assert "temp" in load_backends()
        assert delete_backend("temp") is True
        assert "temp" not in load_backends()

    def test_delete_backend_in_use_by_preset_rejected(self):
        save_backend(LLMBackend(name="used", backend_type="openai"))
        save_profile(LLMPreset(name="p1", model="m", provider="used"))
        with pytest.raises(ValueError, match="still in use"):
            delete_backend("used")


# ---------------------------------------------------------------------------
# Preset CRUD
# ---------------------------------------------------------------------------


class TestPresetCrud:
    def test_save_preset_requires_provider(self):
        with pytest.raises(ValueError, match="Preset provider is required"):
            save_profile(LLMPreset(name="p", model="m", provider=""))

    def test_save_preset_unknown_provider_rejected(self):
        with pytest.raises(ValueError, match="Provider not found: ghost"):
            save_profile(LLMPreset(name="p", model="m", provider="ghost"))

    def test_save_preset_then_resolvable(self):
        # 'openai' is a built-in provider
        save_profile(
            LLMPreset(name="my-gpt", model="gpt-x", provider="openai", max_context=123)
        )
        profile = get_profile("openai/my-gpt")
        assert profile is not None
        assert profile.model == "gpt-x"
        assert profile.max_context == 123

    def test_user_preset_overrides_builtin_same_key(self):
        # override the built-in (codex, gpt-5.4) with a custom model id
        save_profile(LLMPreset(name="gpt-5.4", model="custom-model", provider="codex"))
        profile = get_profile("codex/gpt-5.4")
        assert profile.model == "custom-model"

    def test_delete_preset_by_provider(self):
        save_profile(LLMPreset(name="tmp", model="m", provider="openai"))
        assert get_profile("openai/tmp") is not None
        assert delete_profile("tmp", provider="openai") is True
        assert get_profile("openai/tmp") is None

    def test_delete_missing_preset_returns_false(self):
        assert delete_profile("nope", provider="openai") is False

    def test_delete_bare_name_ambiguous_returns_false(self):
        save_profile(LLMPreset(name="dup", model="m1", provider="openai"))
        save_profile(LLMPreset(name="dup", model="m2", provider="openrouter"))
        # bare name under two providers -> refuse to delete
        assert delete_profile("dup") is False
        # both still present
        assert get_profile("openai/dup") is not None
        assert get_profile("openrouter/dup") is not None

    def test_delete_bare_name_unique_succeeds(self):
        save_profile(LLMPreset(name="solo", model="m", provider="openai"))
        assert delete_profile("solo") is True


# ---------------------------------------------------------------------------
# load_profiles / list_all
# ---------------------------------------------------------------------------


class TestLoadProfilesAndListAll:
    def test_load_profiles_empty_when_no_user_presets(self):
        assert load_profiles() == {}

    def test_load_profiles_resolves_user_preset(self):
        save_profile(LLMPreset(name="up", model="m", provider="openai"))
        profiles = load_profiles()
        assert ("openai", "up") in profiles
        assert profiles[("openai", "up")].model == "m"

    def test_list_all_includes_builtin_presets(self):
        entries = list_all()
        keys = {(e["provider"], e["name"]) for e in entries}
        assert ("codex", "gpt-5.4") in keys
        assert ("anthropic", "claude-opus-4.7") in keys

    def test_list_all_marks_user_source(self):
        save_profile(LLMPreset(name="mine", model="m", provider="openai"))
        entries = list_all()
        mine = [e for e in entries if e["name"] == "mine"]
        assert len(mine) == 1
        assert mine[0]["source"] == "user"

    def test_list_all_builtin_source_label(self):
        entries = list_all()
        codex = [
            e for e in entries if e["provider"] == "codex" and e["name"] == "gpt-5.4"
        ]
        assert codex[0]["source"] == "preset"

    def test_list_all_availability_reflects_keys(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        entries = list_all()
        openai_entry = next(
            e for e in entries if e["provider"] == "openai" and e["name"] == "gpt-5.4"
        )
        assert openai_entry["available"] is True
        gemini_entry = next(e for e in entries if e["provider"] == "gemini")
        assert gemini_entry["available"] is False

    def test_list_all_marks_default(self):
        set_default_model("codex/gpt-5.4")
        entries = list_all()
        defaults = [e for e in entries if e["is_default"]]
        assert len(defaults) == 1
        assert (defaults[0]["provider"], defaults[0]["name"]) == ("codex", "gpt-5.4")

    def test_list_all_user_preset_overrides_builtin_pair(self):
        # a user preset at (codex, gpt-5.4) replaces the builtin entry
        save_profile(LLMPreset(name="gpt-5.4", model="my-custom", provider="codex"))
        entries = list_all()
        codex_54 = [
            e for e in entries if e["provider"] == "codex" and e["name"] == "gpt-5.4"
        ]
        # exactly one entry — the user one wins, builtin not duplicated
        assert len(codex_54) == 1
        assert codex_54[0]["source"] == "user"
        assert codex_54[0]["model"] == "my-custom"

    def test_list_all_default_marking_handles_bare_default(self):
        # a legacy unqualified default falls back to name/model matching
        from kohakuterrarium.llm.backends import save_yaml_store

        save_yaml_store({"version": 3, "default_model": "gpt-4o"})
        entries = list_all()
        # 'gpt-4o' is a bare name shared across providers — every entry
        # with that name is flagged (documented bare-name fallback)
        gpt4o = [e for e in entries if e["is_default"]]
        assert gpt4o
        assert all(e["name"] == "gpt-4o" for e in gpt4o)


# ---------------------------------------------------------------------------
# get_preset + _login_provider_for
# ---------------------------------------------------------------------------


class TestMiscHelpers:
    def test_get_preset_resolves_like_get_profile(self):
        from kohakuterrarium.llm.profiles import get_preset

        a = get_preset("codex/gpt-5.4")
        b = get_profile("codex/gpt-5.4")
        assert a.model == b.model
        assert a.provider == b.provider

    def test_login_provider_for_profile_with_provider(self):
        from kohakuterrarium.llm.profiles import _login_provider_for

        profile = get_profile("anthropic/claude-opus-4.7")
        assert _login_provider_for(profile) == "anthropic"

    def test_login_provider_for_dict_with_provider(self):
        from kohakuterrarium.llm.profiles import _login_provider_for

        assert _login_provider_for({"provider": "openrouter"}) == "openrouter"

    def test_login_provider_for_dict_infers_from_base_url(self):
        from kohakuterrarium.llm.profiles import _login_provider_for

        # no explicit provider -> legacy inference from base_url
        inferred = _login_provider_for({"base_url": "https://openrouter.ai/api/v1"})
        assert inferred == "openrouter"
