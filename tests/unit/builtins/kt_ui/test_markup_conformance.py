"""kt.ui inline markup: the shared conformance file, run against the Python side."""

import json
from pathlib import Path

import pytest

from kohakuterrarium.builtins.kt_ui.markup import (
    MarkupError,
    convert_block,
    split_message,
)
from kohakuterrarium.builtins.kt_ui.spec import validate_spec

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "kt_ui_markup.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", DATA["split"], ids=[c["name"] for c in DATA["split"]])
def test_split(case):
    assert split_message(case["text"]) == case["segments"]


@pytest.mark.parametrize(
    "case", DATA["convert"], ids=[c["name"] for c in DATA["convert"]]
)
def test_convert(case):
    if "error" in case:
        with pytest.raises(MarkupError) as info:
            convert_block(case["source"])
        assert case["error"] in str(info.value)
    else:
        assert convert_block(case["source"]) == case["spec"]


def test_every_converted_fixture_spec_passes_the_validator():
    for case in DATA["convert"]:
        if "spec" in case:
            validate_spec(case["spec"])
