"""Antigravity catalog limits and supported KT variation selections."""

import pytest

from kohakuterrarium.llm.antigravity.presets import DEPRECATED, MODELS, model_settings
from kohakuterrarium.llm.presets.catalog import get_all_presets
from kohakuterrarium.llm.profiles.resolve import get_profile

LEVELS = ["low", "medium", "high"]


@pytest.mark.parametrize(
    "model,context,output,efforts,contexts",
    [
        ("gemini-4-argon", 256000, 65536, LEVELS, ["256k", "512k", "900k"]),
        ("gemini-3.8-flash", 256000, 65536, LEVELS, ["256k", "1048k"]),
        ("claude-opus-5-5", 256000, 128000, LEVELS, ["256k", "840k"]),
        ("claude-sonnet-5-5", 256000, 128000, LEVELS, ["256k", "840k"]),
        ("gpt-oss-120b", 80000, 32768, [], ["80k", "131k"]),
    ],
)
def test_current_agy_limits_and_variations(model, context, output, efforts, contexts):
    preset = get_all_presets()[("google-antigravity", model)]
    assert (preset["max_context"], preset["max_output"]) == (context, output)
    assert preset["reasoning_effort"] == "medium"
    groups = preset["variation_groups"]
    assert list(groups.get("reasoning", {})) == efforts
    assert list(groups["context"]) == contexts
    for effort in efforts:
        profile = get_profile(f"google-antigravity/{model}@reasoning={effort}")
        assert profile.reasoning_effort == effort
        assert profile.model == model
        assert profile.selected_variations == {"reasoning": effort}


def test_catalog_is_exactly_the_agy_agent_list():
    advertised = {
        f"{family}-{effort}"
        for family, spec in MODELS.items()
        for effort in spec.efforts
    }
    assert advertised == {
        *(f"gemini-4-argon-{e}" for e in LEVELS),
        *(f"gemini-3.8-flash-{e}" for e in LEVELS),
        *(f"claude-opus-5-5-{e}" for e in LEVELS),
        *(f"claude-sonnet-5-5-{e}" for e in LEVELS),
        "gpt-oss-120b-medium",
    }
    agy = {
        name for provider, name in get_all_presets() if provider == "google-antigravity"
    }
    assert agy == set(MODELS)


@pytest.mark.parametrize(
    "model,effort,wire,thinking,limit",
    [
        (
            "gemini-4-argon",
            "",
            "gemini-4-argon-medium",
            {"thinkingLevel": "MEDIUM"},
            65536,
        ),
        (
            "gemini-4-argon-high",
            "",
            "gemini-4-argon-high",
            {"thinkingLevel": "HIGH"},
            65536,
        ),
        (
            "gemini-3.8-flash",
            "low",
            "gemini-3.8-flash-low",
            {"thinkingBudget": 1000},
            65536,
        ),
        (
            "gemini-3.8-flash-high",
            "high",
            "gemini-3.8-flash-high",
            {"thinkingBudget": -1},
            65536,
        ),
        (
            "claude-opus-5-5-low",
            "",
            "claude-opus-5-5-low",
            {"thinkingLevel": "LOW"},
            128000,
        ),
        ("gpt-oss-120b", "", "gpt-oss-120b-medium", {"thinkingBudget": 8192}, 32768),
    ],
)
def test_default_and_explicit_sku_settings(model, effort, wire, thinking, limit):
    settings = model_settings(model, effort)
    assert settings.wire_model == wire
    assert settings.thinking_config == {"includeThoughts": True, **thinking}
    assert settings.max_output == limit


def test_custom_model_passes_through_without_thinking():
    settings = model_settings("gemini-custom")
    assert (settings.wire_model, settings.max_output, settings.max_context) == (
        "gemini-custom",
        8192,
        120000,
    )
    assert settings.thinking_config == {}


@pytest.mark.parametrize(
    "model,effort,limit,error",
    [
        ("gemini-4-argon-minimal", "", None, "unsupported_reasoning_effort"),
        ("gemini-4-argon-tiered", "", None, "unsupported_reasoning_effort"),
        ("gemini-3.8-flash-low", "high", None, "conflicting_reasoning_effort"),
        ("gpt-oss-120b", "high", None, "unsupported_reasoning_effort"),
        ("gemini-unknown", "high", None, "unsupported_reasoning_effort"),
        (
            "gemini-3.8-flash-low",
            "",
            1000,
            "max_output_tokens_must_exceed_thinking_budget",
        ),
        ("gpt-oss-120b", "", 8192, "max_output_tokens_must_exceed_thinking_budget"),
        ("gemini-3.8-flash", "low", 65537, "invalid_max_output_tokens"),
        ("gemini-3.8-flash", "low", 1.5, "invalid_max_output_tokens"),
        ("gemini-3.8-flash", "low", True, "invalid_max_output_tokens"),
        ("claude-sonnet-5-5", "", 128001, "invalid_max_output_tokens"),
    ],
)
def test_reject_invalid_settings(model, effort, limit, error):
    with pytest.raises(ValueError, match=error):
        model_settings(model, effort, limit)


def test_dynamic_budget_accepts_any_output_limit():
    settings = model_settings("gemini-3.8-flash-high", "", 64)
    assert settings.max_output == 64
    assert settings.thinking_config["thinkingBudget"] == -1


@pytest.mark.parametrize("old,replacement", sorted(DEPRECATED.items()))
def test_retired_raw_ids_name_their_successor(old, replacement):
    assert replacement in MODELS
    for model in (old, old + "-high"):
        with pytest.raises(ValueError, match=f"deprecated_model, use {replacement}"):
            model_settings(model)


def test_smaller_explicit_output_is_preserved():
    assert model_settings("gemini-4-argon", "low", 4096).max_output == 4096
    assert model_settings("gemini-unknown", "", 1024).max_output == 1024


@pytest.mark.parametrize(
    "selector",
    [
        "gemini-4-argon@reasoning=minimal",
        "gemini-4-argon@context=1048k",
        "gemini-3.8-flash@context=900k",
        "gpt-oss-120b@reasoning=medium",
        "claude-sonnet-5-5@reasoning=max",
    ],
)
def test_catalog_rejects_unsupported_variations(selector):
    with pytest.raises(ValueError, match="Unknown variation"):
        get_profile("google-antigravity/" + selector)


@pytest.mark.parametrize(
    "selector,context,effort",
    [
        ("gemini-4-argon", 256000, "medium"),
        ("gemini-4-argon@context=512k", 512000, "medium"),
        ("gemini-4-argon@context=900k,reasoning=low", 900000, "low"),
        ("gemini-3.8-flash@context=1048k", 1048576, "medium"),
        ("claude-opus-5-5@reasoning=high,context=840k", 840000, "high"),
        ("gpt-oss-120b@context=131k", 131072, "medium"),
    ],
)
def test_context_window_selects_profile_budget(selector, context, effort):
    profile = get_profile("google-antigravity/" + selector)
    assert profile.max_context == context
    assert profile.reasoning_effort == effort
    settings = model_settings(profile.model, profile.reasoning_effort)
    assert settings.wire_model == f"{profile.model}-{effort}"


@pytest.mark.parametrize(
    "selector,successor,effort",
    [
        ("gemini-3.6-flash", "gemini-3.8-flash", "medium"),
        ("gemini-3.7-flash@reasoning=low", "gemini-3.8-flash", "low"),
        ("gemini-3.1-pro@reasoning=high", "gemini-4-argon", "high"),
        ("claude-sonnet-4-6", "claude-sonnet-5-5", "medium"),
        ("claude-opus-4-6-thinking", "claude-opus-5-5", "medium"),
    ],
)
def test_retired_preset_names_resolve_to_successor(selector, successor, effort):
    profile = get_profile("google-antigravity/" + selector)
    assert (profile.provider, profile.model) == ("google-antigravity", successor)
    assert profile.reasoning_effort == effort


def test_retired_presets_are_not_advertised():
    for old in DEPRECATED:
        assert ("google-antigravity", old) not in get_all_presets()
