"""Unit tests for ``llm/presets/drafts.py``: presets for discovered models.

Behavior-first: the exact draft each discovery shape produces, against the
real built-in catalog where the scenario is a real provider (Codex) and a
small explicit catalog where the scenario needs a specific sibling shape.
"""

import logging

import pytest

from kohakuterrarium.llm.discovery import DiscoveredModel
from kohakuterrarium.llm.presets.catalog import get_all_presets
from kohakuterrarium.llm.presets.drafts import (
    catalog_rows,
    draft_kind,
    draft_preset,
    model_family,
    nearest_sibling,
    suggest_drafts,
)
from kohakuterrarium.llm.presets.groups import (
    _OR_REASONING_GROUP_TOP_TOGGLE,
    _OR_REASONING_TOGGLE_GROUP,
)
from kohakuterrarium.llm.profiles.resolve import save_backend, save_profile
from kohakuterrarium.llm.profiles.types import LLMBackend, LLMPreset

CODEX_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultra")


class TestModelFamily:
    @pytest.mark.parametrize(
        "model,family,version",
        [
            ("gpt-6.2-sol", "gpt-*-sol", (6, 2)),
            ("gpt-6-astra", "gpt-*-astra", (6,)),
            ("claude-opus-5-5", "claude-opus-*", (5, 5)),
            ("claude-opus-5", "claude-opus-*", (5,)),
            ("anthropic/claude-opus-5.5", "anthropic/claude-opus-*", (5, 5)),
            ("x-ai/grok-4.7", "x-ai/grok-*", (4, 7)),
            ("deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v*-flash", (4, 1)),
            ("qwen3.8-max", "qwen*-max", (3, 8)),
        ],
    )
    def test_strips_the_last_version(self, model, family, version):
        assert model_family(model) == (family, version)

    @pytest.mark.parametrize("model", ["gpt-4o", "gpt-oss-120b", "sonar", ""])
    def test_ids_without_a_bounded_version_have_no_family(self, model):
        assert model_family(model) is None


class TestNearestSibling:
    CATALOG = {
        ("p", "a"): {"model": "fam-5-x"},
        ("p", "b"): {"model": "fam-6.1-x"},
        ("p", "c"): {"model": "fam-7-x"},
        ("q", "d"): {"model": "fam-6.5-x"},
    }

    def test_highest_version_below_wins(self):
        assert nearest_sibling("p", "fam-6.4-x", self.CATALOG) == {"model": "fam-6.1-x"}

    def test_lowest_version_above_when_none_below(self):
        assert nearest_sibling("p", "fam-4-x", self.CATALOG) == {"model": "fam-5-x"}

    def test_other_providers_and_families_are_ignored(self):
        assert nearest_sibling("q", "fam-9-x", self.CATALOG) == {"model": "fam-6.5-x"}
        assert nearest_sibling("p", "other-6-x", self.CATALOG) is None
        assert nearest_sibling("p", "gpt-4o", self.CATALOG) is None


class TestDraftKind:
    @pytest.mark.parametrize(
        "backend_type,base_url,kind",
        [
            ("codex", "", "codex"),
            ("codex", "https://responses.example/v1", "codex"),
            ("openai", "https://api.openai.com/v1", "openai"),
            ("openai", "https://openrouter.ai/api/v1", "openrouter"),
            (
                "openai",
                "https://generativelanguage.googleapis.com/v1beta/openai/",
                "gemini",
            ),
            ("openai", "https://api.deepseek.com/v1", "deepseek"),
            ("openai", "https://vllm.local/v1", ""),
            ("anthropic", "https://api.anthropic.com", "anthropic"),
            ("anthropic", "https://api.kimi.com/coding/", ""),
            ("grok-subscription", "", "grok"),
            ("echo", "", ""),
        ],
    )
    def test_kind_follows_backend_and_endpoint(self, backend_type, base_url, kind):
        backend = LLMBackend(name="x", backend_type=backend_type, base_url=base_url)
        assert draft_kind(backend) == kind
        assert draft_kind(None) == ""


def _codex(model, levels=CODEX_LEVELS, **limits):
    return DiscoveredModel(model, reasoning_levels=tuple(levels), **limits)


class TestSiblingInheritance:
    def test_codex_model_inherits_the_nearest_family_preset(self):
        draft = draft_preset(
            "codex",
            _codex("gpt-6.3-sol", max_context=500000),
            "codex",
            get_all_presets(),
        )
        sibling = get_all_presets()[("codex", "gpt-6.2-sol")]
        assert draft["name"] == draft["model"] == "gpt-6.3-sol"
        assert draft["max_context"] == sibling["max_context"]
        assert draft["context_window"] == 500000
        assert draft["max_output"] == 128000
        assert draft["reasoning_effort"] == "medium"
        assert draft["extra_body"] == {"websocket_mode": True}
        assert draft["variation_groups"] == sibling["variation_groups"]
        assert "context" not in draft["variation_groups"]

    def test_reasoning_group_is_trimmed_to_reported_levels(self):
        draft = draft_preset(
            "codex", _codex("gpt-6.3-sol", ["low", "high"]), "codex", get_all_presets()
        )
        assert draft["variation_groups"]["reasoning"] == {
            "low": {"reasoning_effort": "low"},
            "high": {"reasoning_effort": "high"},
        }
        # medium is gone: the highest reported level below it is low.
        assert draft["reasoning_effort"] == "low"
        assert draft["variation_groups"]["speed"]

    def test_default_falls_back_to_first_reported_level(self):
        draft = draft_preset(
            "codex",
            _codex("gpt-6.3-sol", ["xhigh", "high"]),
            "codex",
            get_all_presets(),
        )
        assert draft["reasoning_effort"] == "xhigh"

    def test_sibling_without_ultra_does_not_gain_it(self):
        # gpt-6.1-terra's nearest codex sibling is the 5.6 terra (no ultra).
        catalog = {
            k: v
            for k, v in get_all_presets().items()
            if k != ("codex", "gpt-6.1-terra")
        }
        draft = draft_preset("codex", _codex("gpt-6.1-terra"), "codex", catalog)
        assert list(draft["variation_groups"]["reasoning"]) == [
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ]
        assert draft["reasoning_effort"] == "xhigh"

    def test_extra_body_default_follows_the_trimmed_group(self):
        catalog = {
            ("openrouter", "vendor-2"): {
                "model": "vendor/model-2",
                "max_context": 400000,
                "max_output": 9000,
                "context_window": 400000,
                "extra_body": {"reasoning": {"enabled": True, "effort": "high"}},
                "provider_native_tools": [],
                "variation_groups": {
                    "reasoning": _OR_REASONING_GROUP_TOP_TOGGLE,
                    "context": {"1m": {"max_context": 1000000}},
                },
            }
        }
        model = DiscoveredModel(
            "vendor/model-3",
            max_context=800000,
            max_output=4000,
            reasoning_levels=("low", "medium"),
        )
        draft = draft_preset("openrouter", model, "openrouter", catalog)
        assert draft["name"] == "vendor-model-3"
        assert draft["variation_groups"] == {
            "reasoning": {
                "low": {"extra_body.reasoning.effort": "low"},
                "medium": {"extra_body.reasoning.effort": "medium"},
            }
        }
        assert draft["extra_body"] == {
            "reasoning": {"enabled": True, "effort": "medium"}
        }
        assert draft["provider_native_tools"] == []
        assert (draft["max_context"], draft["context_window"]) == (400000, 800000)
        # The provider's own output cap beats the sibling's.
        assert draft["max_output"] == 4000

    def test_group_whose_options_are_not_levels_is_kept(self):
        catalog = {
            ("openrouter", "t-1"): {
                "model": "t-1",
                "variation_groups": {"reasoning": _OR_REASONING_TOGGLE_GROUP},
            }
        }
        model = DiscoveredModel("t-2", reasoning_levels=("low",))
        draft = draft_preset("openrouter", model, "openrouter", catalog)
        assert draft["variation_groups"] == {"reasoning": _OR_REASONING_TOGGLE_GROUP}


class TestReasoningGroupsWithoutSibling:
    @pytest.mark.parametrize(
        "kind,group,patch",
        [
            ("codex", "reasoning", {"reasoning_effort": "high"}),
            ("openai", "reasoning", {"extra_body.reasoning.effort": "high"}),
            ("openrouter", "reasoning", {"extra_body.reasoning.effort": "high"}),
            ("anthropic", "reasoning", {"extra_body.output_config.effort": "high"}),
            ("grok", "reasoning", {"extra_body.reasoning.effort": "high"}),
            ("deepseek", "reasoning", {"extra_body.reasoning_effort": "high"}),
            ("opencode-go", "reasoning", {"extra_body.reasoning_effort": "high"}),
            (
                "gemini",
                "thinking",
                {"extra_body.extra_body.google.thinking_config.thinking_level": "HIGH"},
            ),
        ],
    )
    def test_reported_levels_become_the_kind_group(self, kind, group, patch):
        model = DiscoveredModel("solo-model", reasoning_levels=("low", "high"))
        draft = draft_preset("prov", model, kind, {})
        options = draft["variation_groups"][group]
        assert list(options) == ["low", "high"]
        assert options["high"] == patch

    def test_codex_without_medium_defaults_to_first_reported(self):
        model = DiscoveredModel("solo", reasoning_levels=("high", "xhigh"))
        assert draft_preset("codex", model, "codex", {})["reasoning_effort"] == "high"
        model = DiscoveredModel("solo", reasoning_levels=("low", "medium"))
        assert draft_preset("codex", model, "codex", {})["reasoning_effort"] == "medium"

    @pytest.mark.parametrize("kind", ["", "qwen", "mimo", "package"])
    def test_unknown_wire_field_adds_no_group(self, kind):
        model = DiscoveredModel("solo", reasoning_levels=("low", "high"))
        draft = draft_preset("prov", model, kind, {})
        assert "variation_groups" not in draft
        assert "reasoning_effort" not in draft

    def test_no_reported_levels_adds_no_group(self):
        draft = draft_preset("codex", DiscoveredModel("solo"), "codex", {})
        assert draft == {"provider": "codex", "name": "solo", "model": "solo"}

    def test_reported_limits_fill_the_bare_draft(self):
        model = DiscoveredModel("solo", max_context=64000, max_output=8000)
        assert draft_preset("x", model, "", {}) == {
            "provider": "x",
            "name": "solo",
            "model": "solo",
            "max_context": 64000,
            "max_output": 8000,
            "context_window": 64000,
        }


class TestDatedSnapshots:
    def test_snapshot_inherits_its_base_preset(self):
        catalog = {
            ("p", "team"): {
                "model": "team",
                "max_context": 64000,
                "max_output": 4000,
                "reasoning_effort": "high",
                "variation_groups": {
                    "reasoning": {
                        "low": {"reasoning_effort": "low"},
                        "high": {"reasoning_effort": "high"},
                    },
                    "speed": {"fast": {"service_tier": "priority"}},
                },
            }
        }
        draft = draft_preset("p", DiscoveredModel("team-2026-11-01"), "", catalog)
        assert (draft["max_context"], draft["max_output"]) == (64000, 4000)
        assert draft["reasoning_effort"] == "high"
        assert set(draft["variation_groups"]) == {"reasoning", "speed"}

    def test_snapshot_reported_levels_trim_the_inherited_group(self):
        catalog = {
            ("p", "team"): {
                "model": "team",
                "reasoning_effort": "high",
                "variation_groups": {
                    "reasoning": {
                        "low": {"reasoning_effort": "low"},
                        "high": {"reasoning_effort": "high"},
                    }
                },
            }
        }
        model = DiscoveredModel("team-20261101", reasoning_levels=("low",))
        draft = draft_preset("p", model, "", catalog)
        assert list(draft["variation_groups"]["reasoning"]) == ["low"]
        assert draft["reasoning_effort"] == "low"

    def test_snapshot_without_a_base_preset_uses_the_kind_group(self):
        catalog = {("p", "other"): {"model": "other", "max_context": 64000}}
        model = DiscoveredModel("team-2026-11-01", reasoning_levels=("low",))
        draft = draft_preset("p", model, "deepseek", catalog)
        assert "max_context" not in draft
        assert draft["variation_groups"] == {
            "reasoning": {"low": {"extra_body.reasoning_effort": "low"}}
        }


class TestPackageSuggestions:
    SIBLING = {
        ("echo", "m-1"): {
            "model": "m-1",
            "max_context": 50000,
            "reasoning_effort": "high",
            "variation_groups": {
                "reasoning": {
                    "low": {"reasoning_effort": "low"},
                    "high": {"reasoning_effort": "high"},
                }
            },
        }
    }

    def test_package_fields_win_over_inferred_ones(self):
        model = DiscoveredModel(
            "m-2",
            max_context=90000,
            preset={
                "max_context": 70000,
                "reasoning_effort": "low",
                "variation_groups": {"mode": {"pro": {"extra_body.mode": "pro"}}},
                "unknown_field": 1,
            },
        )
        draft = draft_preset("echo", model, "package", self.SIBLING)
        assert draft["max_context"] == 70000
        assert draft["context_window"] == 90000
        assert draft["reasoning_effort"] == "low"
        assert draft["variation_groups"] == {
            "mode": {"pro": {"extra_body.mode": "pro"}}
        }
        assert "unknown_field" not in draft

    def test_legacy_context_group_in_a_suggestion_is_folded(self):
        legacy = {
            "context": {
                "200k": {"max_context": 200000},
                "long": {"max_context": 1000000},
            },
            "mode": {"pro": {"extra_body.mode": "pro"}},
        }
        reported = DiscoveredModel(
            "m-2", max_context=90000, preset={"variation_groups": legacy}
        )
        draft = draft_preset("echo", reported, "package", self.SIBLING)
        assert draft["variation_groups"] == {"mode": legacy["mode"]}
        assert draft["context_window"] == 90000
        assert draft["context_aliases"] == {"long": 1000000}

        unreported = DiscoveredModel(
            "m-2", preset={"variation_groups": {"context": legacy["context"]}}
        )
        draft = draft_preset("echo", unreported, "", {})
        assert "variation_groups" not in draft
        assert draft["context_window"] == 1000000

    def test_existing_package_preset_draft_has_no_legacy_group(self):
        catalog = {
            ("echo", "cc"): {
                "model": "m-2",
                "variation_groups": {"context": {"1m": {"max_context": 1000000}}},
            }
        }
        draft = draft_preset("echo", DiscoveredModel("m-2"), "", catalog)
        assert "variation_groups" not in draft
        assert draft["context_window"] == 1000000

    @pytest.mark.parametrize(
        "preset",
        [
            {"variation_groups": {"g": {"o": {"model": "other"}}}},
            {"max_output": "lots"},
            {"context_window": -1},
            {"extra_body": "not-a-map"},
            "not-a-dict",
        ],
    )
    def test_invalid_package_draft_is_dropped_with_a_warning(self, preset, caplog):
        model = DiscoveredModel("m-2", max_context=90000, preset=preset)
        with caplog.at_level(logging.WARNING):
            draft = draft_preset("echo", model, "package", self.SIBLING)
        assert draft == draft_preset(
            "echo", DiscoveredModel("m-2", max_context=90000), "package", self.SIBLING
        )
        assert draft["reasoning_effort"] == "high"
        assert "package preset" in caplog.text.lower()


class TestCatalogRowsAndSaved:
    def test_rows_report_existing_presets_levels_and_window(self):
        save_profile(
            LLMPreset(
                name="mine", model="gpt-9-mine", provider="codex", max_context=1234
            )
        )
        models = [
            _codex("gpt-6.1-sol", max_context=500000),
            _codex("gpt-9-mine"),
            DiscoveredModel("brand-new", max_context=None),
        ]
        rows = catalog_rows("codex", models)
        builtin, saved, new = rows
        assert builtin["has_preset"] is True
        assert builtin["id"] == "gpt-6.1-sol"
        assert builtin["reasoning_levels"] == list(CODEX_LEVELS)
        assert builtin["context_window"] == 500000
        # An existing preset is mirrored, not re-inferred.
        assert builtin["preset"]["name"] == "gpt-6.1-sol"
        assert builtin["preset"]["max_context"] == 272000
        assert builtin["preset"]["context_window"] == 1000000
        assert saved["has_preset"] is True
        assert saved["preset"]["max_context"] == 1234
        assert new["has_preset"] is False
        assert new["context_window"] is None
        assert new["reasoning_levels"] == []
        assert new["preset"] == {
            "provider": "codex",
            "name": "brand-new",
            "model": "brand-new",
        }

    def test_suggest_drafts_uses_the_backend_kind(self):
        save_backend(
            LLMBackend(
                name="ds2",
                backend_type="openai",
                base_url="https://api.deepseek.com/v1",
            )
        )
        (draft,) = suggest_drafts(
            "ds2", [DiscoveredModel("ds-x", reasoning_levels=("high",))]
        )
        assert draft["variation_groups"] == {
            "reasoning": {"high": {"extra_body.reasoning_effort": "high"}}
        }
