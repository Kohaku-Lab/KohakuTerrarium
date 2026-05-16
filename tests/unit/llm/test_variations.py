"""Unit tests for ``llm/variations.py`` — variation-selector machinery.

Behavior-first: assert the exact parsed name+selections, shorthand
disambiguation against a preset's groups, dotted-path patch application,
the disallowed-root and cross-group-collision rejections, and the
deep-merge layering rule.
"""

import pytest

from kohakuterrarium.llm.profile_types import LLMPreset
from kohakuterrarium.llm.variations import (
    apply_patch_map,
    apply_variation_groups,
    deep_merge_dicts,
    normalize_variation_selections,
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
