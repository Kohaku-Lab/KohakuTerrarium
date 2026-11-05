"""kt.ui spec nodes → kohakutui nodes, for inline ``<ui>`` blocks.

``mount(parent, node, scope)`` draws the node into ``parent`` (a ``ui_slot.Slot``, so a redraw
updates the nodes in place) with every prop evaluated in ``scope`` (state plus computed
values, see ``kt_ui.spec.build_scope``). Inputs, buttons and forms are in ``ui_controls``. A
component without a mapping is drawn as its text fallback (``kt_ui.fallback``), so nothing the
model writes is lost (D10).
"""

from collections.abc import Callable
from typing import Any

from kohakutui.ui import (
    Button,
    Cell,
    Code,
    Columns,
    Divider,
    Field,
    KV,
    Markdown,
    Progress,
    Sparkline,
    Stack,
    Table,
    Tabs,
    Text,
)

from kohakuterrarium.builtins.kt_ui.expr import ExprError, evaluate
from kohakuterrarium.builtins.kt_ui.fallback import format_cell, node_text
from kohakuterrarium.builtins.kt_ui.values import to_display, truthy
from kohakuterrarium.builtins.ktui.ui_controls import HANDLERS as CONTROL_HANDLERS
from kohakuterrarium.builtins.ktui.ui_charts import chart
from kohakuterrarium.builtins.ktui.ui_values import items, text, value
from kohakuterrarium.builtins.ktui.ui_slot import Slot

# the type of what handlers draw into
Node = Slot

# kt.ui tones → kohakutui theme roles
TONES = {
    "neutral": None,
    "info": "tool",
    "tip": "accent",
    "success": "ok",
    "warning": "attention",
    "danger": "danger",
}
TONE_ICONS = {
    "neutral": "•",
    "info": "ℹ",
    "tip": "✦",
    "success": "✓",
    "warning": "⚠",
    "danger": "✗",
}
ICONS = {
    "check": "✓",
    "close": "✗",
    "warning": "⚠",
    "info": "ℹ",
    "error": "✗",
    "help": "?",
    "star": "★",
    "heart": "♥",
    "time": "◷",
    "lightning": "ϟ",
    "flag": "⚑",
    "link": "↗",
    "lock": "⚿",
    "email": "✉",
    "idea": "✦",
}
STEP_MARKS = {"done": ("✓", "ok"), "current": ("●", "accent"), "todo": ("○", "muted")}
MAX_EACH = 50


def _children(parent: Node, node: dict, scope: dict[str, Any]) -> None:
    for child in node.get("children") or []:
        mount(parent, child, scope)


def _gap(node: dict) -> int:
    return 1 if node.get("gap", "md") in ("md", "lg") else 0


# -- layout -------------------------------------------------------------------------------


def _grid(parent: Node, node: dict, scope: dict) -> None:
    count = value(node.get("columns", 2), scope)
    count = int(count) if isinstance(count, (int, float)) and count >= 1 else 2
    cols = parent.add(Columns(count=count, gap=3))
    _children(cols, node, scope)


def _stack(parent: Node, node: dict, scope: dict) -> None:
    if node.get("direction") == "row":
        cols = parent.add(Columns(gap=2, pack=True))
        _children(cols, node, scope)
        return
    title = text(node.get("title") or node.get("label"), scope) or None
    box = parent.add(Stack(title=title, gap=_gap(node)))
    if node.get("subtitle"):
        box.add(Text(text(node["subtitle"], scope), tone="muted"))
    _children(box, node, scope)


def _tabs(parent: Node, node: dict, scope: dict) -> None:
    tabs = [t for t in node.get("children") or [] if isinstance(t, dict)]
    ui = parent.ui
    key = f"tabs:{id(node)}"
    active = ui.remembered(key, 0) if ui is not None else 0
    drawn = parent.add(
        Tabs(labels=[text(t.get("label"), scope) or "·" for t in tabs], active=active)
    )
    if ui is not None:
        ui.remember_on_change(drawn.node, key)
    for tab in tabs:
        pane = drawn.add(Stack(gap=_gap(tab)))
        _children(pane, tab, scope)


def _accordion(parent: Node, node: dict, scope: dict) -> None:
    opened = truthy(value(node.get("open", False), scope))
    cell = parent.add(
        Cell(role="system", title=text(node.get("title"), scope), collapsed=not opened)
    )
    _children(cell, node, scope)


def _each(parent: Node, node: dict, scope: dict) -> None:
    try:
        entries = evaluate(node.get("items", "[]"), scope)
    except ExprError:
        entries = []
    if not isinstance(entries, list):
        return
    name = node.get("as", "item")
    for index, item in enumerate(entries[:MAX_EACH]):
        mount(parent, node.get("child"), {**scope, name: item, "index": index})
    if len(entries) > MAX_EACH:
        parent.add(Text(f"… {len(entries) - MAX_EACH} more", tone="muted"))


# -- content ------------------------------------------------------------------------------


def _text_node(parent: Node, node: dict, scope: dict) -> None:
    body = text(node.get("value"), scope)
    if truthy(value(node.get("muted", False), scope)):
        parent.add(Text(body, tone="muted"))
    else:
        parent.add(Markdown(source=body))


def _heading(parent: Node, node: dict, scope: dict) -> None:
    level = value(node.get("level", 2), scope)
    tone = "accent" if level in (1, 1.0) else None
    parent.add(Text(text(node.get("value"), scope), bold=True, tone=tone))


def _badge(parent: Node, node: dict, scope: dict) -> None:
    tone = TONES.get(node.get("tone", "neutral"))
    parent.add(Text(f"[{text(node.get('value'), scope)}]", tone=tone or "muted"))


def _stat(parent: Node, node: dict, scope: dict) -> None:
    label = text(node.get("label"), scope)
    shown = text(node.get("value"), scope)
    hint = text(node.get("hint"), scope)
    tone = TONES.get(node.get("tone", "neutral"))
    parent = parent.add(Stack(gap=0))
    parent.add(Text(f"{label}  {shown}" if label else shown, bold=True, tone=tone))
    if hint:
        parent.add(Text(hint, tone="muted"))
    trend = [v for v in items(node.get("trend"), scope) if isinstance(v, (int, float))]
    if trend:
        parent.add(Sparkline(values=trend))


def _callout(parent: Node, node: dict, scope: dict) -> None:
    tone = node.get("tone", "info")
    title = text(node.get("title"), scope) or None
    cell = parent.add(
        Cell(
            role="system",
            icon=TONE_ICONS.get(tone, "•"),
            tone=TONES.get(tone),
            title=title,
        )
    )
    cell.add(Markdown(source=text(node.get("value"), scope)))


def _quote(parent: Node, node: dict, scope: dict) -> None:
    parent = parent.add(Stack(gap=0))
    parent.add(Text("▌ " + text(node.get("value"), scope), italic=True))
    if node.get("cite"):
        parent.add(Text("  — " + text(node["cite"], scope), tone="muted"))


def _steps(parent: Node, node: dict, scope: dict) -> None:
    current = value(node.get("current"), scope)
    parent = parent.add(Stack(gap=0))
    for index, item in enumerate(items(node.get("items"), scope)):
        entry = item if isinstance(item, dict) else {"title": item}
        status = entry.get("status")
        if status is None and isinstance(current, (int, float)):
            status = (
                "done" if index < current else "current" if index == current else "todo"
            )
        mark, tone = STEP_MARKS.get(status or "todo", STEP_MARKS["todo"])
        line = f"{mark} {to_display(entry.get('title'))}"
        if entry.get("detail"):
            line += f"  {to_display(entry['detail'])}"
        parent.add(Text(line, tone=tone))


def _timeline(parent: Node, node: dict, scope: dict) -> None:
    parent = parent.add(Stack(gap=0))
    for item in items(node.get("items"), scope):
        entry = item if isinstance(item, dict) else {"title": item}
        date = to_display(entry["date"]) + "  " if entry.get("date") else ""
        parent.add(
            Text(
                f"• {date}{to_display(entry.get('title'))}",
                tone=TONES.get(entry.get("tone")),
            )
        )
        if entry.get("detail"):
            parent.add(Text("  " + to_display(entry["detail"]), tone="muted"))


def _progress(parent: Node, node: dict, scope: dict) -> None:
    total = value(node.get("max", 100), scope)
    done = value(node.get("value", 0), scope)
    if not isinstance(total, (int, float)) or not isinstance(done, (int, float)):
        parent.add(Text(node_text(node, scope)))
        return
    parent.add(Progress(value=done, total=total, label=text(node.get("label"), scope)))


def _tree_lines(items: list, prefix: str, out: list[str]) -> None:
    for i, item in enumerate(items):
        entry = item if isinstance(item, dict) else {"label": item}
        last = i == len(items) - 1
        badge = f"  [{to_display(entry['badge'])}]" if entry.get("badge") else ""
        out.append(
            f"{prefix}{'└─' if last else '├─'} {to_display(entry.get('label'))}{badge}"
        )
        kids = entry.get("children")
        if isinstance(kids, list):
            _tree_lines(kids, prefix + ("   " if last else "│  "), out)


def _tree(parent: Node, node: dict, scope: dict) -> None:
    lines: list[str] = []
    _tree_lines(items(node.get("items"), scope), "", lines)
    parent.add(Text("\n".join(lines)))


def _kv(parent: Node, node: dict, scope: dict) -> None:
    fields = [
        {"label": to_display(f.get("label")), "value": to_display(f.get("value"))}
        for f in items(node.get("fields"), scope)
        if isinstance(f, dict)
    ]
    parent.add(KV(fields=fields))


def _list(parent: Node, node: dict, scope: dict) -> None:
    ordered = truthy(value(node.get("ordered", False), scope))
    entries = items(node.get("items"), scope)
    marks = [f"{i + 1}. " if ordered else "- " for i in range(len(entries))]
    source = "\n".join(mark + to_display(item) for mark, item in zip(marks, entries))
    parent.add(Markdown(source=source))


def _table(parent: Node, node: dict, scope: dict) -> None:
    columns = items(node.get("columns"), scope)
    rows = items(node.get("rows"), scope)
    keys = [c.get("key") if isinstance(c, dict) else c for c in columns]
    heads = []
    for c in columns:
        if isinstance(c, dict):
            numeric = c.get("format") in ("number", "percent", "currency")
            align = c.get("align") or ("right" if numeric else "left")
            heads.append(
                {
                    "key": str(c.get("key")),
                    "label": to_display(c.get("label", c.get("key"))),
                    "align": align,
                }
            )
        else:
            heads.append(str(c))
    cells = []
    for row in rows:
        if isinstance(row, dict):
            got = [row.get(k) for k in keys]
        elif isinstance(row, list):
            got = row
        else:
            continue
        cells.append([format_cell(v, c) for v, c in zip(got, columns)])
    parent.add(Table(columns=heads, rows=cells))


def _code(parent: Node, node: dict, scope: dict) -> None:
    parent.add(Code(value=text(node.get("value"), scope), lang=node.get("lang")))


def _diagram(parent: Node, node: dict, scope: dict) -> None:
    parent = parent.add(Stack(gap=0))
    title = text(node.get("title"), scope)
    if title:
        parent.add(Text(title, bold=True))
    parent.add(Code(value=text(node.get("source"), scope), lang="mermaid"))


def _math(parent: Node, node: dict, scope: dict) -> None:
    parent.add(Code(value=str(node.get("value", "")), lang="latex"))


def _link(parent: Node, node: dict, scope: dict) -> None:
    # a hyperlink on its label (OSC 8; clicked in full screen it opens); where links are
    # written as text, or the URL may not be linked, kohakutui shows the URL after it
    href = text(node.get("href"), scope)
    label = text(node.get("label"), scope) or href
    parent.add(Text(label, link=href or None))


def _icon(parent: Node, node: dict, scope: dict) -> None:
    glyph = ICONS.get(node.get("name", ""), "•")
    label = text(node.get("label"), scope)
    tone = TONES.get(node.get("tone", "neutral"))
    parent.add(Text(f"{glyph} {label}".rstrip(), tone=tone))


def _copy(parent: Node, node: dict, scope: dict) -> None:
    ui = parent.ui
    parent = parent.add(Stack(gap=0))
    shown = text(node.get("value"), scope)
    parent.add(Code(value=shown))
    label = text(node.get("label"), scope) or "copy"
    if ui is not None and ui.live:
        drawn = parent.add(Button(label=f"[ ⧉ {label} ]", focused_hint="enter copies"))
        ui.on_select(drawn.node, lambda: ui.copy(shown))


def _compare(parent: Node, node: dict, scope: dict) -> None:
    entries = [i for i in items(node.get("items"), scope) if isinstance(i, dict)]
    cols = parent.add(Columns(count=min(len(entries), 3) or 1, gap=3, min_width=24))
    for item in entries:
        box = cols.add(Stack(title=to_display(item.get("title", ""))))
        for pro in item.get("pros") or []:
            box.add(Text(f"✓ {to_display(pro)}", tone="ok"))
        for con in item.get("cons") or []:
            box.add(Text(f"✗ {to_display(con)}", tone="danger"))
        if item.get("verdict"):
            box.add(Text(to_display(item["verdict"]), italic=True, tone="accent"))


def _quiz(parent: Node, node: dict, scope: dict) -> None:
    ui = parent.ui
    box = parent.add(Stack(gap=0))
    box.add(Markdown(source=text(node.get("question"), scope)))
    options = [
        o if isinstance(o, dict) else {"label": to_display(o), "value": o}
        for o in items(node.get("options"), scope)
    ]
    answer = node.get("answer")
    key = f"quiz:{id(node)}"
    picked = ui.remembered(key, None) if ui is not None else None
    if ui is None or not ui.live:
        for o in options:
            mark = "◉" if o.get("value") == picked else "○"
            box.add(Text(f"{mark} {to_display(o.get('label'))}", tone="muted"))
    else:
        field = box.add(Field(control="radio", value=picked, options=options))
        ui.remember_on_change(field.node, key, redraw=True)
    if picked is None:
        return
    right = str(picked) == str(answer)
    shown = next(
        (o.get("label") for o in options if str(o.get("value")) == str(answer)), answer
    )
    verdict = "✓ correct" if right else f"✗ the answer is {to_display(shown)}"
    box.add(Text(verdict, tone="ok" if right else "danger", bold=True))
    if node.get("explain"):
        box.add(Markdown(source=text(node["explain"], scope)))


def _fallback(parent: Node, node: dict, scope: dict) -> None:
    shown = node_text(node, scope)
    if shown:
        parent.add(Text(shown))


HANDLERS: dict[str, Callable[[Node, dict, dict], None]] = {
    "stack": _stack,
    "grid": _grid,
    "tab": _stack,
    "card": _stack,
    "section": _stack,
    "tabs": _tabs,
    "accordion": _accordion,
    "divider": lambda parent, node, scope: parent.add(Divider()),
    "each": _each,
    "text": _text_node,
    "heading": _heading,
    "badge": _badge,
    "stat": _stat,
    "callout": _callout,
    "quote": _quote,
    "steps": _steps,
    "timeline": _timeline,
    "progress": _progress,
    "tree": _tree,
    "kv": _kv,
    "list": _list,
    "table": _table,
    "code": _code,
    "chart": chart,
    "diagram": _diagram,
    "math": _math,
    "link": _link,
    "icon": _icon,
    "copy": _copy,
    "compare": _compare,
    "quiz": _quiz,
    **CONTROL_HANDLERS,
}
# drawn as text: media (P4), html (no app frames in a terminal), timer (no ticking widget yet)
FALLBACK_TYPES = frozenset({"image", "gallery", "html", "timer"})


def mount(parent: Node, node: Any, scope: dict[str, Any]) -> None:
    """Adds ``node`` (a validated spec node) under ``parent``."""
    if not isinstance(node, dict):
        return
    if "visible" in node and not truthy(value(node["visible"], scope)):
        return
    HANDLERS.get(node.get("type", ""), _fallback)(parent, node, scope)
