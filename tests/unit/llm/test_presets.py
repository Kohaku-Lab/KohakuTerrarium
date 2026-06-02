"""Unit tests for ``llm/presets.py`` — built-in preset data + nested view.

Behavior-first: assert the exact contents/shape of the canonical preset
view, preset data integrity (every preset names a provider + model and
its variation_groups patch only allowed roots), and alias resolution.

``presets`` has module-global caches (``_all_presets_cache`` /
``_package_presets_merged``); the ``reset_preset_caches`` fixture clears
them around each test so cache state never leaks.
"""

import pytest

from kohakuterrarium.llm import presets as presets_mod
from kohakuterrarium.llm.presets import (
    PRESETS,
    get_all_presets,
    iter_all_presets,
    resolve_alias,
)
from kohakuterrarium.llm.presets import _canonical_entry
from kohakuterrarium.llm.preset_aliases import _CANONICAL_NAMES, ALIASES
from kohakuterrarium.llm.variations import _ALLOWED_VARIATION_ROOTS


@pytest.fixture(autouse=True)
def reset_preset_caches():
    """Clear module-global preset caches before AND after each test."""
    presets_mod._all_presets_cache = None
    presets_mod._package_presets_merged = False
    yield
    presets_mod._all_presets_cache = None
    presets_mod._package_presets_merged = False


# ---------------------------------------------------------------------------
# _canonical_entry
# ---------------------------------------------------------------------------


class TestCanonicalEntry:
    def test_strips_provider_and_maps_canonical_name(self):
        result = _canonical_entry(
            "gpt-5.4-api", {"provider": "openai", "model": "gpt-5.4", "max_context": 1}
        )
        assert result == ("openai", "gpt-5.4", {"model": "gpt-5.4", "max_context": 1})

    def test_keeps_key_as_name_when_not_in_canonical_map(self):
        result = _canonical_entry("grok-4", {"provider": "openrouter", "model": "m"})
        assert result == ("openrouter", "grok-4", {"model": "m"})

    def test_missing_provider_returns_none(self):
        assert _canonical_entry("x", {"model": "m"}) is None

    def test_empty_provider_returns_none(self):
        assert _canonical_entry("x", {"provider": "", "model": "m"}) is None


# ---------------------------------------------------------------------------
# get_all_presets / iter_all_presets
# ---------------------------------------------------------------------------


class TestGetAllPresets:
    def test_keyed_by_provider_name_tuple(self):
        allp = get_all_presets()
        # the codex gpt-5.4 preset (key 'gpt-5.4') keeps its bare name
        assert ("codex", "gpt-5.4") in allp
        # the -api variant collapses to bare 'gpt-5.4' under openai
        assert ("openai", "gpt-5.4") in allp
        # the -or variant collapses to bare 'gpt-5.4' under openrouter
        assert ("openrouter", "gpt-5.4") in allp

    def test_entry_values_drop_the_provider_key(self):
        allp = get_all_presets()
        codex_entry = allp[("codex", "gpt-5.4")]
        assert "provider" not in codex_entry
        assert codex_entry["model"] == "gpt-5.4"
        assert codex_entry["max_context"] == 400000

    def test_or_variant_has_distinct_model_id(self):
        allp = get_all_presets()
        assert allp[("openrouter", "gpt-5.4")]["model"] == "openai/gpt-5.4"
        assert allp[("codex", "gpt-5.4")]["model"] == "gpt-5.4"

    def test_result_is_cached(self):
        first = get_all_presets()
        second = get_all_presets()
        assert first is second

    def test_iter_all_presets_matches_get_all_presets(self):
        as_list = iter_all_presets()
        as_dict = get_all_presets()
        assert len(as_list) == len(as_dict)
        for provider, name, data in as_list:
            assert as_dict[(provider, name)] == data

    def test_every_builtin_preset_with_provider_is_present(self):
        allp = get_all_presets()
        for key, data in PRESETS.items():
            provider = data.get("provider")
            if not provider:
                continue
            canonical = _CANONICAL_NAMES.get(key, key)
            assert (provider, canonical) in allp


# ---------------------------------------------------------------------------
# resolve_alias
# ---------------------------------------------------------------------------


class TestResolveAlias:
    def test_short_friendly_alias(self):
        assert resolve_alias("opus") == ("anthropic", "claude-opus-4.7")
        assert resolve_alias("gpt5") == ("codex", "gpt-5.4")
        assert resolve_alias("kimi-code") == ("kimi-code", "kimi-for-coding")
        assert resolve_alias("glm-coding") == ("glm-coding", "glm-5.1")

    def test_legacy_suffixed_alias(self):
        assert resolve_alias("gpt-5.4-or") == ("openrouter", "gpt-5.4")
        assert resolve_alias("claude-opus-4.6-direct") == (
            "anthropic",
            "claude-opus-4.6",
        )

    def test_unknown_name_returns_none(self):
        assert resolve_alias("not-an-alias") is None

    def test_canonical_name_is_not_an_alias(self):
        # a bare canonical name is NOT itself an alias entry
        assert resolve_alias("grok-4") is None


# ---------------------------------------------------------------------------
# PRESETS data integrity
# ---------------------------------------------------------------------------


class TestPresetsDataIntegrity:
    def test_every_preset_has_provider_and_model(self):
        for name, data in PRESETS.items():
            assert data.get("provider"), f"{name} missing provider"
            assert data.get("model"), f"{name} missing model"

    def test_every_preset_max_context_is_positive_int(self):
        for name, data in PRESETS.items():
            mc = data.get("max_context")
            assert isinstance(mc, int) and mc > 0, f"{name} bad max_context"

    def test_variation_group_patches_only_target_allowed_roots(self):
        # variations.py rejects patches outside _ALLOWED_VARIATION_ROOTS at
        # apply time — every built-in preset must already comply.
        for name, data in PRESETS.items():
            groups = data.get("variation_groups", {})
            for group_name, options in groups.items():
                for option_name, patch in options.items():
                    for path in patch:
                        root = path.split(".", 1)[0]
                        assert root in _ALLOWED_VARIATION_ROOTS, (
                            f"{name}/{group_name}/{option_name}: "
                            f"patch '{path}' targets disallowed root '{root}'"
                        )

    def test_canonical_names_map_to_real_preset_keys(self):
        # every key in _CANONICAL_NAMES must be a real PRESETS key
        for legacy_key in _CANONICAL_NAMES:
            assert legacy_key in PRESETS, f"{legacy_key} not in PRESETS"

    def test_aliases_resolve_to_existing_canonical_presets(self):
        # every alias target (provider, name) must exist in the nested view
        allp = get_all_presets()
        for alias, (provider, name) in ALIASES.items():
            assert (
                provider,
                name,
            ) in allp, f"alias '{alias}' -> ({provider}, {name}) not in preset view"

    def test_codex_presets_use_codex_provider(self):
        # the headline ChatGPT-subscription presets bind to the codex provider
        assert PRESETS["gpt-5.4"]["provider"] == "codex"
        assert PRESETS["gpt-5.5"]["provider"] == "codex"

    def test_anthropic_direct_presets_use_anthropic_provider(self):
        assert PRESETS["claude-opus-4.7"]["provider"] == "anthropic"
        assert PRESETS["claude-opus-4.7"]["model"] == "claude-opus-4-7"

    def test_kimi_code_direct_preset_uses_kimi_code_provider(self):
        preset = PRESETS["kimi-for-coding"]
        assert preset["provider"] == "kimi-code"
        assert preset["model"] == "kimi-for-coding"
        assert preset["max_context"] == 262144
        assert preset["max_output"] == 32768

    def test_glm_coding_direct_presets_use_bearer_auth(self):
        expected = {
            "glm-5.1": ("GLM-5.1", 204800, 131072),
            "glm-5-turbo": ("GLM-5-Turbo", 204800, 131072),
            "glm-4.7": ("GLM-4.7", 204800, 131072),
            "glm-4.5-air": ("GLM-4.5-Air", 131072, 98304),
        }
        for name, (model, max_context, max_output) in expected.items():
            preset = PRESETS[name]
            assert preset["provider"] == "glm-coding"
            assert preset["model"] == model
            assert preset["max_context"] == max_context
            assert preset["max_output"] == max_output
            assert preset["extra_body"]["auth_as_bearer"] is True

    def test_glm_coding_legacy_suffixed_aliases_resolve(self):
        assert resolve_alias("glm-5.1-coding") == ("glm-coding", "glm-5.1")
        assert resolve_alias("glm-4.7-coding") == ("glm-coding", "glm-4.7")


# ---------------------------------------------------------------------------
# Package preset merging
# ---------------------------------------------------------------------------


class TestPackagePresetMerge:
    def test_package_preset_added_under_its_provider(self, monkeypatch):
        monkeypatch.setattr(
            presets_mod,
            "list_packages",
            lambda: [
                {
                    "name": "pkg-a",
                    "llm_presets": [
                        {
                            "name": "fancy-model",
                            "provider": "my-provider",
                            "model": "fancy-1",
                            "max_context": 5000,
                        }
                    ],
                }
            ],
        )
        allp = get_all_presets()
        assert ("my-provider", "fancy-model") in allp
        entry = allp[("my-provider", "fancy-model")]
        # provider + name stripped from the stored body
        assert entry == {"model": "fancy-1", "max_context": 5000}

    def test_package_preset_without_name_or_provider_skipped(self, monkeypatch):
        monkeypatch.setattr(
            presets_mod,
            "list_packages",
            lambda: [
                {
                    "name": "pkg-b",
                    "llm_presets": [
                        {"provider": "p", "model": "m"},  # no name
                        {"name": "n", "model": "m"},  # no provider
                        "not-a-dict",
                    ],
                }
            ],
        )
        allp = get_all_presets()
        # nothing from pkg-b made it in
        assert ("p", "n") not in allp

    def test_package_preset_does_not_override_builtin(self, monkeypatch):
        monkeypatch.setattr(
            presets_mod,
            "list_packages",
            lambda: [
                {
                    "name": "pkg-c",
                    "llm_presets": [
                        {
                            "name": "gpt-5.4",
                            "provider": "codex",
                            "model": "HIJACKED",
                        }
                    ],
                }
            ],
        )
        allp = get_all_presets()
        # built-in (codex, gpt-5.4) stays untouched
        assert allp[("codex", "gpt-5.4")]["model"] == "gpt-5.4"

    def test_duplicate_package_preset_first_wins(self, monkeypatch):
        monkeypatch.setattr(
            presets_mod,
            "list_packages",
            lambda: [
                {
                    "name": "pkg-d",
                    "llm_presets": [
                        {"name": "dupe", "provider": "px", "model": "first"},
                        {"name": "dupe", "provider": "px", "model": "second"},
                    ],
                }
            ],
        )
        allp = get_all_presets()
        assert allp[("px", "dupe")]["model"] == "first"

    def test_package_scan_failure_is_swallowed(self, monkeypatch):
        def _boom():
            raise RuntimeError("package scan exploded")

        monkeypatch.setattr(presets_mod, "list_packages", _boom)
        # built-in presets still resolve despite the package scan failing
        allp = get_all_presets()
        assert ("codex", "gpt-5.4") in allp

    def test_merge_runs_only_once(self, monkeypatch):
        calls = []

        def _tracking():
            calls.append(1)
            return []

        monkeypatch.setattr(presets_mod, "list_packages", _tracking)
        presets_mod._merge_package_presets()
        # second call short-circuits on the _package_presets_merged guard
        assert presets_mod._merge_package_presets() == {}
        assert len(calls) == 1
