"""The Python half of the kt.ui expression conformance file (vitest runs the JS half)."""

import json
from pathlib import Path

import pytest

from kohakuterrarium.builtins.kt_ui.expr import ExprError, evaluate, interpolate

FIXTURE = Path(__file__).parents[3] / "fixtures" / "kt_ui_expressions.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = DATA["cases"]


def _label(case):
    return case.get("expr", case.get("template"))


@pytest.mark.parametrize("case", CASES, ids=[_label(c) for c in CASES])
def test_case(case):
    run = (
        (lambda: evaluate(case["expr"], DATA["scope"]))
        if "expr" in case
        else (lambda: interpolate(case["template"], DATA["scope"]))
    )
    if "error" in case:
        with pytest.raises(ExprError) as info:
            run()
        assert info.value.code == case["error"]
    else:
        assert run() == case["value"]


def test_every_error_code_is_covered():
    codes = {c["error"] for c in CASES if "error" in c}
    assert codes == {
        "syntax",
        "unknown_name",
        "unknown_function",
        "type",
        "zero_division",
        "domain",
        "arity",
        "limit",
    }
