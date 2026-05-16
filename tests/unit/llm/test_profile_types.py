"""Unit tests for ``llm/profile_types.py`` — LLMBackend / LLMPreset / LLMProfile.

Behavior-first: the dataclasses own to_dict/from_dict round-tripping and
a couple of legacy-shape migrations. Each assert pins the exact resulting
field values.
"""

from kohakuterrarium.llm.profile_types import LLMBackend, LLMPreset, LLMProfile

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
            variation_groups={"r": {"low": {}}},
        )
        d = preset.to_dict()
        assert d["provider"] == "codex"
        assert d["temperature"] == 0.7
        assert d["reasoning_effort"] == "high"
        assert d["service_tier"] == "priority"
        assert d["extra_body"] == {"reasoning": {"effort": "high"}}
        assert d["retry_policy"] == {"max": 3}
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
