"""kt.ui ``chart`` nodes: line kinds as a kohakutui ``Chart``, bar kinds as ``Bars``, heatmaps
as a shaded grid. Short series are joined into lines by the Chart itself."""

from typing import Any

from kohakutui.ui import Bars, Chart, Stack, Text

from kohakuterrarium.builtins.kt_ui.fallback import node_text
from kohakuterrarium.builtins.kt_ui.values import to_display
from kohakuterrarium.builtins.ktui.ui_slot import Slot
from kohakuterrarium.builtins.ktui.ui_values import items, text, value

# charts drawn as line plots, as bars, or (heatmap) as a shaded grid
LINE_KINDS = ("line", "area", "scatter")
BAR_KINDS = ("bar", "stacked", "radar", "pie", "gauge")
PX_PER_ROW = 25


def _rows(pixels: Any) -> int:
    """A chart height in web pixels (validated >= 80) as terminal rows, 4 to 16."""
    if not isinstance(pixels, (int, float)) or isinstance(pixels, bool):
        return 8
    return max(4, min(16, round(pixels / PX_PER_ROW)))


def chart(parent: Slot, node: dict, scope: dict) -> None:
    kind = node.get("kind")
    series = [s for s in items(node.get("series"), scope) if isinstance(s, dict)]
    if not series:
        parent.add(Text(node_text(node, scope)))
        return
    if kind in BAR_KINDS:
        _bars(parent, node, scope, series)
        return
    if kind == "heatmap":
        _heatmap(parent, node, scope, series)
        return
    low, high = value(node.get("min"), scope), value(node.get("max"), scope)
    numbers = [
        y[1] if isinstance(y, list) and len(y) == 2 else y
        for series_item in series
        for y in (series_item.get("data") or [])
    ]
    whole = all(
        isinstance(y, int) or (isinstance(y, float) and y.is_integer())
        for y in numbers
        if isinstance(y, (int, float)) and not isinstance(y, bool)
    )
    plot = parent.add(
        Chart(
            label=text(node.get("title"), scope) or None,
            digits=0 if whole else 2,
            height=_rows(value(node.get("height"), scope)),
            y_min=low if isinstance(low, (int, float)) else None,
            y_max=high if isinstance(high, (int, float)) else None,
        )
    )
    names = [to_display(s.get("name", f"series {i + 1}")) for i, s in enumerate(series)]
    datas = [s.get("data") if isinstance(s.get("data"), list) else [] for s in series]
    for x in range(max(len(d) for d in datas)):
        point = {}
        for name, data in zip(names, datas):
            y = data[x] if x < len(data) else None
            if isinstance(y, list) and len(y) == 2:  # scatter [x, y]
                y = y[1]
            if isinstance(y, (int, float)) and not isinstance(y, bool):
                point[name] = y
        if point:
            plot.append(point)


def _numbers(data: Any) -> list[float]:
    numbers = []
    for y in data if isinstance(data, list) else []:
        ok = isinstance(y, (int, float)) and not isinstance(y, bool)
        numbers.append(y if ok else 0)
    return numbers


def _bars(parent: Slot, node: dict, scope: dict, series: list[dict]) -> None:
    kind = node.get("kind")
    labels = [to_display(v) for v in items(node.get("labels"), scope)]
    box = parent.add(Stack(title=text(node.get("title"), scope) or None))
    if kind == "gauge":
        first = series[0]
        name = to_display(first.get("name", "")) or (labels[0] if labels else "value")
        high = value(node.get("max"), scope)
        box.add(
            Bars(
                labels=[name],
                series=[{"data": _numbers(first.get("data"))[:1] or [0]}],
                max=high if isinstance(high, (int, float)) else 100,
            )
        )
        return
    rows = [
        {"name": to_display(s.get("name", "")), "data": _numbers(s.get("data"))}
        for s in series
    ]
    if kind == "pie":
        rows = rows[:1]
    width = max((len(r["data"]) for r in rows), default=0)
    labels = (labels + [str(i + 1) for i in range(len(labels), width)])[:width]
    box.add(
        Bars(
            labels=labels, series=rows, stacked=kind == "stacked", percent=kind == "pie"
        )
    )


HEAT = "░▒▓█"


def _heatmap(parent: Slot, node: dict, scope: dict, series: list[dict]) -> None:
    labels = [to_display(v) for v in items(node.get("labels"), scope)]
    rows = [(to_display(s.get("name", "")), _numbers(s.get("data"))) for s in series]
    values = [v for _, data in rows for v in data]
    low, high = (min(values), max(values)) if values else (0, 0)
    name_w = max((len(n) for n, _ in rows), default=0)
    cell_w = max([2, *(len(label) for label in labels)])
    box = parent.add(Stack(title=text(node.get("title"), scope) or None))
    lines = []
    if labels:
        head = " ".join(label[:cell_w].ljust(cell_w) for label in labels)
        lines.append((" " * (name_w + 1) + head).rstrip())
    for name, data in rows:
        cells = []
        for v in data:
            share = (v - low) / (high - low) if high > low else 1.0
            cells.append(HEAT[min(3, int(share * 3.999))] * cell_w)
        lines.append(name.ljust(name_w) + " " + " ".join(cells))
    lines.append(f"{HEAT[0]} {to_display(low)}  …  {HEAT[3]} {to_display(high)}")
    box.add(Text("\n".join(lines)))


__all__ = ["BAR_KINDS", "LINE_KINDS", "chart"]
