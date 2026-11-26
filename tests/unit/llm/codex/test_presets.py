"""Regression tests for current Codex subscription preset metadata."""

import pytest

from kohakuterrarium.llm.presets.catalog import PRESETS, get_all_presets, resolve_alias
from kohakuterrarium.llm.profiles.variations import apply_variation_groups


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"])
def test_current_codex_preset_preserves_model_and_composes_variations(model):
    preset = PRESETS[model]
    assert get_all_presets()[("codex", model)]["model"] == model
    assert preset["provider"] == "codex"
    assert preset["max_context"] == 272000
    assert preset["max_output"] == 128000
    assert preset["reasoning_effort"] == "medium"
    assert preset["extra_body"] == {"websocket_mode": True}
    patched = apply_variation_groups(
        preset,
        preset["variation_groups"],
        {"reasoning": "max", "context": "1m", "speed": "fast"},
    )
    assert patched["model"] == model
    assert patched["reasoning_effort"] == "max"
    assert patched["max_context"] == 1000000
    assert patched["service_tier"] == "priority"
    assert "mode" not in preset["variation_groups"]
    assert "none" not in preset["variation_groups"]["reasoning"]


def test_luna_does_not_inherit_sol_ultra():
    assert set(PRESETS["gpt-6-luna"]["variation_groups"]["reasoning"]) == {
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    }
    for model in ("gpt-6.1-sol", "gpt-6-sol", "gpt-6-astra"):
        assert PRESETS[model]["variation_groups"]["reasoning"]["ultra"] == {
            "reasoning_effort": "ultra"
        }


def test_existing_codex_defaults_and_aliases_are_unchanged():
    assert PRESETS["gpt-6-astra"]["max_context"] == 1000000
    assert PRESETS["gpt-6-astra"]["reasoning_effort"] == "xhigh"
    assert resolve_alias("sol") == ("codex", "gpt-5.6-sol")
    assert resolve_alias("gpt6") == ("codex", "gpt-6-astra")


@pytest.mark.parametrize(
    "model,max_context,effort,sibling",
    [
        # Codex catalog (codex-rs/models-manager/models.json, 2026-11):
        # context_window 500k / 272k / 500k, max_context_window 872k, low..ultra.
        ("gpt-6.2-sol", 500000, "medium", "gpt-6.1-sol"),
        ("gpt-6.2-astra", 272000, "xhigh", "gpt-6-astra"),
        ("gpt-6.1-terra", 500000, "xhigh", "gpt-5.6-terra"),
    ],
)
def test_newest_codex_presets_follow_the_catalog(model, max_context, effort, sibling):
    preset = get_all_presets()[("codex", model)]
    assert preset["model"] == model
    assert preset["max_context"] == max_context
    assert preset["context_window"] == 1_000_000
    assert preset["max_output"] == 128000
    assert preset["reasoning_effort"] == effort == PRESETS[sibling]["reasoning_effort"]
    assert preset["extra_body"] == {"websocket_mode": True}
    groups = preset["variation_groups"]
    assert set(groups) == {"reasoning", "speed"}
    assert list(groups["reasoning"]) == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    patched = apply_variation_groups(
        preset, groups, {"reasoning": "ultra", "speed": "fast", "context": "872k"}
    )
    assert patched["reasoning_effort"] == "ultra"
    assert patched["service_tier"] == "priority"
    assert patched["max_context"] == 872000
