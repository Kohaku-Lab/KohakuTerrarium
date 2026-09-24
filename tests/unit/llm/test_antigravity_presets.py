"""Antigravity catalog limits and supported KT variation selections."""

import pytest

from kohakuterrarium.llm.antigravity_presets import model_settings
from kohakuterrarium.llm.presets import get_all_presets
from kohakuterrarium.llm.profiles import get_profile


@pytest.mark.parametrize(
    "model,context,output,efforts",
    [
        ("gemini-3.6-flash", 1048576, 65536, ["low", "medium", "high"]),
        ("gemini-3.7-flash", 1048576, 65536, ["low", "medium", "high"]),
        ("gemini-3.8-flash", 1048576, 65536, ["low", "medium", "high"]),
        ("gemini-3.1-pro", 1048576, 65535, ["low", "high"]),
        ("claude-sonnet-4-6", 250000, 64000, []),
        ("claude-opus-4-6-thinking", 250000, 64000, []),
    ],
)
def test_current_agy_limits_and_variations(model, context, output, efforts):
    preset = get_all_presets()[("google-antigravity", model)]
    assert (preset["max_context"], preset["max_output"]) == (context, output)
    assert list(preset.get("variation_groups", {}).get("reasoning", {})) == efforts
    for effort in efforts:
        profile = get_profile(f"google-antigravity/{model}@reasoning={effort}")
        assert profile.reasoning_effort == effort
        assert profile.model == model
        assert profile.selected_variations == {"reasoning": effort}


@pytest.mark.parametrize(
    "model,effort,wire,limit",
    [
        ("gemini-3.8-flash", "", "gemini-3.8-flash-tiered", 65536),
        ("gemini-3.7-flash-tiered", "medium", "gemini-3.7-flash-tiered", 65536),
        ("gemini-3.6-flash-low", "low", "gemini-3.6-flash-low", 65536),
        ("gemini-3.8-flash-low", "", "gemini-3.8-flash-tiered", 65536),
        ("gemini-3.7-flash-high", "high", "gemini-3.7-flash-tiered", 65536),
        ("gemini-3.1-pro-low", "", "gemini-3.1-pro-low", 65535),
        ("gemini-3.1-pro-high", "", "gemini-pro-agent", 65535),
        ("gemini-3.1-pro", "high", "gemini-pro-agent", 65535),
        ("gemini-custom", "", "gemini-custom", 8192),
    ],
)
def test_default_and_explicit_sku_settings(model, effort, wire, limit):
    settings = model_settings(model, effort)
    assert settings.wire_model == wire
    assert settings.max_output == limit


@pytest.mark.parametrize(
    "model,effort,limit,error",
    [
        ("gemini-3.1-pro-medium", "", None, "unsupported_reasoning_effort"),
        ("gemini-3.8-flash-low", "high", None, "conflicting_reasoning_effort"),
        (
            "gemini-3.1-pro",
            "low",
            1001,
            "max_output_tokens_must_exceed_thinking_budget",
        ),
        (
            "claude-sonnet-4-6",
            "",
            1024,
            "max_output_tokens_must_exceed_thinking_budget",
        ),
        ("claude-opus-4-6-thinking", "high", None, "unsupported_reasoning_effort"),
        ("gemini-unknown", "high", None, "unsupported_reasoning_effort"),
        ("gemini-3.8-flash", "low", 65537, "invalid_max_output_tokens"),
        ("gemini-3.8-flash", "low", 1.5, "invalid_max_output_tokens"),
        ("gemini-3.8-flash", "low", True, "invalid_max_output_tokens"),
    ],
)
def test_reject_invalid_settings(model, effort, limit, error):
    with pytest.raises(ValueError, match=error):
        model_settings(model, effort, limit)


def test_smaller_explicit_output_is_preserved():
    assert model_settings("gemini-3.1-pro", "low", 4096).max_output == 4096
    assert model_settings("gemini-unknown", "", 1024).max_output == 1024


@pytest.mark.parametrize(
    "selector",
    [
        "gemini-3.1-pro@reasoning=medium",
        "gemini-3.8-flash@reasoning=none",
        "claude-sonnet-4-6@reasoning=high",
        "claude-opus-4-6-thinking@reasoning=low",
    ],
)
def test_catalog_rejects_unsupported_variations(selector):
    with pytest.raises(ValueError, match="Unknown variation"):
        get_profile("google-antigravity/" + selector)


def test_retired_flash_is_not_advertised_as_an_antigravity_preset():
    assert ("google-antigravity", "gemini-3-flash") not in get_all_presets()
