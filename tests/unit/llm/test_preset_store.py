"""Unit tests for ``llm/preset_store.py`` — preset YAML I/O + migration.

Behavior-first: assert the exact ``(provider, name)``-keyed presets read
from both the nested and legacy-flat YAML layouts, the nested-vs-flat
heuristic, provider-priority resolution, and the serialised payload
shape. ``PROFILES_PATH`` is redirected to a per-test tmp file.
"""

import pytest

from kohakuterrarium.llm.backends import save_yaml_store
from kohakuterrarium.llm.preset_store import (
    _load_flat_presets_legacy,
    _load_nested_presets,
    _looks_nested,
    load_presets,
    preset_from_data,
    serialize_user_data,
)
from kohakuterrarium.llm.profile_types import LLMBackend, LLMPreset


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    # ``PROFILES_PATH`` is a back-compat display constant; the live read
    # / write path goes through ``_profiles_path()`` which resolves
    # ``KT_CONFIG_DIR`` fresh every call.  Patching the constant alone
    # leaks every save to the operator's real ``~/.kohakuterrarium/``.
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))


class TestPresetFromData:
    def test_explicit_provider_overrides_data_provider(self):
        preset = preset_from_data(
            "p", {"model": "m", "provider": "wrong"}, provider="openai"
        )
        assert preset.provider == "openai"

    def test_provider_inferred_from_data_when_not_passed(self):
        preset = preset_from_data(
            "p", {"model": "m", "base_url": "https://openrouter.ai/api/v1"}
        )
        assert preset.provider == "openrouter"

    def test_model_carried_through(self):
        preset = preset_from_data("p", {"model": "gpt-4o"}, provider="openai")
        assert preset.model == "gpt-4o"


class TestPresetLoaders:
    def test_nested_loader_non_dict_input_returns_empty(self):
        assert _load_nested_presets("not a dict") == {}

    def test_flat_loader_non_dict_input_returns_empty(self):
        assert _load_flat_presets_legacy(None) == {}

    def test_nested_loader_skips_non_dict_bucket(self):
        out = _load_nested_presets(
            {"openai": {"good": {"model": "m"}}, "broken": "scalar"}
        )
        assert list(out) == [("openai", "good")]

    def test_flat_loader_skips_non_dict_entry(self):
        out = _load_flat_presets_legacy(
            {"good": {"model": "m", "provider": "openai"}, "junk": "scalar"}
        )
        assert list(out) == [("openai", "good")]


class TestLooksNested:
    def test_nested_layout_detected(self):
        stored = {"openai": {"gpt-x": {"model": "gpt-x"}}}
        assert _looks_nested(stored) is True

    def test_flat_layout_detected_by_model_key(self):
        stored = {"gpt-x": {"model": "gpt-x", "provider": "openai"}}
        assert _looks_nested(stored) is False

    def test_non_dict_value_means_not_nested(self):
        assert _looks_nested({"x": "scalar"}) is False


class TestLoadPresets:
    def test_nested_layout_loaded_with_provider_name_keys(self):
        save_yaml_store(
            {"presets": {"openai": {"my-gpt": {"model": "gpt-4o", "max_context": 100}}}}
        )
        presets = load_presets()
        assert ("openai", "my-gpt") in presets
        preset = presets[("openai", "my-gpt")]
        assert preset.model == "gpt-4o"
        assert preset.max_context == 100

    def test_legacy_flat_layout_loaded_with_inferred_provider(self):
        save_yaml_store(
            {
                "presets": {
                    "old-preset": {
                        "model": "gpt-4o",
                        "provider": "openai",
                    }
                }
            }
        )
        presets = load_presets()
        assert ("openai", "old-preset") in presets

    def test_flat_entry_without_resolvable_provider_dropped(self):
        save_yaml_store(
            {"presets": {"orphan": {"model": "x"}}}  # no provider, no base_url
        )
        presets = load_presets()
        assert presets == {}

    def test_legacy_profiles_block_merged_when_flat(self):
        # a flat legacy `profiles` block alongside a flat legacy `presets`
        # block — the documented merge path.
        save_yaml_store(
            {
                "presets": {"p1": {"model": "m", "provider": "openai"}},
                "profiles": {
                    "legacy": {"model": "gpt-4o", "provider": "openai"},
                },
            }
        )
        presets = load_presets()
        assert ("openai", "legacy") in presets

    def test_legacy_profiles_only_file_still_loads_presets(self):
        # the exact migration path the `profiles` fallback was written for:
        # an old file has a `profiles:` block and NO `presets:` key.
        save_yaml_store(
            {"profiles": {"legacy": {"model": "gpt-4o", "provider": "openai"}}}
        )
        presets = load_presets()
        assert ("openai", "legacy") in presets

    def test_missing_presets_block_returns_empty(self):
        save_yaml_store({"version": 3})
        assert load_presets() == {}

    def test_nested_layout_skips_non_dict_buckets_and_entries(self):
        save_yaml_store(
            {
                "presets": {
                    "openai": {
                        "good": {"model": "m"},
                        "junk": "not a dict",
                    },
                    "broken": "not a dict",
                }
            }
        )
        presets = load_presets()
        # only the well-formed entry survives the skip branches
        assert list(presets) == [("openai", "good")]

    def test_flat_layout_skips_non_dict_entries(self):
        save_yaml_store(
            {
                "presets": {
                    "good": {"model": "m", "provider": "openai"},
                    "junk": "not a dict",
                }
            }
        )
        presets = load_presets()
        assert list(presets) == [("openai", "good")]


class TestSerializeUserData:
    def test_version_always_written(self):
        data = serialize_user_data({}, {})
        assert data["version"] == 3

    def test_default_model_written_when_given(self):
        data = serialize_user_data({}, {}, default_model="gpt-x")
        assert data["default_model"] == "gpt-x"

    def test_builtin_backends_excluded_from_serialised_backends(self):
        builtin = LLMBackend(name="openai", backend_type="openai")
        custom = LLMBackend(name="myproxy", backend_type="openai", base_url="u")
        data = serialize_user_data({}, {"openai": builtin, "myproxy": custom})
        # only the custom backend is persisted; builtins are implicit
        assert set(data["backends"]) == {"myproxy"}

    def test_presets_serialised_in_nested_shape_without_provider_key(self):
        preset = LLMPreset(name="my-gpt", model="gpt-4o", provider="openai")
        data = serialize_user_data({("openai", "my-gpt"): preset}, {})
        assert "openai" in data["presets"]
        body = data["presets"]["openai"]["my-gpt"]
        assert body["model"] == "gpt-4o"
        # provider is implied by the nesting key, not duplicated in the body
        assert "provider" not in body

    def test_empty_presets_and_backends_omitted(self):
        data = serialize_user_data({}, {})
        assert "presets" not in data
        assert "backends" not in data

    def test_load_presets_round_trips_serialised_payload(self):
        preset = LLMPreset(name="rt", model="gpt-4o", provider="openai", max_output=999)
        save_yaml_store(serialize_user_data({("openai", "rt"): preset}, {}))
        loaded = load_presets()
        assert loaded[("openai", "rt")].max_output == 999
        assert loaded[("openai", "rt")].model == "gpt-4o"
