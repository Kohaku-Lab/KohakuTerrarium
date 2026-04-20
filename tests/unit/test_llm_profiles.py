"""Tests for LLM profile system."""

from unittest.mock import patch

import pytest

from kohakuterrarium.llm.profile_types import LLMPreset
from kohakuterrarium.llm.profiles import (
    ALIASES,
    PRESETS,
    LLMProfile,
    apply_variation_groups,
    delete_profile,
    get_default_model,
    get_preset,
    get_profile,
    list_all,
    load_profiles,
    normalize_variation_selections,
    parse_variation_selector,
    resolve_controller_llm,
    save_profile,
    set_default_model,
)


@pytest.fixture(autouse=True)
def tmp_profiles(tmp_path):
    """Use a temp file for profiles instead of ~/.kohakuterrarium/.

    ``autouse=True`` so no test can accidentally write to the user's real
    ``~/.kohakuterrarium/llm_profiles.yaml`` — an earlier version of this
    fixture was opt-in and silently polluted the developer's home dir when
    a test read state without naming the fixture.

    The ``PROFILES_PATH`` symbol is re-exported from :mod:`profiles` for
    back-compat, but the actual file I/O happens in :mod:`backends` — that's
    the module we need to patch so ``load_yaml_store`` sees the temp file.
    """
    profiles_path = tmp_path / "llm_profiles.yaml"
    with (
        patch("kohakuterrarium.llm.backends.PROFILES_PATH", profiles_path),
        patch("kohakuterrarium.llm.profiles.PROFILES_PATH", profiles_path),
    ):
        yield profiles_path


class TestPresets:
    def test_presets_not_empty(self):
        assert len(PRESETS) > 0

    def test_all_presets_have_required_fields(self):
        for name, data in PRESETS.items():
            assert "provider" in data, f"{name} missing provider"
            assert "model" in data, f"{name} missing model"
            assert "max_context" in data, f"{name} missing max_context"

    def test_aliases_point_to_valid_presets(self):
        for alias, target in ALIASES.items():
            assert (
                target in PRESETS
            ), f"alias '{alias}' points to missing preset '{target}'"

    def test_get_preset_by_name(self):
        p = get_preset("gpt-5.4")
        assert p is not None
        assert p.model == "gpt-5.4"
        assert p.max_context > 0

    def test_get_preset_by_alias(self):
        p = get_preset("gemini")
        assert p is not None
        assert "gemini" in p.model

    def test_get_preset_nonexistent(self):
        assert get_preset("nonexistent-model-xyz") is None


class TestLLMProfile:
    def test_from_dict(self):
        p = LLMProfile.from_dict(
            "test",
            {
                "provider": "openai",
                "model": "test-model",
                "max_context": 100000,
                "selected_variations": {"reasoning": "low"},
            },
        )
        assert p.name == "test"
        assert p.model == "test-model"
        assert p.max_context == 100000
        assert p.selected_variations == {"reasoning": "low"}

    def test_to_dict(self):
        p = LLMProfile(
            name="test",
            provider="openai",
            model="test-model",
            max_context=100000,
            base_url="https://example.com",
            selected_variations={"reasoning": "high"},
        )
        d = p.to_dict()
        assert d["provider"] == "openai"
        assert d["model"] == "test-model"
        assert d["base_url"] == "https://example.com"
        assert d["selected_variations"] == {"reasoning": "high"}
        assert "name" not in d

    def test_defaults(self):
        p = LLMProfile(name="test", provider="openai", model="m")
        assert p.max_context == 256000
        assert p.max_output == 65536
        assert p.temperature is None
        assert p.extra_body == {}


class TestPresetSerialization:
    def test_variation_groups_round_trip(self):
        preset = LLMPreset(
            name="custom",
            provider="openai",
            model="gpt-test",
            variation_groups={
                "reasoning": {
                    "low": {"extra_body.reasoning.effort": "low"},
                    "high": {"extra_body.reasoning.effort": "high"},
                }
            },
        )
        data = preset.to_dict()
        restored = LLMPreset.from_dict("custom", data)
        assert restored.variation_groups == preset.variation_groups

    def test_save_and_load_variation_groups(self, tmp_profiles):
        save_profile(
            LLMPreset(
                name="user-variant",
                provider="openai",
                model="gpt-test",
                variation_groups={
                    "mode": {
                        "fast": {"temperature": 0.1},
                    }
                },
            )
        )
        loaded = get_profile("user-variant@mode=fast")
        assert loaded is not None
        assert loaded.temperature == 0.1
        assert loaded.selected_variations == {"mode": "fast"}

    def test_save_profile_preserves_variation_groups(self, tmp_profiles):
        """Updating a preset via LLMProfile (no variation_groups field) must
        preserve the groups already on the saved preset — otherwise
        API round-trips silently wipe the variation set."""
        save_profile(
            LLMPreset(
                name="user-variant",
                provider="openai",
                model="gpt-test",
                variation_groups={
                    "mode": {"fast": {"temperature": 0.1}},
                },
            )
        )

        updated = LLMProfile(
            name="user-variant",
            provider="openai",
            model="gpt-test-v2",
            max_context=512000,
        )
        save_profile(updated)

        from kohakuterrarium.llm.profiles import load_presets

        stored = load_presets()["user-variant"]
        assert stored.model == "gpt-test-v2"
        assert stored.max_context == 512000
        assert stored.variation_groups == {
            "mode": {"fast": {"temperature": 0.1}},
        }


class TestProfileStorage:
    def test_save_and_load(self, tmp_profiles):
        profile = LLMProfile(
            name="myprofile",
            provider="openai",
            model="custom-model",
            max_context=50000,
        )
        save_profile(profile)

        profiles = load_profiles()
        assert "myprofile" in profiles
        assert profiles["myprofile"].model == "custom-model"

    def test_delete_profile(self, tmp_profiles):
        profile = LLMProfile(name="todel", provider="openai", model="m")
        save_profile(profile)
        assert delete_profile("todel") is True
        assert delete_profile("todel") is False

    def test_default_model(self, tmp_profiles):
        with patch("kohakuterrarium.llm.profiles._is_available", return_value=False):
            assert get_default_model() == ""
        set_default_model("gpt-5.4")
        assert get_default_model() == "gpt-5.4"

    def test_load_empty(self, tmp_profiles):
        profiles = load_profiles()
        assert profiles == {}

    def test_load_corrupt_file(self, tmp_profiles):
        tmp_profiles.write_text("not: valid: yaml: [[[")
        profiles = load_profiles()
        assert profiles == {}


class TestVariationHelpers:
    def test_parse_variation_selector(self):
        name, selections = parse_variation_selector(
            "claude-opus-4.6-direct@speed=fast,reasoning=low"
        )
        assert name == "claude-opus-4.6-direct"
        assert selections == {"speed": "fast", "reasoning": "low"}

    def test_parse_variation_selector_shorthand(self):
        name, selections = parse_variation_selector("claude-opus-4.6-direct@fast")
        assert name == "claude-opus-4.6-direct"
        assert selections == {"__option__": "fast"}

    def test_normalize_unknown_group(self):
        preset = LLMPreset(
            name="t",
            provider="openai",
            model="m",
            variation_groups={"reasoning": {"low": {}}},
        )
        with pytest.raises(ValueError, match="Unknown variation group"):
            normalize_variation_selections({"speed": "fast"}, preset)

    def test_normalize_unknown_option(self):
        preset = LLMPreset(
            name="t",
            provider="openai",
            model="m",
            variation_groups={"reasoning": {"low": {}}},
        )
        with pytest.raises(ValueError, match="Unknown variation option"):
            normalize_variation_selections({"reasoning": "high"}, preset)

    def test_normalize_ambiguous_shorthand(self):
        preset = LLMPreset(
            name="t",
            provider="openai",
            model="m",
            variation_groups={"a": {"fast": {}}, "b": {"fast": {}}},
        )
        with pytest.raises(ValueError, match="Ambiguous variation option"):
            normalize_variation_selections({"__option__": "fast"}, preset)

    def test_apply_variation_groups_collision(self):
        with pytest.raises(ValueError, match="conflict"):
            apply_variation_groups(
                {"extra_body": {}},
                {
                    "a": {"x": {"extra_body.reasoning.effort": "low"}},
                    "b": {"y": {"extra_body.reasoning.effort": "high"}},
                },
                {"a": "x", "b": "y"},
            )


class TestResolution:
    def test_resolve_from_config_llm(self, tmp_profiles):
        profile = resolve_controller_llm({"llm": "gpt-5.4"})
        assert profile is not None
        assert profile.model == "gpt-5.4"

    def test_resolve_from_alias(self, tmp_profiles):
        profile = resolve_controller_llm({"llm": "gemini"})
        assert profile is not None
        assert "gemini" in profile.model

    def test_resolve_from_default(self, tmp_profiles):
        set_default_model("gpt-4o")
        profile = resolve_controller_llm({})
        assert profile is not None
        assert profile.model == "gpt-4o"

    def test_resolve_cli_override(self, tmp_profiles):
        profile = resolve_controller_llm({"llm": "gpt-5.4"}, llm_override="gemini")
        assert profile is not None
        assert "gemini" in profile.model

    def test_resolve_inline_overrides(self, tmp_profiles):
        profile = resolve_controller_llm(
            {"llm": "gpt-5.4", "temperature": 0.3, "reasoning_effort": "xhigh"}
        )
        assert profile is not None
        assert profile.temperature == 0.3
        assert profile.reasoning_effort == "xhigh"

    def test_resolve_grouped_variations_from_selector(self, tmp_profiles):
        # ``claude-opus-4.6-direct`` is a back-compat alias for
        # ``claude-opus-4.6``. Anthropic's adaptive-thinking API puts effort
        # under ``output_config.effort`` (not nested in ``thinking``).
        # Fast mode was removed — it's not surfaced by Anthropic's
        # OpenAI-compat endpoint, use the ``-or`` variant for that instead.
        profile = resolve_controller_llm(
            {"llm": "claude-opus-4.6-direct@reasoning=low"}
        )
        assert profile is not None
        assert profile.selected_variations == {"reasoning": "low"}
        assert profile.extra_body["output_config"]["effort"] == "low"

    def test_resolve_grouped_variations_from_model_provider_and_config(
        self, tmp_profiles
    ):
        profile = resolve_controller_llm(
            {
                "model": "gpt-5.4",
                "provider": "openai",
                "variation_selections": {"reasoning": "low"},
            }
        )
        assert profile is not None
        assert profile.provider == "openai"
        assert profile.extra_body["reasoning"]["effort"] == "low"
        assert profile.selected_variations == {"reasoning": "low"}

    def test_resolve_extra_body_merge_after_variation(self, tmp_profiles):
        # Inline ``extra_body`` should deep-merge on top of the variation-resolved
        # extra_body. Here the variation sets ``output_config.effort=low`` and
        # the inline override raises it to ``max`` while adding ``budget``.
        profile = resolve_controller_llm(
            {
                "llm": "claude-opus-4.6-direct@reasoning=low",
                "extra_body": {"output_config": {"effort": "max", "budget": 2048}},
            }
        )
        assert profile is not None
        assert profile.extra_body["output_config"]["effort"] == "max"
        assert profile.extra_body["output_config"]["budget"] == 2048

    def test_resolve_controller_llm_extra_body_deep_merges_nested_objects(
        self, tmp_profiles
    ):
        profile = resolve_controller_llm(
            {
                "llm": "gpt-5.4-direct@reasoning=low",
                "extra_body": {"reasoning": {"summary": "auto"}},
            }
        )
        assert profile is not None
        assert profile.extra_body["reasoning"]["effort"] == "low"
        assert profile.extra_body["reasoning"]["summary"] == "auto"

    def test_resolve_user_profile_over_preset(self, tmp_profiles):
        custom = LLMProfile(
            name="gpt-5.4",
            provider="openai",
            model="custom-gpt-5.4",
            max_context=999999,
        )
        save_profile(custom)
        profile = resolve_controller_llm({"llm": "gpt-5.4"})
        assert profile is not None
        assert profile.model == "custom-gpt-5.4"

    def test_resolve_no_profile_returns_none(self, tmp_profiles):
        with patch("kohakuterrarium.llm.profiles._is_available", return_value=False):
            profile = resolve_controller_llm({})
            assert profile is None

    def test_resolve_unknown_returns_none(self, tmp_profiles):
        profile = resolve_controller_llm({"llm": "nonexistent-xyz"})
        assert profile is None

    def test_resolve_ambiguous_model_lookup(self, tmp_profiles):
        with pytest.raises(ValueError, match="ambiguous"):
            resolve_controller_llm({"model": "gpt-5.4"})

    def test_selector_unknown_group_is_explicit(self, tmp_profiles):
        with pytest.raises(ValueError, match="Unknown variation group"):
            resolve_controller_llm({"llm": "gpt-5.4-direct@speed=fast"})

    def test_selector_unknown_option_is_explicit(self, tmp_profiles):
        with pytest.raises(ValueError, match="Unknown variation option"):
            resolve_controller_llm({"llm": "gpt-5.4-direct@reasoning=extreme"})


class TestListAll:
    def test_includes_presets(self, tmp_profiles):
        entries = list_all()
        names = [e["name"] for e in entries]
        assert "gpt-5.4" in names
        assert "gemini-3.1-pro" in names

    def test_includes_user_profiles(self, tmp_profiles):
        save_profile(LLMProfile(name="custom", provider="openai", model="m"))
        entries = list_all()
        names = [e["name"] for e in entries]
        assert "custom" in names

    def test_marks_default(self, tmp_profiles):
        set_default_model("gpt-5.4")
        entries = list_all()
        defaults = [e for e in entries if e.get("is_default")]
        assert len(defaults) >= 1
        assert defaults[0]["name"] == "gpt-5.4"

    def test_exposes_variation_metadata(self, tmp_profiles):
        # claude-opus-4.6 (Anthropic direct) is the primary Opus 4.6 preset
        # since the 2026-04 naming cleanup; ``claude-opus-4.6-direct`` is now
        # a back-compat alias pointing at this entry. Only the ``reasoning``
        # group is kept on direct presets — fast mode requires the native API
        # and isn't reachable through Anthropic's OpenAI-compat endpoint.
        entry = next(e for e in list_all() if e["name"] == "claude-opus-4.6")
        assert "reasoning" in entry["variation_groups"]
        assert entry["selected_variations"] == {}
