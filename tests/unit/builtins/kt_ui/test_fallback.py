"""Plain-text fallback of kt.ui specs."""

import pytest

from kohakuterrarium.builtins.kt_ui import fallback as fallback_mod
from kohakuterrarium.builtins.kt_ui.components import COMPONENTS
from kohakuterrarium.builtins.kt_ui.fallback import render_fallback
from kohakuterrarium.builtins.kt_ui.spec import validate_spec


def _render(root, state=None, title=None, computed=None, override=None):
    spec = {"root": root, "state": state or {}, "computed": computed or {}}
    if title:
        spec["title"] = title
    return render_fallback(validate_spec(spec), override)


def test_every_component_renders_as_text():
    root = {
        "type": "stack",
        "children": [
            {"type": "heading", "value": "Plan", "level": 3},
            {"type": "text", "value": "line one\nline two"},
            {"type": "badge", "value": "new"},
            {"type": "stat", "label": "Total", "value": "{{ fmt(total, 2) }}"},
            {"type": "stat", "value": "bare"},
            {"type": "kv", "fields": [{"label": "Owner", "value": "{{ who }}"}]},
            {"type": "list", "items": ["a", "b"], "ordered": True},
            {"type": "list", "items": "{{ ['x'] }}"},
            {
                "type": "table",
                "columns": ["name", {"key": "qty", "label": "Qty"}],
                "rows": [{"name": "tea", "qty": 2}, ["cake"], "odd"],
            },
            {
                "type": "chart",
                "kind": "line",
                "title": "Trend",
                "labels": ["mon", "tue"],
                "series": [{"name": "cups", "data": [1, 2]}, {"data": 3}],
            },
            {"type": "image", "src": "data:image/png;base64,AA", "alt": "logo"},
            {"type": "image", "src": "/a.png"},
            {"type": "code", "value": "x = 1", "lang": "py"},
            {"type": "diagram", "source": "a --> b"},
            {"type": "divider"},
            {
                "type": "select",
                "label": "Size",
                "bind": "size",
                "options": ["s", {"value": "l", "label": "Large"}],
            },
            {"type": "toggle", "bind": "on"},
            {"type": "button", "label": "Go"},
            {"type": "text", "value": "hidden", "visible": False},
            {"type": "text", "value": "secret", "alt": "summary only"},
            {
                "type": "tabs",
                "children": [
                    {
                        "type": "tab",
                        "label": "First",
                        "children": [{"type": "text", "value": "in tab"}],
                    }
                ],
            },
            {
                "type": "card",
                "title": "Card",
                "children": [{"type": "text", "value": "inside"}],
            },
            {"type": "form", "send": "f", "children": [], "submit": "Save"},
        ],
    }
    text = _render(
        root, state={"total": 1234.5, "who": "Ada", "size": "l", "on": True}, title="T"
    )
    lines = text.splitlines()
    assert lines[0] == "T"
    for expected in [
        "### Plan",
        "line one",
        "line two",
        "[new]",
        "Total: 1,234.50",
        "bare",
        "Owner: Ada",
        "1. a",
        "2. b",
        "- x",
        "name | Qty",
        "tea | 2",
        "cake |",
        "odd",
        "[line chart: Trend]",
        "labels: mon, tue",
        "cups: 1, 2",
        "[image: logo]",
        "[image]",
        "x = 1",
        "a --> b",
        "---",
        "Size: l (s, Large)",
        "on: true",
        "[Go]",
        "summary only",
        "First",
        "  in tab",
        "Card",
        "  inside",
        "[Save]",
    ]:
        assert (
            expected in [line.rstrip() for line in lines] or expected in text
        ), expected
    assert "hidden" not in text and "secret" not in text


def test_each_override_errors_and_limits(monkeypatch):
    root = {
        "type": "stack",
        "children": [
            {
                "type": "each",
                "items": "range(n)",
                "as": "i",
                "child": {"type": "text", "value": "row {{ i }}/{{ index }}"},
            },
            {"type": "stat", "label": "Ratio", "value": "{{ 1 / n }}"},
            {
                "type": "table",
                "columns": "{{ cols }}",
                "rows": "{{ rows }}",
            },
            {
                "type": "each",
                "items": "1 / zero",
                "child": {"type": "text", "value": "x"},
            },
            {"type": "each", "items": "n", "child": {"type": "text", "value": "x"}},
        ],
    }
    state = {"n": 2, "zero": 0, "cols": ["a"], "rows": [[1], [2], [3]]}
    text = _render(root, state=state)
    assert "row 0/0" in text and "row 1/1" in text and "Ratio: 0.5" in text
    zero = _render(root, state=state, override={"n": 0})
    assert "Ratio: ?" in zero and "row" not in zero
    monkeypatch.setattr(fallback_mod, "MAX_EACH_ITEMS", 1)
    monkeypatch.setattr(fallback_mod, "MAX_TABLE_ROWS", 2)
    capped = _render(root, state=state)
    assert "… 1 more" in capped and "… 1 more rows" in capped
    assert "\n?" in "\n" + capped or capped.count("?") >= 1


def test_long_fallback_is_truncated(monkeypatch):
    monkeypatch.setattr(fallback_mod, "MAX_FALLBACK_CHARS", 10)
    text = _render({"type": "text", "value": "abcdefghijklmnop"})
    assert len(text) == 10 and text.endswith("…")


def test_batch_a_and_b_components_render_as_text():
    root = {
        "type": "stack",
        "children": [
            {
                "type": "callout",
                "tone": "warning",
                "title": "Heads up",
                "value": "Back up",
            },
            {"type": "callout", "value": "plain"},
            {"type": "quote", "value": "Measure twice.", "cite": "Ada"},
            {
                "type": "steps",
                "current": 1,
                "items": ["Plan", {"title": "Build", "detail": "2d"}, "Ship"],
            },
            {"type": "steps", "items": [{"title": "Only", "status": "done"}]},
            {"type": "timeline", "items": [{"date": "2026-01", "title": "v1"}, "v2"]},
            {"type": "progress", "label": "Upload", "value": 30, "max": 60},
            {"type": "progress", "value": 9, "max": 0},
            {"type": "progress", "value": 500},
            {"type": "stat", "label": "Users", "value": "12", "trend": [1, 5, 3]},
            {"type": "stat", "value": "flat", "trend": []},
            {
                "type": "table",
                "columns": [
                    "n",
                    {"key": "p", "label": "Price", "format": "currency"},
                    {"key": "s", "label": "Share", "format": "percent", "digits": 1},
                    {"key": "c", "label": "Count", "format": "number"},
                    {
                        "key": "e",
                        "label": "Euro",
                        "format": "currency",
                        "currency": "eur",
                    },
                    {
                        "key": "k",
                        "label": "Odd",
                        "format": "currency",
                        "currency": "CHF",
                    },
                ],
                "rows": [
                    {"n": "a", "p": -1234.5, "s": 0.256, "c": 12345, "e": 2, "k": 3},
                    ["b", "n/a", None, True, 1, 1],
                ],
            },
            {
                "type": "tree",
                "items": [
                    {
                        "label": "src",
                        "badge": "dir",
                        "children": ["main.py", {"label": "lib", "children": ["x.py"]}],
                    },
                    "README.md",
                ],
            },
            {
                "type": "accordion",
                "title": "Details",
                "children": [{"type": "text", "value": "inside"}],
            },
            {
                "type": "chart",
                "kind": "gauge",
                "min": 0,
                "max": 200,
                "series": [{"name": "cpu", "data": [42]}],
            },
        ],
    }
    lines = [line.rstrip() for line in _render(root).splitlines()]
    for expected in [
        "[warning] Heads up",
        "  Back up",
        "[info]",
        "  plain",
        "> Measure twice.",
        "  — Ada",
        "1. [x] Plan",
        "2. [>] Build — 2d",
        "3. [ ] Ship",
        "1. [x] Only",
        "- 2026-01  v1",
        "- v2",
        "Upload: [##########----------] 50%",
        "progress: ?",
        "[####################] 100%",
        "Users: 12 ▁█▅",
        "flat",
        "n | Price | Share | Count | Euro | Odd",
        "a | -$1,234.50 | 25.6% | 12,345 | €2.00 | CHF 3.00",
        "b | n/a |  | true | €1.00 | CHF 1.00",
        "- src [dir]",
        "  - main.py",
        "  - lib",
        "    - x.py",
        "- README.md",
        "▸ Details",
        "  inside",
        "[gauge chart]",
        "range: 0-200",
        "cpu: 42",
    ]:
        assert expected in lines, (expected, lines)


_SAMPLE_LISTS = {
    "fields": [{"label": "f", "value": 1}],
    "gallery.items": ["/a.png"],
    "compare.items": [{"title": "A", "pros": ["p"], "cons": ["c"]}],
    "series": [{"name": "s", "data": [1]}],
    "rows": ["r"],
}
_SAMPLE_VALUES = {
    "text": "x",
    "literal": "x",
    "bool": True,
    "var": "v",
    "name": "n",
    "src": "/a.png",
    "url": "https://example.org/",
    "map": {},
    "setmap": {},
    "expr": "[1]",
    "children": [{"type": "text", "value": "c"}],
    "tabs": [{"type": "tab", "label": "t"}],
    "child": {"type": "text", "value": "c"},
}


def _sample(component_type):
    node = {"type": component_type}
    for key, prop in COMPONENTS[component_type].props.items():
        if not prop.required and prop.kind not in ("children", "tabs"):
            continue
        if prop.kind == "enum":
            node[key] = prop.choices[0]
        elif prop.kind in ("num", "int"):
            node[key] = prop.low if prop.low is not None else 1
        elif prop.kind == "list":
            node[key] = _SAMPLE_LISTS.get(
                f"{component_type}.{key}", _SAMPLE_LISTS.get(key, ["x"])
            )
        else:
            node[key] = _SAMPLE_VALUES[prop.kind]
    if component_type == "form":
        node["send"] = "n"
    if component_type == "timer":
        node["seconds"] = 90
    return node


@pytest.mark.parametrize("component_type", sorted(COMPONENTS))
def test_every_component_type_has_a_text_form(component_type):
    if component_type == "tab":
        root = {"type": "tabs", "children": [_sample("tab")]}
    else:
        root = _sample(component_type)
    assert _render(root, state={"v": 1}).strip(), component_type
