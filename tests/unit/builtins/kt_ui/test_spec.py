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

    def test_new_component_shapes_have_their_paths(self):
        deep = {"label": "d"}
        for _ in range(14):
            deep = {"label": "d", "children": [deep]}
        nodes = [
            {"type": "steps", "items": [{"title": "a", "status": "later"}, 3]},
            {"type": "timeline", "items": [{"date": "2026"}]},
            {"type": "tree", "items": [{"children": []}]},
            {"type": "tree", "items": [{"label": "x", "children": "nope"}]},
            {"type": "tree", "items": [deep]},
            {"type": "tree", "items": ["leaf"] * 501},
            {
                "type": "table",
                "columns": [
                    {"key": "a", "format": "money"},
                    {"key": "b", "digits": 11},
                ],
                "rows": [],
            },
            {"type": "callout", "value": "x", "tone": "neutral"},
            {"type": "progress", "value": 1, "style": "pie"},
            {"type": "chart", "kind": "funnel", "series": []},
            {"type": "accordion", "children": []},
        ]
        errors = _errors({"root": {"type": "stack", "children": nodes}})
        assert "status is one of" in errors["root.children[0].items[0]"]
        assert "a step is" in errors["root.children[0].items[1]"]
        assert "timeline entry" in errors["root.children[1].items[0]"]
        assert "tree node is" in errors["root.children[2].items[0]"]
        assert "array of nodes" in errors["root.children[3].items[0]"]
        assert "levels deep" in errors["root.children[4].items[0]"]
        assert "at most 500 nodes" in errors["root.children[5].items"]
        assert "format is one of" in errors["root.children[6].columns[0]"]
        assert "digits" in errors["root.children[6].columns[1]"]
        assert "one of" in errors["root.children[7].tone"]
        assert "one of" in errors["root.children[8].style"]
        assert "one of" in errors["root.children[9].kind"]
        assert errors["root.children[10].title"] == "is required"

    def test_media_link_and_widget_rules_have_their_paths(self):
        nodes = [
            {"type": "link", "href": "javascript:alert(1)"},
            {"type": "link", "href": "ftp://x.org/{{ q }}"},
            {"type": "gallery", "items": ["https://cdn.example/a.png", {"alt": "x"}]},
            {"type": "gallery", "items": ["/a.png"] * 49},
            {"type": "compare", "items": []},
            {"type": "compare", "items": [{"title": "A", "pros": "fast"}]},
            {"type": "quiz", "question": "Q", "options": ["a", "b"], "answer": "c"},
            {"type": "timer", "label": "t"},
            {"type": "icon", "name": "unicorn"},
            {"type": "html", "source": "x" * (48 * 1024 + 1)},
            {"type": "rating", "bind": "score", "max": 11},
            {"type": "link", "href": "{{ q }}/path"},
        ]
        spec = {
            "state": {"q": "", "score": 1, "__ui": {}},
            "root": {"type": "stack", "children": nodes},
        }
        errors = _errors(spec)
        assert "http://" in errors["root.children[0].href"]
        assert "http://" in errors["root.children[1].href"]
        assert "same-origin" in errors["root.children[2].items[0]"]
        assert "src string" in errors["root.children[2].items[1]"]
        assert "at most 48" in errors["root.children[3].items"]
        assert "1-6 items" in errors["root.children[4].items"]
        assert "list of strings" in errors["root.children[5].items[0]"]
        assert "one of the options" in errors["root.children[6].answer"]
        assert "needs 'seconds'" in errors["root.children[7].seconds"]
        assert "one of" in errors["root.children[8].name"]
        assert "the limit is" in errors["root.children[9].source"]
        assert "<= 10" in errors["root.children[10].max"]
        assert "http://" in errors["root.children[11].href"]
        assert "reserved" in errors["state.__ui"]

    def test_media_link_and_widget_rules_accept_good_nodes(self):
        nodes = [
            {"type": "link", "href": "https://example.org/a?b=1", "label": "Docs"},
            {"type": "link", "href": "https://example.org/s?q={{ q }}"},
            {"type": "link", "href": "{{ url }}"},
            {
                "type": "gallery",
                "items": ["/a.png", {"src": "data:image/png;base64,AA"}],
            },
            {"type": "compare", "items": [{"title": "A", "pros": ["p"], "cons": []}]},
            {
                "type": "quiz",
                "question": "Q",
                "options": [{"value": "b"}],
                "answer": "b",
            },
            {"type": "timer", "mode": "stopwatch"},
            {"type": "html", "source": "<b>{{ raw }}</b>"},
            {"type": "math", "value": "\\frac{a}{b}"},
            {"type": "copy", "value": "pip install {kt}"},
        ]
        spec = {
            "state": {"q": "", "url": ""},
            "root": {"type": "stack", "children": nodes},
        }
        assert (
            validate_spec(spec).spec["root"]["children"][7]["source"]
            == "<b>{{ raw }}</b>"
        )

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
