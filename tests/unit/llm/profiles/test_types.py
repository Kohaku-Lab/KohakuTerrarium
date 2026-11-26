"""Unit tests for ``llm/profiles/types.py`` — LLMBackend / LLMPreset / LLMProfile.

Behavior-first: the dataclasses own to_dict/from_dict round-tripping and
a couple of legacy-shape migrations. Each assert pins the exact resulting
field values.
"""

import pytest

from kohakuterrarium.llm.profiles.types import LLMBackend, LLMPreset, LLMProfile

# ---------------------------------------------------------------------------
# LLMBackend
# ---------------------------------------------------------------------------


class TestLLMBackend:
    def test_to_dict_minimal_omits_empty_fields(self):
        backend = LLMBackend(name="custom", backend_type="openai")
        assert backend.to_dict() == {"backend_type": "openai"}

    def test_to_dict_includes_set_fields(self):
        backend = LLMBackend(
            name="custom",
            backend_type="openai",
            base_url="http://h/v1",
            api_key_env="MY_KEY",
            provider_name="codex",
            provider_native_tools=["image_gen"],
        )
        assert backend.to_dict() == {
            "backend_type": "openai",
            "base_url": "http://h/v1",
            "api_key_env": "MY_KEY",
            "provider_name": "codex",
            "provider_native_tools": ["image_gen"],
        }

    def test_from_dict_round_trip(self):
        data = {
            "backend_type": "anthropic",
            "base_url": "http://h",
            "api_key_env": "K",
            "provider_name": "p",
            "provider_native_tools": ["a", "b"],
        }
        backend = LLMBackend.from_dict("my-backend", data)
        assert backend.name == "my-backend"
        assert backend.to_dict() == data

    def test_from_dict_legacy_provider_key_maps_to_backend_type(self):
        # old shape used 'provider' instead of 'backend_type'
        backend = LLMBackend.from_dict("b", {"provider": "codex"})
        assert backend.backend_type == "codex"

    def test_from_dict_missing_backend_type_defaults_to_openai(self):
        backend = LLMBackend.from_dict("b", {})
        assert backend.backend_type == "openai"

    def test_from_dict_non_list_native_tools_coerced_to_empty(self):
        backend = LLMBackend.from_dict(
            "b", {"backend_type": "openai", "provider_native_tools": "notalist"}
        )
        assert backend.provider_native_tools == []

    def test_from_dict_native_tools_stringified_and_falsy_dropped(self):
        backend = LLMBackend.from_dict(
            "b", {"backend_type": "openai", "provider_native_tools": ["x", "", None, 7]}
        )
        assert backend.provider_native_tools == ["x", "7"]

    def test_native_tools_default_is_independent_list(self):
        a = LLMBackend(name="a", backend_type="openai")
        b = LLMBackend(name="b", backend_type="openai")
        a.provider_native_tools.append("t")
        assert b.provider_native_tools == []


# ---------------------------------------------------------------------------
# LLMPreset
# ---------------------------------------------------------------------------


class TestLLMPreset:
    def test_defaults(self):
        preset = LLMPreset(name="p", model="m")
        assert preset.provider == ""
        assert preset.max_context == 256000
        assert preset.max_output == 65536
        assert preset.temperature is None
        assert preset.reasoning_effort == ""
        assert preset.provider_native_tools is None
        assert preset.variation_groups == {}

    def test_to_dict_minimal(self):
        preset = LLMPreset(name="p", model="gpt-5.4")
        assert preset.to_dict() == {
            "model": "gpt-5.4",
            "max_context": 256000,
            "max_output": 65536,
        }

    def test_to_dict_includes_optional_fields(self):
        preset = LLMPreset(
            name="p",
            model="m",
            provider="codex",
            temperature=0.7,
            reasoning_effort="high",
            service_tier="priority",
            extra_body={"reasoning": {"effort": "high"}},
            retry_policy={"max": 3},
            provider_native_tools=[],
            variation_groups={"r": {"low": {}}},
        )
        d = preset.to_dict()
        assert d["provider"] == "codex"
        assert d["temperature"] == 0.7
        assert d["reasoning_effort"] == "high"
        assert d["service_tier"] == "priority"
        assert d["extra_body"] == {"reasoning": {"effort": "high"}}
        assert d["retry_policy"] == {"max": 3}
        assert d["provider_native_tools"] == []
        assert d["variation_groups"] == {"r": {"low": {}}}

    def test_to_dict_temperature_zero_is_kept(self):
        # temperature=0.0 is a valid value distinct from "unset"
        preset = LLMPreset(name="p", model="m", temperature=0.0)
        assert preset.to_dict()["temperature"] == 0.0

    def test_from_dict_round_trip(self):
        data = {
            "model": "m",
            "provider": "openai",
            "max_context": 100,
            "max_output": 50,
            "temperature": 0.5,
            "reasoning_effort": "low",
        }
        preset = LLMPreset.from_dict("p", data)
        assert preset.name == "p"
        assert preset.model == "m"
        assert preset.provider == "openai"
        assert preset.max_context == 100
        assert preset.max_output == 50

    def test_from_dict_backend_key_aliases_provider(self):
        # legacy data used 'backend' for the provider field
        preset = LLMPreset.from_dict("p", {"model": "m", "backend": "codex"})
        assert preset.provider == "codex"

    def test_from_dict_provider_wins_over_backend(self):
        preset = LLMPreset.from_dict(
            "p", {"model": "m", "provider": "openai", "backend": "codex"}
        )
        assert preset.provider == "openai"

    def test_from_dict_none_variation_groups_coerced_to_empty(self):
        preset = LLMPreset.from_dict("p", {"model": "m", "variation_groups": None})
        assert preset.variation_groups == {}

    def test_from_dict_preserves_explicit_empty_native_tools(self):
        preset = LLMPreset.from_dict("p", {"model": "m", "provider_native_tools": []})
        assert preset.provider_native_tools == []
        assert preset.to_dict()["provider_native_tools"] == []


# ---------------------------------------------------------------------------
# LLMProfile
# ---------------------------------------------------------------------------


class TestLLMProfile:
    def test_defaults(self):
        profile = LLMProfile(name="p", model="m")
        assert profile.provider == ""
        assert profile.backend_type == ""
        assert profile.max_context == 256000
        assert profile.selected_variations == {}
        assert profile.backend_native_tools == []

    def test_to_dict_minimal(self):
        profile = LLMProfile(name="p", model="m")
        assert profile.to_dict() == {
            "model": "m",
            "max_context": 256000,
            "max_output": 65536,
        }

    def test_to_dict_includes_set_fields(self):
        profile = LLMProfile(
            name="p",
            model="m",
            provider="codex",
            backend_type="codex",
            base_url="http://h",
            api_key_env="K",
            temperature=1.0,
            reasoning_effort="xhigh",
            service_tier="priority",
            extra_body={"a": 1},
            retry_policy={"max": 2},
            selected_variations={"reasoning": "high"},
        )
        d = profile.to_dict()
        assert d["provider"] == "codex"
        assert d["backend_type"] == "codex"
        assert d["base_url"] == "http://h"
        assert d["api_key_env"] == "K"
        assert d["temperature"] == 1.0
        assert d["reasoning_effort"] == "xhigh"
        assert d["service_tier"] == "priority"
        assert d["extra_body"] == {"a": 1}
        assert d["retry_policy"] == {"max": 2}
        assert d["selected_variations"] == {"reasoning": "high"}

    def test_from_dict_legacy_backend_type_in_provider_field_migrated(self):
        # docstring: a legacy backend type (codex/openai/anthropic) sitting
        # in 'provider' is moved to backend_type and provider cleared
        profile = LLMProfile.from_dict("p", {"model": "m", "provider": "codex"})
        assert profile.backend_type == "codex"
        assert profile.provider == ""

    def test_from_dict_legacy_migration_skipped_if_backend_type_set(self):
        profile = LLMProfile.from_dict(
            "p", {"model": "m", "provider": "openai", "backend_type": "anthropic"}
        )
        # explicit backend_type present -> no migration
        assert profile.backend_type == "anthropic"
        assert profile.provider == "openai"

    def test_from_dict_real_provider_name_not_migrated(self):
        # 'my-enterprise' is not a legacy backend type -> stays in provider
        profile = LLMProfile.from_dict("p", {"model": "m", "provider": "my-enterprise"})
        assert profile.provider == "my-enterprise"
        assert profile.backend_type == ""

    def test_from_dict_backend_key_aliases_provider(self):
        profile = LLMProfile.from_dict("p", {"model": "m", "backend": "gemini"})
        assert profile.provider == "gemini"

    def test_from_dict_non_list_native_tools_coerced(self):
        profile = LLMProfile.from_dict(
            "p", {"model": "m", "backend_native_tools": "bad"}
        )
        assert profile.backend_native_tools == []

    def test_from_dict_native_tools_stringified(self):
        profile = LLMProfile.from_dict(
            "p", {"model": "m", "backend_native_tools": ["a", None, 3]}
        )
        assert profile.backend_native_tools == ["a", "3"]


class TestContextWindowSerde:
    def test_preset_round_trips_context_window(self):
        preset = LLMPreset(name="p", model="m", context_window=1_000_000)
        data = preset.to_dict()
        assert data["context_window"] == 1_000_000
        assert LLMPreset.from_dict("p", data).context_window == 1_000_000

    def test_unknown_context_window_is_omitted_and_reads_as_none(self):
        data = LLMPreset(name="p", model="m").to_dict()
        assert "context_window" not in data
        assert LLMPreset.from_dict("p", data).context_window is None

    @pytest.mark.parametrize("raw", [0, -3, "1m", True, 1.5])
    def test_malformed_stored_context_window_reads_as_none(self, raw):
        assert LLMPreset.from_dict("p", {"context_window": raw}).context_window is None

    def test_profile_round_trips_context_window(self):
        profile = LLMProfile(name="p", model="m", context_window=500_000)
        assert profile.to_dict()["context_window"] == 500_000
        assert LLMProfile.from_dict("p", profile.to_dict()).context_window == 500_000
        assert "context_window" not in LLMProfile(name="p", model="m").to_dict()


_CC_CONTEXT = {
    "200k": {"max_context": 200000},
    "400k": {"max_context": 400000},
    "700k": {"max_context": 700000},
    "1m": {"max_context": 1000000},
}
_REASONING = {"high": {"reasoning_effort": "high"}}


def _legacy_data(**extra):
    return {
        "model": "claude-x",
        "max_context": 200000,
        "variation_groups": {"context": _CC_CONTEXT, "reasoning": _REASONING},
        **extra,
    }


class TestLegacyContextFold:
    """A ``context`` group that only sets ``max_context`` reads as numeric context."""

    def test_from_dict_folds_the_group_and_derives_the_window(self):
        preset = LLMPreset.from_dict("cc", _legacy_data())
        assert preset.variation_groups == {"reasoning": _REASONING}
        assert preset.context_window == 1000000
        assert preset.context_aliases == {}
        assert "context" not in preset.to_dict()["variation_groups"]

    def test_direct_construction_folds_too(self):
        preset = LLMPreset(
            name="cc", model="m", variation_groups={"context": _CC_CONTEXT}
        )
        assert preset.variation_groups == {}
        assert preset.context_window == 1000000

    def test_a_set_window_is_not_replaced(self):
        preset = LLMPreset.from_dict("cc", _legacy_data(context_window=1048576))
        assert preset.context_window == 1048576

    def test_mixed_context_group_stays_a_named_group(self):
        groups = {"context": {"1m": {"max_context": 1000000, "max_output": 1}}}
        preset = LLMPreset.from_dict("cc", {"model": "m", "variation_groups": groups})
        assert preset.variation_groups == groups
        assert preset.context_window is None
        assert preset.to_stored_dict()["variation_groups"] == groups

    def test_aliases_serialise_only_when_present_and_round_trip(self):
        groups = {"context": {"long": {"max_context": 900000}}}
        preset = LLMPreset.from_dict("cc", {"model": "m", "variation_groups": groups})
        assert preset.context_aliases == {"long": 900000}
        data = preset.to_dict()
        assert data["context_aliases"] == {"long": 900000}
        assert "variation_groups" not in data
        assert LLMPreset.from_dict("cc", data) == preset
        assert "context_aliases" not in LLMPreset(name="p", model="m").to_dict()

    @pytest.mark.parametrize(
        "raw", [{"x": 0}, {"x": "1m"}, {"x": True}, {"": 5}, ["x"], "x"]
    )
    def test_malformed_stored_aliases_are_dropped(self, raw):
        preset = LLMPreset.from_dict("p", {"model": "m", "context_aliases": raw})
        assert preset.context_aliases == {}

    def test_unchanged_preset_is_stored_as_it_was_read(self):
        preset = LLMPreset.from_dict("cc", _legacy_data())
        stored = preset.to_stored_dict()
        assert stored["variation_groups"] == {
            "context": _CC_CONTEXT,
            "reasoning": _REASONING,
        }
        assert "context_window" not in stored
        assert "context_aliases" not in stored
        assert LLMPreset.from_dict("cc", stored) == preset

    def test_an_edit_outside_the_fold_keeps_the_stored_group(self):
        preset = LLMPreset.from_dict("cc", _legacy_data())
        preset.max_output = 1234
        stored = preset.to_stored_dict()
        assert stored["max_output"] == 1234
        assert "context" in stored["variation_groups"]

    def test_an_edit_to_the_groups_stores_the_folded_form(self):
        groups = {"context": {"long": {"max_context": 900000}}, "r": _REASONING}
        preset = LLMPreset.from_dict("cc", {"model": "m", "variation_groups": groups})
        preset.variation_groups = {}
        stored = preset.to_stored_dict()
        assert "variation_groups" not in stored
        # The alias survives a save of the folded form.
        assert stored["context_aliases"] == {"long": 900000}
        assert stored["context_window"] == 900000
        assert LLMPreset.from_dict("cc", stored).context_aliases == {"long": 900000}

    def test_an_edited_window_is_stored_next_to_the_group(self):
        preset = LLMPreset.from_dict("cc", _legacy_data())
        preset.context_window = 2000000
        stored = preset.to_stored_dict()
        assert "context" in stored["variation_groups"]
        assert stored["context_window"] == 2000000

    def test_inherit_context_fold_carries_aliases_and_stored_form(self):
        groups = {"context": {"long": {"max_context": 900000}}}
        stored = LLMPreset.from_dict("cc", {"model": "m", "variation_groups": groups})
        edited = LLMPreset(name="cc", model="m", max_output=99, context_window=900000)
        edited.inherit_context_fold(stored)
        assert edited.context_aliases == {"long": 900000}
        assert edited.to_stored_dict()["variation_groups"] == groups
        # A missing previous preset changes nothing.
        fresh = LLMPreset(name="cc", model="m")
        fresh.inherit_context_fold(None)
        assert fresh.context_aliases == {}
        assert fresh.to_stored_dict() == fresh.to_dict()
