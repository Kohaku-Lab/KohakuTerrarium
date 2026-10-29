"""kt.ui expression limits, helpers and number formatting (cases beyond the shared file)."""

import pytest

from kohakuterrarium.builtins.kt_ui.expr import (
    ExprError,
    evaluate,
    free_names,
    normalize,
    number_text,
    parse,
    template_expression,
    template_expressions,
    to_display,
    truthy,
)


def test_long_and_deep_expressions_are_refused():
    with pytest.raises(ExprError, match="longer than"):
        parse("1+" * 1100 + "1")
    with pytest.raises(ExprError, match="nests too deeply"):
        parse("(" * 70 + "1" + ")" * 70)
    with pytest.raises(ExprError, match="nests too deeply"):
        parse("not " * 70 + "1")
    with pytest.raises(ExprError) as info:
        parse(3)
    assert info.value.code == "syntax"


def test_step_limit_stops_runaway_evaluation():
    with pytest.raises(ExprError) as info:
        evaluate("sum(range(1000)) + sum(range(1000))", {}, max_steps=1500)
    assert info.value.code == "limit"


def test_non_ascii_names_are_a_syntax_error_not_a_hang():
    with pytest.raises(ExprError) as info:
        evaluate("é + 1", {})
    assert info.value.code == "syntax"
    with pytest.raises(ExprError):
        evaluate("٣ + 1", {})


def test_free_names_skip_functions_and_keys():
    node = parse("a + round(b.c, d[0]) + (e ? f : 'g') + [h]")
    assert free_names(node) == {"a", "b", "d", "e", "f", "h"}


@pytest.mark.parametrize(
    "value,text",
    [
        (0.0, "0"),
        (-0.0, "0"),
        (5.0, "5"),
        (-12.5, "-12.5"),
        (1e21, "1e+21"),
        (1.5e300, "1.5e+300"),
        (123456789012345680000.0, "123456789012345680000"),
        (1e-6, "0.000001"),
        (1.25e-7, "1.25e-7"),
        (0.1, "0.1"),
        (float("nan"), "NaN"),
        (float("inf"), "Infinity"),
        (float("-inf"), "-Infinity"),
    ],
)
def test_number_text_matches_javascript(value, text):
    assert number_text(value) == text


def test_display_truthiness_and_normalize():
    assert to_display({"a": [1, None, "x"]}) == '{"a":[1,null,"x"]}'
    assert to_display(False) == "false"
    assert truthy({}) is False and truthy({"a": 1}) is True and truthy(0.0) is False
    assert normalize((1, {"k": 2})) == [1.0, {"k": 2.0}]
    assert normalize(object()).startswith("<object")


def test_template_helpers():
    assert template_expression(" {{ a }} ") == " a "
    assert template_expression("{{ a }} {{ b }}") is None
    assert template_expression("x {{ a }}") is None
    assert template_expressions("{{a}}-{{ b }}") == ["a", " b "]
