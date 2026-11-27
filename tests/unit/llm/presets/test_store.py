"""Unit tests for ``llm/presets/store.py`` — preset YAML I/O + migration.

Behavior-first: assert the exact ``(provider, name)``-keyed presets read
from both the nested and legacy-flat YAML layouts, migration, provider-priority resolution, and the serialised payload
shape. ``PROFILES_PATH`` is redirected to a per-test tmp file.
"""

import sys

import pytest

from kohakuterrarium.errors import ConfigError
from kohakuterrarium.llm.profiles.backends import save_yaml_store
from kohakuterrarium.llm.profiles.resolve import (
    create_preset,
    list_all,
    set_default_model,
)
from kohakuterrarium.llm.presets.store import (
    load_presets,
    preset_from_data,
    serialize_user_data,
)
from kohakuterrarium.llm.profiles.types import LLMBackend, LLMPreset

from tests.unit._fakes.package_backend import echo_backend, install_backend_package


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


class TestLoadPresets:
    def test_valid_name_model_and_its_sibling_survive_later_creations(self):
        for name in ("custom", "model", "added-later"):
            create_preset(LLMPreset(name=name, provider="openai", model="remote-id"))
        assert set(load_presets()) == {
            ("openai", "custom"),
            ("openai", "model"),
            ("openai", "added-later"),
        }

    def test_current_and_legacy_presets_survive_rewrite_with_current_keys_winning(self):
        save_yaml_store(
            {
                "version": 3,
                "profiles": {
                    "older": {"provider": "openai", "model": "legacy-only"},
                    "both": {"provider": "openai", "model": "legacy-value"},
                },
                "presets": {
                    "openai": {"both": {"model": "current-value", "temperature": 0.25}},
                    "other": {"provider": "openai", "model": "flat-in-presets"},
                },
            }
        )
        set_default_model("openai/both")
        loaded = load_presets()
        assert {key: p.model for key, p in loaded.items()} == {
            ("openai", "both"): "current-value",
            ("openai", "older"): "legacy-only",
            ("openai", "other"): "flat-in-presets",
        }
        assert loaded[("openai", "both")].temperature == 0.25

    @pytest.mark.parametrize(
        "unreadable",
        [
            "malformed bucket",
            {"stray": {"model": "no-provider"}},
            {"openai": {"broken": "invalid entry"}},
        ],
    )
    def test_partial_migrations_never_discard_the_unreadable_record(
        self, tmp_path, unreadable
    ):
        save_yaml_store(
            {
                "presets": {"openai": {"visible": {"model": "working"}}},
                "profiles": unreadable,
            }
        )
        path = tmp_path / "llm_profiles.yaml"
        original = path.read_bytes()
        assert ("openai", "visible") in load_presets()
        with pytest.raises(ConfigError, match="llm_profiles.yaml"):
            create_preset(LLMPreset(name="new", model="new", provider="openai"))
        assert path.read_bytes() == original

    def test_failed_file_read_leaves_store_intact_and_is_reported_by_inventory(
        self, tmp_path, monkeypatch
    ):
        save_yaml_store(
            {"presets": {"openai": {"manual": {"model": "private-model-name"}}}}
        )
        path = tmp_path / "llm_profiles.yaml"
        original = path.read_bytes()
        read_text = type(path).read_text

        def deny_profile_read(file, *args, **kwargs):
            if file == path:
                raise PermissionError("private-model-name")
            return read_text(file, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(type(path), "read_text", deny_profile_read)
            for action in (list_all, lambda: set_default_model("openai/manual")):
                with pytest.raises(
                    ConfigError, match="Cannot read llm_profiles.yaml"
                ) as caught:
                    action()
                assert "private-model-name" not in str(caught.value)
        assert path.read_bytes() == original
        assert ("openai", "manual") in load_presets()

    @pytest.mark.parametrize(
        "content",
        [
            "presets:\n  openai:\n    hand-a: {model: a}\n  openai:\n    hand-b: {model: b}\n",
            "presets: {openai: {hand-b: {model: a}, hand-b: {model: b}}}",
            "presets: {openai: {hand-b: {model: b, extra_body: {flag: a, flag: b}}}}",
            "presets: {openai: {hand-b: {model: b, extra_body: {true: a, yes: b}}}}",
        ],
    )
    def test_duplicate_keys_refuse_inventory_and_rewrite(self, tmp_path, content):
        path = tmp_path / "llm_profiles.yaml"
        path.write_bytes(content.encode())
        for action in (list_all, lambda: set_default_model("openai/hand-b")):
            with pytest.raises(ConfigError, match="Duplicate mapping key .*line"):
                action()
        assert path.read_bytes() == content.encode()

    def test_invalid_typed_yaml_key_still_reports_a_config_error(self, tmp_path):
        path = tmp_path / "llm_profiles.yaml"
        original = b"presets: {!!seq invalid: value}"
        path.write_bytes(original)
        with pytest.raises(ConfigError, match="Cannot read llm_profiles.yaml"):
            set_default_model("openai/example")
        assert path.read_bytes() == original

    def test_yaml_merges_allow_explicit_overrides_and_multiple_anchors(self, tmp_path):
        path = tmp_path / "llm_profiles.yaml"
        path.write_text(
            "presets:\n  openai:\n"
            "    first: &first {model: a, max_context: 10000}\n"
            "    second: &second {<<: *first, model: b, temperature: 0.25}\n"
            "    shared: {<<: [*second, *first], model: c}\n",
            encoding="utf-8",
        )
        set_default_model("openai/shared")
        presets = load_presets()
        assert {k: p.model for k, p in presets.items()} == {
            ("openai", "first"): "a",
            ("openai", "second"): "b",
            ("openai", "shared"): "c",
        }
        shared = presets[("openai", "shared")]
        assert (shared.max_context, shared.temperature) == (10000, 0.25)

    def test_ambiguous_legacy_entry_does_not_erase_nested_preset(self, tmp_path):
        path = tmp_path / "llm_profiles.yaml"
        data = {
            "presets": {
                "openai": {
                    "model": "legacy",
                    "provider": "openai",
                    "manual": {"model": "mine"},
                }
            }
        }
        save_yaml_store(data)
        original = path.read_bytes()
        with pytest.raises(ConfigError, match="presets.openai.manual"):
            set_default_model("openai/openai")
        assert path.read_bytes() == original

    def test_model_keys_inside_legacy_preset_fields_survive_migration(self):
        extra = {"model": "sent-upstream", "custom": {"model": "another"}}
        variations = {"style": {"chat": {"model": "other-model"}}}
        save_yaml_store(
            {
                "profiles": {
                    "old": {
                        "model": "m",
                        "provider": "openai",
                        "extra_body": extra,
                        "variation_groups": variations,
                    }
                }
            }
        )
        set_default_model("openai/old")
        saved = load_presets()[("openai", "old")]
        assert (saved.extra_body, saved.variation_groups) == (extra, variations)

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

    def test_unchanged_package_backend_is_not_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "path", list(sys.path))
        install_backend_package(tmp_path, "p", [echo_backend()])
        package_record = LLMBackend(
            name="echo", backend_type="echo", provider_name="echo"
        )
        edited = LLMBackend(
            name="echo", backend_type="echo", provider_name="echo", base_url="u"
        )
        assert "backends" not in serialize_user_data({}, {"echo": package_record})
        data = serialize_user_data({}, {"echo": edited})
        assert data["backends"]["echo"]["base_url"] == "u"

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

    def test_legacy_context_group_is_folded_on_load_and_written_back_unchanged(self):
        legacy = {
            "model": "claude-x",
            "max_context": 200000,
            "max_output": 65536,
            "variation_groups": {
                "context": {
                    "200k": {"max_context": 200000},
                    "long": {"max_context": 1000000},
                }
            },
        }
        save_yaml_store({"version": 3, "presets": {"anthropic": {"cc": legacy}}})
        loaded = load_presets()
        preset = loaded[("anthropic", "cc")]
        assert preset.variation_groups == {}
        assert preset.context_window == 1000000
        assert preset.context_aliases == {"long": 1000000}

        # Saving any preset writes this one back as the user wrote it.
        other = LLMPreset(name="o", model="gpt-4o", provider="openai")
        data = serialize_user_data({**loaded, ("openai", "o"): other}, {})
        assert data["presets"]["anthropic"]["cc"] == legacy
        save_yaml_store(data)
        assert load_presets()[("anthropic", "cc")] == preset

    def test_named_subagent_models_are_serialised_without_normalisation(self):
        mapping = {"researcher": "anthropic/claude-sonnet", "Reviewer": "raw-model"}
        data = serialize_user_data({}, {}, subagent_models=mapping)
        assert data["subagent_models"] == mapping

    def test_child_models_keep_exact_keys_and_drop_blank_values(self):
        mapping = {
            "default": "openrouter/cheap",
            "@biome/creatures/swe": "codex/gpt",
            "blank": "  ",
            "": "openai/missing",
        }
        data = serialize_user_data({}, {}, child_models=mapping)
        assert data["child_models"] == {
            "default": "openrouter/cheap",
            "@biome/creatures/swe": "codex/gpt",
        }
