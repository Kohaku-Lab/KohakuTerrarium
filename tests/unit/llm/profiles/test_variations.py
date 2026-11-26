"""Unit tests for ``llm/profiles/variations.py`` — variation-selector machinery.

Behavior-first: assert the exact parsed name+selections, shorthand
disambiguation against a preset's groups, dotted-path patch application,
the disallowed-root and cross-group-collision rejections, and the
deep-merge layering rule.
"""

import pytest

from kohakuterrarium.llm.profiles.types import LLMPreset
from kohakuterrarium.llm.profiles.variations import (
    apply_patch_map,
    apply_variation_groups,
    deep_merge_dicts,
    format_context_size,
    normalize_variation_selections,
    parse_context_size,
    parse_variation_selector,
)


class TestParseVariationSelector:
    def test_no_at_sign_returns_name_and_empty_selections(self):
        assert parse_variation_selector("gpt-x") == ("gpt-x", {})

    def test_group_equals_option_parsed(self):
        name, sels = parse_variation_selector("gpt-x@speed=fast")
        assert name == "gpt-x"
        assert sels == {"speed": "fast"}

    def test_multiple_selections_comma_separated(self):
        _name, sels = parse_variation_selector("gpt-x@a=1,b=2")
        assert sels == {"a": "1", "b": "2"}

    def test_bare_shorthand_stored_under_internal_key(self):
        _name, sels = parse_variation_selector("gpt-x@fast")
        assert sels == {"__option__": "fast"}

    def test_missing_name_before_at_raises(self):
        with pytest.raises(ValueError, match="missing a preset/model name"):
            parse_variation_selector("@fast")

    def test_empty_selector_after_at_raises(self):
        with pytest.raises(ValueError, match="is empty"):
            parse_variation_selector("gpt-x@   ")

    def test_empty_comma_part_raises(self):
        with pytest.raises(ValueError, match="empty variation selection"):
            parse_variation_selector("gpt-x@a=1,,b=2")

    def test_blank_group_or_option_raises(self):
        with pytest.raises(ValueError, match="Invalid variation selection"):
            parse_variation_selector("gpt-x@=1")

    def test_two_bare_shorthands_rejected(self):
        with pytest.raises(ValueError, match="only specify one option"):
            parse_variation_selector("gpt-x@fast,slow")


class TestApplyPatchMap:
    def test_dotted_path_creates_nested_structure(self):
        out = apply_patch_map({}, {"extra_body.reasoning.effort": "high"})
        assert out == {"extra_body": {"reasoning": {"effort": "high"}}}

    def test_base_not_mutated(self):
        base = {"temperature": 0.5}
        apply_patch_map(base, {"temperature": 0.9})
        assert base == {"temperature": 0.5}

    def test_disallowed_root_rejected(self):
        with pytest.raises(ValueError, match="Unsupported variation patch target"):
            apply_patch_map({}, {"model": "gpt-y"})

    def test_collision_with_non_object_intermediate_rejected(self):
        with pytest.raises(ValueError, match="is not an object"):
            apply_patch_map({"temperature": 0.5}, {"temperature.nested": 1})

    def test_empty_patch_returns_deep_copy(self):
        base = {"extra_body": {"a": 1}}
        out = apply_patch_map(base, {})
        assert out == base
        assert out is not base


class TestNormalizeVariationSelections:
    def _preset(self):
        return LLMPreset(
            name="p",
            model="m",
            variation_groups={
                "speed": {"fast": {"temperature": 0.2}, "slow": {"temperature": 0.9}},
                "depth": {"deep": {"reasoning_effort": "high"}},
            },
        )

    def test_explicit_group_option_validated_and_returned(self):
        out = normalize_variation_selections({"speed": "fast"}, self._preset())
        assert out == {"speed": "fast"}

    def test_unknown_group_rejected(self):
        with pytest.raises(ValueError, match="Unknown variation group"):
            normalize_variation_selections({"bogus": "x"}, self._preset())

    def test_unknown_option_rejected(self):
        with pytest.raises(ValueError, match="Unknown variation option"):
            normalize_variation_selections({"speed": "warp"}, self._preset())

    def test_shorthand_resolved_to_its_unique_group(self):
        out = normalize_variation_selections({"__option__": "deep"}, self._preset())
        assert out == {"depth": "deep"}

    def test_shorthand_with_no_matching_group_rejected(self):
        with pytest.raises(ValueError, match="Unknown variation option"):
            normalize_variation_selections({"__option__": "ghost"}, self._preset())

    def test_ambiguous_shorthand_rejected(self):
        preset = LLMPreset(
            name="p",
            model="m",
            variation_groups={
                "g1": {"shared": {"temperature": 0.1}},
                "g2": {"shared": {"temperature": 0.2}},
            },
        )
        with pytest.raises(ValueError, match="Ambiguous variation option"):
            normalize_variation_selections({"__option__": "shared"}, preset)


class TestApplyVariationGroups:
    def test_selected_option_patches_applied(self):
        groups = {"speed": {"fast": {"temperature": 0.2}}}
        out = apply_variation_groups({"temperature": 0.7}, groups, {"speed": "fast"})
        assert out["temperature"] == 0.2

    def test_two_groups_apply_independently(self):
        groups = {
            "speed": {"fast": {"temperature": 0.2}},
            "depth": {"deep": {"reasoning_effort": "high"}},
        }
        out = apply_variation_groups({}, groups, {"speed": "fast", "depth": "deep"})
        assert out == {"temperature": 0.2, "reasoning_effort": "high"}

    def test_cross_group_path_collision_rejected(self):
        groups = {
            "g1": {"a": {"temperature": 0.1}},
            "g2": {"b": {"temperature": 0.2}},
        }
        with pytest.raises(ValueError, match="conflict on 'temperature'"):
            apply_variation_groups({}, groups, {"g1": "a", "g2": "b"})

    def test_base_not_mutated(self):
        base = {"temperature": 0.7}
        apply_variation_groups(base, {"s": {"f": {"temperature": 0.1}}}, {"s": "f"})
        assert base == {"temperature": 0.7}


class TestDeepMergeDicts:
    def test_nested_dicts_merged_recursively(self):
        base = {"extra_body": {"a": 1, "b": 2}}
        override = {"extra_body": {"b": 99, "c": 3}}
        assert deep_merge_dicts(base, override) == {
            "extra_body": {"a": 1, "b": 99, "c": 3}
        }

    def test_non_dict_override_replaces_value(self):
        assert deep_merge_dicts({"k": {"x": 1}}, {"k": "scalar"}) == {"k": "scalar"}

    def test_inputs_not_mutated(self):
        base = {"k": {"x": 1}}
        override = {"k": {"y": 2}}
        deep_merge_dicts(base, override)
        assert base == {"k": {"x": 1}}
        assert override == {"k": {"y": 2}}

    def test_empty_override_returns_copy_of_base(self):
        base = {"k": 1}
        out = deep_merge_dicts(base, {})
        assert out == base and out is not base


class TestContextSelection:
    """``context=<n>`` sets ``max_context`` on any preset, no group needed."""

    @pytest.mark.parametrize(
        "text,tokens",
        [
            ("500000", 500_000),
            ("272k", 272_000),
            ("272K", 272_000),
            ("1m", 1_000_000),
            ("1M", 1_000_000),
            ("1.5m", 1_500_000),
            ("0.5k", 500),
            (" 64k ", 64_000),
        ],
    )
    def test_sizes_parse_to_whole_tokens(self, text, tokens):
        assert parse_context_size(text) == tokens

    @pytest.mark.parametrize(
        "text", ["", "big", "-5k", "0", "0k", "1.0005k", "1g", "k", "1e6", "1,000"]
    )
    def test_invalid_sizes_raise_value_error(self, text):
        with pytest.raises(ValueError, match="Invalid context size"):
            parse_context_size(text)

    @pytest.mark.parametrize(
        "tokens,text",
        [(1_000_000, "1m"), (272_000, "272k"), (1_500_000, "1500k"), (1234, "1234")],
    )
    def test_canonical_form_is_compact_and_reparses(self, tokens, text):
        assert format_context_size(tokens) == text
        assert parse_context_size(text) == tokens

    def _preset(self, groups=None):
        return LLMPreset(name="p", model="m", variation_groups=groups or {})

    def test_normalize_accepts_context_without_a_group(self):
        assert normalize_variation_selections({"context": "500K"}, self._preset()) == {
            "context": "500k"
        }

    def test_normalize_canonicalizes_plain_numbers(self):
        assert normalize_variation_selections(
            {"context": "1000000"}, self._preset()
        ) == {"context": "1m"}

    def test_normalize_rejects_bad_context_size(self):
        with pytest.raises(ValueError, match="Invalid context size 'huge'"):
            normalize_variation_selections({"context": "huge"}, self._preset())

    def test_context_combines_with_group_selections(self):
        preset = self._preset({"reasoning": {"high": {"reasoning_effort": "high"}}})
        normalized = normalize_variation_selections(
            {"reasoning": "high", "context": "500k"}, preset
        )
        assert normalized == {"reasoning": "high", "context": "500k"}
        resolved = apply_variation_groups(
            {"max_context": 1}, preset.variation_groups, normalized
        )
        assert resolved == {"max_context": 500_000, "reasoning_effort": "high"}

    def test_shorthand_size_falls_back_to_context(self):
        preset = self._preset({"reasoning": {"high": {"reasoning_effort": "high"}}})
        assert normalize_variation_selections({"__option__": "1m"}, preset) == {
            "context": "1m"
        }

    def test_shorthand_typo_still_rejected(self):
        with pytest.raises(ValueError, match="Unknown variation option 'hgih'"):
            normalize_variation_selections({"__option__": "hgih"}, self._preset())

    def test_apply_sets_max_context_without_a_group(self):
        assert apply_variation_groups({"max_context": 1}, {}, {"context": "1m"}) == {
            "max_context": 1_000_000
        }

    def test_a_preset_context_group_keeps_its_own_options(self):
        # A ``context`` group that patches more than ``max_context`` stays named.
        small = {"max_context": 32000, "max_output": 4000}
        preset = self._preset({"context": {"small": small}})
        normalized = normalize_variation_selections({"context": "small"}, preset)
        assert normalized == {"context": "small"}
        assert apply_variation_groups({}, preset.variation_groups, normalized) == small
        # Numbers still work next to the named options.
        assert apply_variation_groups(
            {}, preset.variation_groups, {"context": "64k"}
        ) == {"max_context": 64000}

    def test_context_conflicts_with_a_group_that_writes_max_context(self):
        groups = {"window": {"wide": {"max_context": 9}}}
        with pytest.raises(ValueError, match="conflict on 'max_context'"):
            apply_variation_groups({}, groups, {"window": "wide", "context": "1m"})


class TestFoldedContextGroups:
    """A folded legacy ``context`` group resolves through the numeric path."""

    def _preset(self, options):
        return LLMPreset(
            name="cc",
            model="m",
            variation_groups={"context": options, "reasoning": {"high": {}}},
        )

    def _resolve(self, preset, selections):
        normalized = normalize_variation_selections(selections, preset)
        resolved = apply_variation_groups(
            {"max_context": 1}, preset.variation_groups, normalized
        )
        return normalized, resolved["max_context"]

    def test_size_names_resolve_by_parsing(self):
        preset = self._preset(
            {"200k": {"max_context": 200000}, "1m": {"max_context": 1000000}}
        )
        assert self._resolve(preset, {"context": "1m"}) == ({"context": "1m"}, 10**6)
        assert self._resolve(preset, {"context": "200k"}) == (
            {"context": "200k"},
            200000,
        )
        # Any other number works as on a preset without the group.
        assert self._resolve(preset, {"context": "300k"})[1] == 300000

    def test_a_non_size_name_resolves_to_the_group_number(self):
        preset = self._preset({"long": {"max_context": 1000000}})
        assert self._resolve(preset, {"context": "long"}) == (
            {"context": "1m"},
            1000000,
        )
        # The bare shorthand finds it too.
        assert self._resolve(preset, {"__option__": "long"}) == (
            {"context": "1m"},
            1000000,
        )

    def test_a_size_name_mapped_to_another_number_uses_the_group_number(self):
        preset = self._preset({"1m": {"max_context": 1048576}})
        normalized, tokens = self._resolve(preset, {"context": "1m"})
        assert tokens == 1048576
        # The selection is stored as the number, so it re-resolves without the map.
        assert normalized == {"context": "1048576"}
        bare = LLMPreset(name="cc", model="m")
        assert self._resolve(bare, normalized)[1] == 1048576
        assert self._resolve(preset, {"__option__": "1m"})[1] == 1048576

    def test_an_unknown_name_is_still_rejected(self):
        preset = self._preset({"long": {"max_context": 1000000}})
        with pytest.raises(ValueError, match="Invalid context size 'huge'"):
            normalize_variation_selections({"context": "huge"}, preset)
