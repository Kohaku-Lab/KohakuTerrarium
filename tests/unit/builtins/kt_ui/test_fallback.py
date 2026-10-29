"""Plain-text fallback of kt.ui specs."""

from kohakuterrarium.builtins.kt_ui import fallback as fallback_mod
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
