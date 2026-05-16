"""Unit tests for :mod:`kohakuterrarium.prompt.framework_hints`.

Four prose blocks are spliced into every system prompt, each keyed by a
stable canonical key. Contract:

- ``get_framework_hint`` returns the override when one is present (even
  an empty string), else the built-in default, else ``None`` for a key
  that is not canonical at all.
- Unknown keys *inside* an override map are ignored (warned), never
  crash.
- ``merge_overrides`` lets creature-level entries win over package-level.
- ``canonical_keys`` enumerates exactly the four recognised keys.
"""

from kohakuterrarium.prompt.framework_hints import (
    HINT_EXECUTION_MODEL_DYNAMIC,
    HINT_EXECUTION_MODEL_NATIVE,
    HINT_EXECUTION_MODEL_STATIC,
    HINT_OUTPUT_MODEL,
    canonical_keys,
    get_framework_hint,
    merge_overrides,
)


class TestCanonicalKeys:
    def test_returns_exactly_the_four_keys(self):
        assert set(canonical_keys()) == {
            HINT_OUTPUT_MODEL,
            HINT_EXECUTION_MODEL_DYNAMIC,
            HINT_EXECUTION_MODEL_STATIC,
            HINT_EXECUTION_MODEL_NATIVE,
        }

    def test_key_constant_values_are_dotted_canonical_strings(self):
        assert HINT_OUTPUT_MODEL == "framework.output_model"
        assert HINT_EXECUTION_MODEL_DYNAMIC == "framework.execution_model.dynamic"
        assert HINT_EXECUTION_MODEL_STATIC == "framework.execution_model.static"
        assert HINT_EXECUTION_MODEL_NATIVE == "framework.execution_model.native"


class TestGetFrameworkHint:
    def test_default_output_model_contains_placeholder(self):
        # The default output-model block keeps {named_outputs_section}
        # for the aggregator to fill at render time.
        hint = get_framework_hint(HINT_OUTPUT_MODEL)
        assert "{named_outputs_section}" in hint
        assert "## Output Format" in hint

    def test_default_dynamic_execution_block(self):
        hint = get_framework_hint(HINT_EXECUTION_MODEL_DYNAMIC)
        assert "## Execution Model" in hint
        assert "Background Tasks" in hint

    def test_default_native_execution_block(self):
        hint = get_framework_hint(HINT_EXECUTION_MODEL_NATIVE)
        assert "native function calling" in hint
        assert "## Tool Usage" in hint

    def test_non_canonical_key_returns_none(self):
        assert get_framework_hint("framework.not_a_real_key") is None

    def test_override_present_wins_over_default(self):
        overrides = {HINT_OUTPUT_MODEL: "CUSTOM OUTPUT PROSE"}
        assert get_framework_hint(HINT_OUTPUT_MODEL, overrides) == "CUSTOM OUTPUT PROSE"

    def test_empty_string_override_is_honoured_not_skipped(self):
        # Empty string means "omit this block" — it must be returned as-is,
        # NOT fall through to the default.
        overrides = {HINT_EXECUTION_MODEL_STATIC: ""}
        assert get_framework_hint(HINT_EXECUTION_MODEL_STATIC, overrides) == ""

    def test_override_for_other_key_does_not_affect_this_key(self):
        overrides = {HINT_OUTPUT_MODEL: "X"}
        # Dynamic key has no override -> default returned.
        assert get_framework_hint(
            HINT_EXECUTION_MODEL_DYNAMIC, overrides
        ) == get_framework_hint(HINT_EXECUTION_MODEL_DYNAMIC)

    def test_unknown_key_in_override_map_is_ignored_not_fatal(self):
        # A bogus key alongside a valid one must not crash; valid key still resolves.
        overrides = {"framework.bogus": "junk", HINT_OUTPUT_MODEL: "GOOD"}
        assert get_framework_hint(HINT_OUTPUT_MODEL, overrides) == "GOOD"

    def test_empty_override_map_falls_through_to_default(self):
        assert get_framework_hint(HINT_OUTPUT_MODEL, {}) == get_framework_hint(
            HINT_OUTPUT_MODEL
        )


class TestMergeOverrides:
    def test_both_none_returns_empty_dict(self):
        assert merge_overrides(None, None) == {}

    def test_package_level_only(self):
        pkg = {HINT_OUTPUT_MODEL: "pkg"}
        assert merge_overrides(pkg, None) == {HINT_OUTPUT_MODEL: "pkg"}

    def test_creature_level_only(self):
        crt = {HINT_OUTPUT_MODEL: "crt"}
        assert merge_overrides(None, crt) == {HINT_OUTPUT_MODEL: "crt"}

    def test_creature_level_wins_for_same_key(self):
        pkg = {HINT_OUTPUT_MODEL: "pkg", HINT_EXECUTION_MODEL_STATIC: "pkg-static"}
        crt = {HINT_OUTPUT_MODEL: "crt"}
        merged = merge_overrides(pkg, crt)
        assert merged == {
            HINT_OUTPUT_MODEL: "crt",
            HINT_EXECUTION_MODEL_STATIC: "pkg-static",
        }

    def test_unknown_keys_preserved_for_later_warning(self):
        merged = merge_overrides({"framework.bogus": "x"}, None)
        assert merged == {"framework.bogus": "x"}

    def test_merge_does_not_mutate_inputs(self):
        pkg = {HINT_OUTPUT_MODEL: "pkg"}
        crt = {HINT_EXECUTION_MODEL_STATIC: "crt"}
        merge_overrides(pkg, crt)
        assert pkg == {HINT_OUTPUT_MODEL: "pkg"}
        assert crt == {HINT_EXECUTION_MODEL_STATIC: "crt"}
