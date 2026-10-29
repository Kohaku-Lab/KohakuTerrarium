"""Plain-text rendering of a ``kt.ui`` spec, for outputs that cannot draw it."""

from typing import Any

from kohakuterrarium.builtins.kt_ui.expr import (
    ExprError,
    evaluate,
    interpolate,
    to_display,
    truthy,
)
from kohakuterrarium.builtins.kt_ui.fallback_extra import EXTRA_TYPES, extra_lines
from kohakuterrarium.builtins.kt_ui.spec import ValidatedSpec, build_scope

MAX_FALLBACK_CHARS = 4000
MAX_EACH_ITEMS = 50
MAX_TABLE_ROWS = 20
PROGRESS_CELLS = 20
SPARK = "▁▂▃▄▅▆▇█"
STEP_MARKS = {"done": "[x]", "current": "[>]", "todo": "[ ]"}
CURRENCY_SIGNS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "¥",
    "TWD": "NT$",
}


def _value(value: Any, scope: dict[str, Any]) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return interpolate(value, scope)
    except ExprError:
        return "?"


def _text(value: Any, scope: dict[str, Any]) -> str:
    return to_display(_value(value, scope))


def _visible(node: dict, scope: dict[str, Any]) -> bool:
    return "visible" not in node or truthy(_value(node["visible"], scope))


def _option_label(option: Any, scope: dict[str, Any]) -> str:
    if isinstance(option, dict):
        return _text(option.get("label", option.get("value")), scope)
    return _text(option, scope)


def format_cell(value: Any, column: Any) -> str:
    """A table cell as text, using the column's ``format`` / ``digits`` / ``currency``."""
    fmt = column.get("format", "text") if isinstance(column, dict) else "text"
    if fmt == "text" or isinstance(value, bool) or not isinstance(value, (int, float)):
        return to_display(value)
    digits = column.get("digits", 2 if fmt == "currency" else 0)
    digits = digits if isinstance(digits, int) and 0 <= digits <= 10 else 0
    match fmt:
        case "percent":
            return f"{value * 100:,.{digits}f}%"
        case "currency":
            code = str(column.get("currency", "USD")).upper()
            sign = CURRENCY_SIGNS.get(code, code + " ")
            return f"{'-' if value < 0 else ''}{sign}{abs(value):,.{digits}f}"
        case "number":
            return f"{value:,.{digits}f}"
    return to_display(value)


def _table(node: dict, scope: dict[str, Any]) -> list[str]:
    columns = _value(node.get("columns"), scope)
    rows = _value(node.get("rows"), scope)
    if not isinstance(columns, list) or not isinstance(rows, list):
        return ["(table)"]
    keys = [c.get("key") if isinstance(c, dict) else c for c in columns]
    labels = [
        _text(c.get("label", c.get("key")), scope) if isinstance(c, dict) else str(c)
        for c in columns
    ]
    lines = [" | ".join(labels)]
    for i, row in enumerate(rows[:MAX_TABLE_ROWS]):
        if isinstance(row, dict):
            cells = [
                format_cell(_value(row.get(k), scope), c) for k, c in zip(keys, columns)
            ]
        elif isinstance(row, list):
            cells = [
                format_cell(_value(row[j], scope), columns[j]) if j < len(row) else ""
                for j in range(len(keys))
            ]
        else:
            cells = [_text(row, scope)]
        lines.append(" | ".join(cells))
    if len(rows) > MAX_TABLE_ROWS:
        lines.append(f"… {len(rows) - MAX_TABLE_ROWS} more rows")
    return lines


def _chart(node: dict, scope: dict[str, Any]) -> list[str]:
    title = _text(node.get("title", ""), scope)
    lines = [f"[{node.get('kind', 'chart')} chart{': ' + title if title else ''}]"]
    if node.get("kind") == "gauge":
        low, high = _value(node.get("min", 0), scope), _value(
            node.get("max", 100), scope
        )
        lines.append(f"range: {to_display(low)}-{to_display(high)}")
    labels = _value(node.get("labels"), scope)
    series = _value(node.get("series"), scope)
    if isinstance(labels, list) and labels:
        lines.append("labels: " + ", ".join(_text(v, scope) for v in labels))
    for item in series if isinstance(series, list) else []:
        if isinstance(item, dict):
            data = _value(item.get("data"), scope)
            name = _text(item.get("name", "series"), scope)
            values = data if isinstance(data, list) else [data]
            lines.append(f"{name}: " + ", ".join(_text(v, scope) for v in values))
    return lines


def _render(node: Any, scope: dict[str, Any], out: list[str], depth: int) -> None:
    if not isinstance(node, dict) or not _visible(node, scope):
        return
    pad = "  " * depth
    node_type = node.get("type")
    if "alt" in node and node_type != "image":
        out.append(pad + _text(node["alt"], scope))
        return
    match node_type:
        case "stack" | "grid" | "form" | "section" | "card" | "tab":
            heading = node.get("title", node.get("label"))
            if heading:
                out.append(f"{pad}{_text(heading, scope)}")
            inner = depth + (1 if heading else 0)
            for child in node.get("children") or []:
                _render(child, scope, out, inner)
            if node_type == "form":
                out.append(f"{pad}[{_text(node.get('submit', 'Submit'), scope)}]")
        case "tabs":
            for child in node.get("children") or []:
                _render(child, scope, out, depth)
        case "divider":
            out.append(pad + "---")
        case "heading":
            level = node.get("level", 2)
            marks = "#" * level if isinstance(level, int) else "##"
            out.append(f"{pad}{marks} {_text(node.get('value'), scope)}")
        case "text" | "code" | "diagram":
            value = node.get("value", node.get("source"))
            out.extend(pad + line for line in _text(value, scope).splitlines())
        case "badge":
            out.append(f"{pad}[{_text(node.get('value'), scope)}]")
        case "stat":
            label = _text(node.get("label", ""), scope)
            value = _text(node.get("value"), scope)
            trend = sparkline(_value(node.get("trend"), scope))
            line = f"{label}: {value}" if label else value
            out.append(pad + line + (f" {trend}" if trend else ""))
        case "callout":
            tone = node.get("tone", "info")
            title = _text(node.get("title", ""), scope)
            out.append(f"{pad}[{tone}]{' ' + title if title else ''}")
            out.extend(
                f"{pad}  {line}"
                for line in _text(node.get("value"), scope).splitlines()
            )
        case "quote":
            out.extend(
                f"{pad}> {line}"
                for line in _text(node.get("value"), scope).splitlines()
            )
            if node.get("cite"):
                out.append(f"{pad}  — {_text(node['cite'], scope)}")
        case "steps":
            out.extend(pad + line for line in _steps(node, scope))
        case "timeline":
            out.extend(pad + line for line in _timeline(node, scope))
        case "progress":
            out.append(pad + _progress(node, scope))
        case "tree":
            items = _value(node.get("items"), scope)
            _tree(items if isinstance(items, list) else [], scope, out, depth)
        case "accordion":
            out.append(f"{pad}▸ {_text(node.get('title'), scope)}")
            for child in node.get("children") or []:
                _render(child, scope, out, depth + 1)
        case "kv":
            fields = _value(node.get("fields"), scope)
            for item in fields if isinstance(fields, list) else []:
                if isinstance(item, dict):
                    label = _text(item.get("label"), scope)
                    out.append(f"{pad}{label}: {_text(item.get('value'), scope)}")
        case "list":
            items = _value(node.get("items"), scope)
            for i, item in enumerate(items if isinstance(items, list) else []):
                mark = f"{i + 1}." if node.get("ordered") is True else "-"
                out.append(f"{pad}{mark} {_text(item, scope)}")
        case "table":
            out.extend(pad + line for line in _table(node, scope))
        case "chart":
            out.extend(pad + line for line in _chart(node, scope))
        case "image":
            out.append(
                f"{pad}[image{': ' + _text(node['alt'], scope) if node.get('alt') else ''}]"
            )
        case "select" | "radio":
            current = to_display(scope.get(node.get("bind")))
            options = _value(node.get("options"), scope)
            choices = ", ".join(
                _option_label(o, scope)
                for o in (options if isinstance(options, list) else [])
            )
            out.append(
                f"{pad}{_text(node.get('label', node.get('bind')), scope)}: {current} ({choices})"
            )
        case "number" | "slider" | "text_input" | "toggle" | "checkbox" | "date":
            current = to_display(scope.get(node.get("bind")))
            out.append(
                f"{pad}{_text(node.get('label', node.get('bind')), scope)}: {current}"
            )
        case "button":
            out.append(f"{pad}[{_text(node.get('label'), scope)}]")
        case "each":
            _each(node, scope, out, depth)
        case "rating":
            out.append(pad + _rating(node, scope))
        case _ if node_type in EXTRA_TYPES:
            out.extend(pad + line for line in extra_lines(node, scope))


def _rating(node: dict, scope: dict[str, Any]) -> str:
    top = node.get("max", 5) if isinstance(node.get("max"), int) else 5
    current = scope.get(node.get("bind"))
    stars = round(current) if isinstance(current, (int, float)) else 0
    stars = max(0, min(top, stars))
    label = _text(node.get("label", node.get("bind")), scope)
    return f"{label}: {'★' * stars}{'☆' * (top - stars)} ({stars}/{top})"


def sparkline(values: Any) -> str:
    """Block characters for a list of numbers (empty when there are none)."""
    nums = (
        [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if isinstance(values, list)
        else []
    )
    if not nums:
        return ""
    low, high = min(nums), max(nums)
    span = (high - low) or 1
    return "".join(SPARK[round((v - low) / span * (len(SPARK) - 1))] for v in nums)


def _steps(node: dict, scope: dict[str, Any]) -> list[str]:
    items = _value(node.get("items"), scope)
    current = _value(node.get("current"), scope)
    lines = []
    for i, item in enumerate(items if isinstance(items, list) else []):
        step = item if isinstance(item, dict) else {"title": item}
        status = step.get("status")
        if status is None and isinstance(current, (int, float)):
            status = "done" if i < current else "current" if i == current else "todo"
        mark = STEP_MARKS.get(status or "todo", "[ ]")
        detail = _text(step.get("detail", ""), scope)
        title = _text(step.get("title"), scope)
        lines.append(f"{i + 1}. {mark} {title}" + (f" — {detail}" if detail else ""))
    return lines


def _timeline(node: dict, scope: dict[str, Any]) -> list[str]:
    items = _value(node.get("items"), scope)
    lines = []
    for item in items if isinstance(items, list) else []:
        entry = item if isinstance(item, dict) else {"title": item}
        date = _text(entry.get("date", ""), scope)
        detail = _text(entry.get("detail", ""), scope)
        head = f"{date}  " if date else ""
        lines.append(
            f"- {head}{_text(entry.get('title'), scope)}"
            + (f" — {detail}" if detail else "")
        )
    return lines


def _progress(node: dict, scope: dict[str, Any]) -> str:
    value = _value(node.get("value"), scope)
    top = _value(node.get("max", 100), scope)
    if (
        not isinstance(value, (int, float))
        or not isinstance(top, (int, float))
        or top <= 0
    ):
        return f"{_text(node.get('label', 'progress'), scope)}: ?"
    share = min(1.0, max(0.0, value / top))
    filled = round(share * PROGRESS_CELLS)
    bar = "#" * filled + "-" * (PROGRESS_CELLS - filled)
    label = _text(node.get("label", ""), scope)
    return f"{label + ': ' if label else ''}[{bar}] {round(share * 100)}%"


def _tree(items: list, scope: dict[str, Any], out: list[str], depth: int) -> None:
    for item in items:
        node = item if isinstance(item, dict) else {"label": item}
        badge = _text(node.get("badge", ""), scope)
        label = _text(node.get("label"), scope)
        out.append("  " * depth + f"- {label}" + (f" [{badge}]" if badge else ""))
        children = node.get("children")
        if isinstance(children, list):
            _tree(children, scope, out, depth + 1)


def _each(node: dict, scope: dict[str, Any], out: list[str], depth: int) -> None:
    try:
        items = evaluate(node.get("items", "[]"), scope)
    except ExprError:
        out.append("  " * depth + "?")
        return
    if not isinstance(items, list):
        return
    name = node.get("as", "item")
    for index, item in enumerate(items[:MAX_EACH_ITEMS]):
        _render(node.get("child"), {**scope, name: item, "index": index}, out, depth)
    if len(items) > MAX_EACH_ITEMS:
        out.append("  " * depth + f"… {len(items) - MAX_EACH_ITEMS} more")


def render_fallback(
    validated: ValidatedSpec, state: dict[str, Any] | None = None
) -> str:
    """Text form of ``validated`` with ``state`` (defaults to the spec's own state)."""
    scope = build_scope(validated, state)
    out: list[str] = []
    title = validated.spec.get("title")
    if title:
        out.append(_text(title, scope))
    _render(validated.spec.get("root"), scope, out, 0)
    text = "\n".join(line.rstrip() for line in out if line.strip())
    if len(text) > MAX_FALLBACK_CHARS:
        text = text[: MAX_FALLBACK_CHARS - 1] + "…"
    return text


__all__ = ["MAX_FALLBACK_CHARS", "format_cell", "render_fallback", "sparkline"]
