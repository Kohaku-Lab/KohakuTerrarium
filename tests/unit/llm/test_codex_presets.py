"""Regression tests for current Codex subscription preset metadata."""

import pytest

from kohakuterrarium.llm.presets import PRESETS, get_all_presets, resolve_alias
from kohakuterrarium.llm.variations import apply_variation_groups


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
