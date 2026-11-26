"""Unit tests for ``llm/profiles/context.py`` — context sizes and legacy groups.

A ``context`` variation group whose every option only sets ``max_context`` is
the legacy spelling of the numeric ``context=<n>`` selection. These tests pin
which groups count as legacy, and what folding one yields.
"""

import pytest

from kohakuterrarium.llm.profiles.context import (
    fold_legacy_context,
    legacy_context_values,
)

# The shape kt-claude-code and older user presets still ship.
CC_CONTEXT = {
    "200k": {"max_context": 200000},
    "400k": {"max_context": 400000},
    "700k": {"max_context": 700000},
    "1m": {"max_context": 1000000},
}
REASONING = {"high": {"reasoning_effort": "high"}}


class TestLegacyContextValues:
    def test_max_context_only_options_map_to_their_numbers(self):
        assert legacy_context_values(CC_CONTEXT) == {
            "200k": 200000,
            "400k": 400000,
            "700k": 700000,
            "1m": 1000000,
        }

    @pytest.mark.parametrize(
        "options",
        [
            {"1m": {"max_context": 1000000, "max_output": 64000}},
            {"1m": {"max_context": 1000000}, "fast": {"temperature": 0.1}},
            {"1m": {}},
            {"1m": {"max_context": "1m"}},
            {"1m": {"max_context": 0}},
            {"1m": {"max_context": True}},
            {"1m": None},
            {},
            None,
            ["1m"],
        ],
    )
    def test_anything_else_is_not_legacy(self, options):
        assert legacy_context_values(options) is None


class TestFoldLegacyContext:
    def test_folds_out_the_group_and_derives_the_window(self):
        groups = {"context": CC_CONTEXT, "reasoning": REASONING}
        rest, window, aliases = fold_legacy_context(groups, None, {})
        assert rest == {"reasoning": REASONING}
        assert window == 1000000
        # Every option name already parses to its own number.
        assert aliases == {}
        # The input is left untouched.
        assert "context" in groups

    def test_a_set_window_is_kept(self):
        _rest, window, _aliases = fold_legacy_context(
            {"context": CC_CONTEXT}, 1048576, {}
        )
        assert window == 1048576

    def test_names_that_are_not_sizes_become_aliases(self):
        options = {"short": {"max_context": 128000}, "1m": {"max_context": 1000000}}
        _rest, _window, aliases = fold_legacy_context({"context": options}, None, {})
        assert aliases == {"short": 128000}

    def test_a_size_name_mapped_to_another_number_keeps_the_group_number(self):
        options = {"1m": {"max_context": 1048576}, "200k": {"max_context": 200000}}
        _rest, window, aliases = fold_legacy_context({"context": options}, None, {})
        assert aliases == {"1m": 1048576}
        assert window == 1048576

    def test_group_numbers_win_over_given_aliases(self):
        options = {"long": {"max_context": 900000}}
        _rest, _window, aliases = fold_legacy_context(
            {"context": options}, None, {"long": 1, "extra": 5}
        )
        assert aliases == {"long": 900000, "extra": 5}

    def test_mixed_context_group_is_not_folded(self):
        options = {"1m": {"max_context": 1000000, "extra_body.beta": "ctx-1m"}}
        assert fold_legacy_context({"context": options}, None, {}) is None

    def test_no_context_group_is_not_folded(self):
        assert fold_legacy_context({"reasoning": REASONING}, None, {}) is None
        assert fold_legacy_context({}, None, {}) is None
