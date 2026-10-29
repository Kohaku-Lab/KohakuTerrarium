"""kt.ui spec validation, generated JSON schema, scope building."""

import json

import pytest

from kohakuterrarium.builtins.kt_ui.spec import (
    MAX_SPEC_BYTES,
    SpecError,
    build_scope,
    json_schema,
    validate_spec,
)

BILL = {
    "title": "Bill {{ people }}",
    "state": {"total": 120, "people": 3, "tip": 10, "note": ""},
    "computed": {
        "each": "round(subtotal / people, 2)",
        "subtotal": "total + total * tip / 100",
    },
    "root": {
        "type": "stack",
        "children": [
            {"type": "number", "label": "Total", "bind": "total"},
            {"type": "slider", "label": "Tip", "bind": "tip", "min": 0, "max": 30},
            {"type": "stat", "label": "Each", "value": "{{ fmt(each, 2) }}"},
            {
                "type": "chart",
                "kind": "bar",
                "labels": ["a"],
                "series": [{"data": [1]}],
            },
            {
                "type": "tabs",
                "children": [{"type": "tab", "label": "One", "children": []}],
            },
            {
                "type": "each",
                "items": "range(people)",
                "as": "n",
                "child": {"type": "text", "value": "#{{ n + 1 }} of {{ index }}"},
            },
            {
                "type": "form",
                "send": "note",
                "children": [{"type": "text_input", "bind": "note"}],
            },
            {
                "type": "button",
                "label": "Go",
                "send": "result",
                "set": {"tip": "tip + 5"},
                "data": {"each": "{{ each }}"},
                "visible": "{{ total > 0 }}",
            },
        ],
    },
}


def _errors(spec):
    with pytest.raises(SpecError) as info:
        validate_spec(spec)
    return dict(info.value.errors)


class TestValidSpec:
    def test_good_spec_reports_sends_order_and_defaults(self):
        validated = validate_spec(BILL)
        assert validated.send_names == {"note", "result"}
        assert validated.interactive is True
        assert validated.computed_order == ["subtotal", "each"]
        assert validated.spec["version"] == 1 and validated.layout == "inline"

    def test_display_only_spec_is_not_interactive(self):
        validated = validate_spec({"root": {"type": "text", "value": "hi"}})
        assert validated.interactive is False and validated.send_names == set()

    def test_build_scope_evaluates_computed_with_state_override(self):
        validated = validate_spec(BILL)
        assert build_scope(validated)["each"] == 44
        scope = build_scope(validated, {"people": 0})
        assert scope["each"] is None, "a runtime error yields null"
        assert scope["subtotal"] == 132


class TestErrors:
    def test_each_kind_of_problem_has_its_path(self):
        spec = {
            "version": 2,
            "layout": "wide",
            "extra": 1,
            "state": {"and": 1, "x": 1},
            "computed": {"x": "1", "y": 3, "z": "nope +"},
            "root": {
                "type": "stack",
                "children": [
                    {"type": "widget"},
                    {"type": "number", "bind": "missing"},
                    {"type": "slider", "bind": "x", "min": "a", "max": 1},
                    {"type": "grid", "columns": 20, "children": []},
                    {"type": "button", "label": "b", "send": "bad name"},
                    {"type": "text", "value": "{{ ghost }}", "colour": "red"},
                    {"type": "image", "src": "https://tracker.example/x.png"},
                    {"type": "tabs", "children": [{"type": "text", "value": "x"}]},
                    {"type": "select", "bind": "x", "options": [3]},
                    {"type": "button", "label": "b", "set": {"nope": "1"}},
                    {"type": "each", "items": "range(", "as": "if", "child": {}},
                ],
            },
        }
        errors = _errors(spec)
        assert "must be 1" in errors["version"]
        assert "inline" in errors["layout"]
        assert errors["extra"] == "unknown top-level key"
        assert "identifier" in errors["state.and"]
        assert "already a key" in errors["computed.x"]
        assert "expression string" in errors["computed.y"]
        assert "bad expression" in errors["computed.z"]
        assert "unknown component" in errors["root.children[0].type"]
        assert "not a key of 'state'" in errors["root.children[1].bind"]
        assert "number" in errors["root.children[2].min"]
        assert "<= 12" in errors["root.children[3].columns"]
        assert "message name" in errors["root.children[4].send"]
        assert "unknown name 'ghost'" in errors["root.children[5].value"]
        assert errors["root.children[5].colour"] == "unknown prop for text"
        assert "same-origin" in errors["root.children[6].src"]
        assert "'tab' nodes" in errors["root.children[7].children[0]"]
        assert "option" in errors["root.children[8].options[0]"]
        assert "not a key" in errors["root.children[9].set.nope"]
        assert "bad expression" in errors["root.children[10].items"]

    def test_errors_are_capped(self):
        nodes = [{"type": "nope"} for _ in range(50)]
        errors = _errors({"root": {"type": "stack", "children": nodes}})
        assert len(errors) == 20

    def test_required_and_root(self):
        assert _errors({})["root"] == "is required"
        assert _errors({"root": {"type": "heading"}})["root.value"] == "is required"
        assert _errors([])["spec"] == "must be an object"

    def test_computed_cycle(self):
        errors = _errors(
            {
                "computed": {"a": "b + 1", "b": "a + 1"},
                "root": {"type": "text", "value": "{{ a }}"},
            }
        )
        assert "dependency cycle" in errors["computed.a"]

    def test_each_scope_does_not_leak(self):
        spec = {
            "root": {
                "type": "stack",
                "children": [
                    {
                        "type": "each",
                        "items": "[1]",
                        "child": {"type": "text", "value": "{{ item }}"},
                    },
                    {"type": "text", "value": "{{ item }}"},
                ],
            }
        }
        errors = _errors(spec)
        assert list(errors) == ["root.children[1].value"]

    def test_size_limit(self):
        big = {"root": {"type": "text", "value": "x" * (MAX_SPEC_BYTES + 1)}}
        assert "limit" in _errors(big)["spec"]


class TestJsonSchema:
    def test_schema_matches_the_validator(self):
        jsonschema = pytest.importorskip("jsonschema")
        schema = json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema)
        assert not list(validator.iter_errors(BILL))
        bad = {"root": {"type": "text", "value": "x", "colour": "red"}}
        assert list(validator.iter_errors(bad))
        assert list(validator.iter_errors({"root": {"type": "unknown"}}))
        json.dumps(schema)
