"""Model-catalog contract: newest models present, OpenRouter in step with the direct lists."""

import pytest

from kohakuterrarium.llm.presets.aliases import resolve_deprecated
from kohakuterrarium.llm.presets.catalog import get_all_presets, resolve_alias
from kohakuterrarium.llm.profiles.variations import apply_variation_groups

PRESETS = get_all_presets()


def _norm(model: str) -> str:
    return model.rsplit("/", 1)[-1].replace(".", "-")


def _options(provider: str, name: str, group: str) -> set[str]:
    return set(PRESETS[(provider, name)]["variation_groups"][group])


class TestNewestModels:
    @pytest.mark.parametrize(
        ("provider", "name", "model", "effort"),
        [
            ("anthropic", "claude-fable-5.2", "claude-fable-5-2", "high"),
            ("anthropic", "claude-opus-5.5", "claude-opus-5-5", "medium"),
            ("anthropic", "claude-sonnet-5.5", "claude-sonnet-5-5", "high"),
            ("anthropic", "claude-haiku-5.5", "claude-haiku-5-5", "medium"),
        ],
    )
    def test_direct_claude_models_use_the_documented_default_effort(
        self, provider, name, model, effort
    ):
        preset = PRESETS[(provider, name)]
        assert preset["model"] == model
        assert preset["max_context"] == 1_000_000
        assert preset["extra_body"]["output_config"]["effort"] == effort

    def test_short_names_follow_the_newest_models(self):
        assert resolve_alias("fable") == ("anthropic", "claude-fable-5.2")
        assert resolve_alias("opus") == ("anthropic", "claude-opus-5.5")
        assert resolve_alias("kimi") == ("openrouter", "kimi-k3.1-pro")
        assert resolve_alias("carbon") == ("gemini", "gemini-4-carbon")

    def test_gemini_carbon_exists_on_every_route(self):
        assert PRESETS[("gemini", "gemini-4-carbon")]["model"] == "gemini-4-carbon"
        assert PRESETS[("google-antigravity", "gemini-4-carbon")]["model"] == (
            "gemini-4-carbon"
        )
        assert PRESETS[("openrouter", "gemini-4-carbon")]["model"] == (
            "google/gemini-4-carbon"
        )

    def test_openrouter_carbon_cannot_switch_reasoning_off(self):
        preset = PRESETS[("openrouter", "gemini-4-carbon")]
        assert _options("openrouter", "gemini-4-carbon", "reasoning") == {
            "low",
            "medium",
            "high",
        }
        assert preset["extra_body"]["reasoning"] == {
            "enabled": True,
            "effort": "medium",
        }

    @pytest.mark.parametrize(
        "name", ["kimi-k3.1-pro", "ripple-65b-preview", "muse-ember", "hy4"]
    )
    def test_new_openrouter_models_are_listed(self, name):
        assert ("openrouter", name) in PRESETS

    @pytest.mark.parametrize(
        ("old", "new"),
        [("kimi-k2.6", "kimi-k3.1-pro"), ("gpt-5.4", "gpt-5.5")],
    )
    def test_retired_openrouter_models_point_at_a_listed_successor(self, old, new):
        assert ("openrouter", old) not in PRESETS
        assert resolve_deprecated(old, "openrouter") == [("openrouter", new)]
        assert ("openrouter", new) in PRESETS


class TestOpenRouterKeepsUp:
    @pytest.mark.parametrize("direct", ["anthropic", "gemini"])
    def test_every_direct_model_has_an_openrouter_route(self, direct):
        routed = {
            _norm(data["model"])
            for (provider, _), data in PRESETS.items()
            if provider == "openrouter"
        }
        missing = {
            data["model"]
            for (provider, _), data in PRESETS.items()
            if provider == direct and _norm(data["model"]) not in routed
        }
        assert missing == set()


class TestOpenRouterContextPresets:
    def test_large_window_models_offer_local_context_budgets(self):
        large = {
            key: data
            for key, data in PRESETS.items()
            if key[0] == "openrouter" and data["max_context"] >= 500_000
        }
        assert large
        for data in large.values():
            assert _context_budget(data, "400k") == 400_000

    def test_small_window_models_are_left_alone(self):
        small = [
            data
            for (provider, _), data in PRESETS.items()
            if provider == "openrouter" and data["max_context"] < 500_000
        ]
        assert small
        assert all("context" not in d.get("variation_groups", {}) for d in small)


def _context_budget(data: dict, option: str) -> int:
    return apply_variation_groups(data, data["variation_groups"], {"context": option})[
        "max_context"
    ]
