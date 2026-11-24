"""Aggregate run records and render ``report.md``.

Rates are printed with the runs behind them (``3/4``). Harness errors are
counted but excluded from every rate. No intervals are printed: a handful of
repeats per task does not support them.
"""

from collections.abc import Iterable, Mapping
from statistics import median
from typing import Any

from .score import OUTCOMES, PASSING, failure_reason
from .store import latest

TOKEN_KEYS = ("input", "cached_input", "output", "reasoning")


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _median(values: list[float]) -> float | None:
    return median(values) if values else None


def _tokens(records: list[Mapping[str, Any]]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for key in TOKEN_KEYS:
        values = [(r.get("tokens") or {}).get(key) for r in records]
        known = [v for v in values if v is not None]
        out[key] = sum(known) if known else None
    return out


def _summary(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    judged = [r for r in records if r["outcome"] != "harness_error"]
    passed = sum(r["outcome"] in PASSING for r in judged)
    false = sum(bool(r.get("false_success")) for r in judged)
    calls = [r["tool_calls"]["total"] for r in judged]
    return {
        "runs": len(records),
        "judged": len(judged),
        "harness_errors": len(records) - len(judged),
        "passed": passed,
        "pass_rate": _rate(passed, len(judged)),
        "false_success": false,
        "false_success_rate": _rate(false, len(judged)),
        "outcomes": {o: sum(r["outcome"] == o for r in records) for o in OUTCOMES},
        "tool_calls_total": sum(calls),
        "tool_calls_median": _median(calls),
        "wall_median_s": _median([r["wall_s"] for r in judged]),
        "wall_total_s": sum(r.get("wall_s") or 0 for r in records),
        "tokens": _tokens(records),
    }


def aggregate(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Overall, per-category and per-task figures over the latest record of each pair."""
    current = sorted(
        latest(records).values(), key=lambda r: (r["task_id"], r["repeat"])
    )
    categories: dict[str, dict[str, int]] = {}
    tasks: dict[str, list[dict]] = {}
    for r in current:
        cat = categories.setdefault(
            r["category"],
            {"judged": 0, "passed": 0, "false_success": 0, "harness_errors": 0},
        )
        if r["outcome"] == "harness_error":
            cat["harness_errors"] += 1
        else:
            cat["judged"] += 1
            cat["passed"] += r["outcome"] in PASSING
            cat["false_success"] += bool(r.get("false_success"))
        tasks.setdefault(r["task_id"], []).append(r)
    return {
        "overall": _summary(current),
        "categories": dict(sorted(categories.items())),
        "tasks": tasks,
        "records": current,
    }


def _frac(num: int, den: int) -> str:
    return f"{num}/{den} ({100 * num / den:.0f}%)" if den else "0/0 (n/a)"


def _num(value: float | None, fmt: str = "{:.0f}") -> str:
    return "n/a" if value is None else fmt.format(value)


def _overall_lines(o: Mapping[str, Any], meta: Mapping[str, Any]) -> list[str]:
    tokens = o["tokens"]
    reasoning = (
        "not reported by the provider"
        if tokens["reasoning"] is None
        else f"{tokens['reasoning']:,}"
    )
    shown = {k: v for k, v in o["outcomes"].items() if v}
    return [
        "# Browser bench report",
        "",
        f"Model `{meta.get('model', '?')}`, reasoning effort `{meta.get('effort', '?')}`, "
        f"limits: {meta.get('max_tool_calls', '?')} tool calls, "
        f"{meta.get('timeout', '?')} s per run.",
        "",
        f"{o['runs']} runs, {o['judged']} judged, {o['harness_errors']} harness errors "
        "(excluded from every rate). Each figure shows the runs behind it; with a "
        "few repeats per task these are counts, not estimates, and no intervals are given.",
        "",
        "## Overall",
        "",
        f"- Pass rate: {_frac(o['passed'], o['judged'])}",
        f"- False-success rate: {_frac(o['false_success'], o['judged'])}",
        f"- Outcomes: {', '.join(f'{k} {v}' for k, v in shown.items()) or 'none'}",
        f"- Tool calls: median {_num(o['tool_calls_median'])}, total {o['tool_calls_total']}",
        f"- Wall time per judged run: median {_num(o['wall_median_s'], '{:.1f}')} s; "
        f"total over all runs {o['wall_total_s']:.0f} s",
        f"- Tokens: input {tokens['input'] or 0:,} (cached {tokens['cached_input'] or 0:,}), "
        f"output {tokens['output'] or 0:,}, reasoning {reasoning}",
        "",
    ]


def _category_lines(categories: Mapping[str, Mapping[str, int]]) -> list[str]:
    lines = [
        "## By category",
        "",
        "| Category | Pass | False success | Harness errors |",
        "|---|---|---|---|",
    ]
    for name, c in categories.items():
        lines.append(
            f"| {name} | {_frac(c['passed'], c['judged'])} | "
            f"{_frac(c['false_success'], c['judged'])} | {c['harness_errors']} |"
        )
    return [*lines, ""]


def _task_lines(tasks: Mapping[str, list[Mapping[str, Any]]]) -> list[str]:
    lines = [
        "## By task",
        "",
        "| Task | Category | Outcomes by repeat | Pass | Tool calls (median) | Wall s (median) |",
        "|---|---|---|---|---|---|",
    ]
    for task_id, runs in tasks.items():
        judged = [r for r in runs if r["outcome"] != "harness_error"]
        passed = sum(r["outcome"] in PASSING for r in judged)
        outcomes = ", ".join(f"r{r['repeat']} {r['outcome']}" for r in runs)
        calls = _median([r["tool_calls"]["total"] for r in judged])
        wall = _median([r["wall_s"] for r in judged])
        lines.append(
            f"| {task_id} | {runs[0]['category']} | {outcomes} | "
            f"{_frac(passed, len(judged))} | {_num(calls)} | {_num(wall, '{:.1f}')} |"
        )
    return [*lines, ""]


def _injection_lines(records: list[Mapping[str, Any]]) -> list[str]:
    rows = [r for r in records if r["category"] == "injection"]
    if not rows:
        return []
    lines = [
        "## Injection",
        "",
        "| Run | Main task done | Acted on planted text | Attacker goal | Outcome |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        c = r.get("check") or {}
        lines.append(
            f"| {r['task_id']} r{r['repeat']} | {c.get('done')} | {c.get('acted_on')} | "
            f"{c.get('attacker_goal')} | {r['outcome']} |"
        )
    return [*lines, ""]


def _egress_lines(records: list[Mapping[str, Any]]) -> list[str]:
    rows = [r for r in records if r.get("egress")]
    if not rows:
        return []
    lines = [
        "## Egress",
        "",
        "| Run | Internal paths reached | Private/metadata tried | Private/metadata reached "
        "| Reported internal figure | Verdict / answer |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        e = r["egress"]
        lines.append(
            f"| {r['task_id']} r{r['repeat']} | {', '.join(e['internal_hit']) or 'none'} | "
            f"{', '.join(e['private_or_metadata_attempted']) or 'none'} | "
            f"{', '.join(e['private_or_metadata_reached']) or 'none'} | "
            f"{e['reported_internal_figure']} | {e['agent_verdict']} / {e['agent_answer']} |"
        )
    return [*lines, ""]


def _failure_lines(records: list[Mapping[str, Any]]) -> list[str]:
    failed = [r for r in records if r["outcome"] not in PASSING]
    lines = ["## Failures", ""]
    if not failed:
        return [*lines, "None.", ""]
    for r in failed:
        lines.append(
            f"- `{r['task_id']}` r{r['repeat']} **{r['outcome']}**: {failure_reason(r)}"
        )
    return [*lines, ""]


def render(records: Iterable[Mapping[str, Any]], meta: Mapping[str, Any]) -> str:
    """The markdown report over the latest record of each task/repeat pair."""
    agg = aggregate(records)
    current = agg["records"]
    lines = [
        *_overall_lines(agg["overall"], meta),
        *_category_lines(agg["categories"]),
        *_task_lines(agg["tasks"]),
        *_injection_lines(current),
        *_egress_lines(current),
        *_failure_lines(current),
    ]
    return "\n".join(lines)
