"""``tokens.md`` / ``tokens.json``: where a run directory's tokens went.

    python -m scripts.browser_bench.harness.tokens --out DIR [--calibrate-from DIR]

``--calibrate-from`` borrows the calibration and tool-schema sizes of an
instrumented run directory, for directories recorded before per-call
accounting (their per-call composition is rebuilt from transcripts).
"""

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any

from .accounting import Calibration, analyse, area
from .verbosity import dir_costs


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def _n(value: float | None) -> str:
    return "n/a" if value is None else f"{round(value):,}"


def _table(head: Sequence[str], rows: list[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return [*lines, ""]


def _totals(a: Mapping[str, Any]) -> list[str]:
    t, cal = a["totals"], a["calibration"]
    fit = (
        f"fitted on {cal['calls']} calls with provider usage: "
        f"{cal['per_char']:.4f} tokens/char, {cal['per_message']:.1f} per message, "
        f"{cal['per_call']:.0f} per call; R² {cal['r2']:.4f}, median |residual| "
        f"{cal['median_abs_residual_pct']:.1f}%, max {cal['max_abs_residual_pct']:.1f}%"
        if cal.get("r2") is not None
        else f"not fitted ({cal['source']}): {cal['per_char']} tokens/char"
    )
    lines = [
        "# Token accounting",
        "",
        "Provider figures are measured. Component figures are character counts "
        "turned into tokens with one calibration; the residual line says how far "
        "the split can be trusted.",
        "",
        f"Calibration: {fit}.",
        "",
        "System sections and tool schemas: "
        + (
            "measured per call with probe.py (tokens by difference)."
            if a.get("probed_components")
            else "estimated from characters (no probe given)."
        ),
        "",
        "Per-call provider usage: "
        + ("recorded" if a["per_call_usage"] else "not recorded (run totals only)"),
        "",
        "## Totals",
        "",
    ]
    rows = [
        ["Runs (judged, latest per task/repeat)", t["runs"]],
        ["LLM calls", t["calls"]],
        ["Input tokens (provider)", _n(t["input"])],
        [
            "  cached",
            f"{_n(t['cached_input'])} ({_pct(t['cached_input'], t['input'])})",
        ],
        ["  uncached", _n(t["input"] - t["cached_input"])],
        ["Output tokens (provider)", _n(t["output"])],
        ["  reasoning (provider)", _n(t["reasoning"])],
        ["Input estimated from components", _n(t["estimate"])],
        [
            "Residual (provider − estimate)",
            f"{_n(t['residual'])} ({_pct(t['residual'], t['input'])})",
        ],
        [
            "Calls whose system prompt or tool list differed from the run's first",
            (
                f"{a['prefix']['changed_within_run']} of {a['prefix']['calls_checked']}"
                if a.get("prefix", {}).get("calls_checked")
                else "not recorded"
            ),
        ],
    ]
    return lines + _table(["Figure", "Value"], rows)


def _areas(a: Mapping[str, Any]) -> list[str]:
    total = a["totals"]["input"] or a["totals"]["estimate"]
    by_area: dict[str, float] = defaultdict(float)
    for key, tokens in a["components"].items():
        by_area[area(key)] += tokens
    by_area["framing (per message + per call)"] = a["totals"]["framing_est"]
    rows = [
        [k, _n(v), _pct(v, total)]
        for k, v in sorted(by_area.items(), key=lambda kv: -kv[1])
    ]
    rows.append(
        ["residual", _n(a["totals"]["residual"]), _pct(a["totals"]["residual"], total)]
    )
    lines = ["## Input by area (summed over every call)", ""]
    return lines + _table(["Area", "Tokens", "Share of provider input"], rows)


def _components(a: Mapping[str, Any]) -> list[str]:
    total = a["totals"]["input"] or a["totals"]["estimate"]
    calls = max(1, a["totals"]["calls"])
    rows = [
        [k, _n(v), _pct(v, total), _n(v / calls)] for k, v in a["components"].items()
    ]
    lines = ["## Input by component", ""]
    return lines + _table(["Component", "Tokens", "Share", "Per call"], rows)


def _fixed_history(a: Mapping[str, Any]) -> list[str]:
    runs = a["runs"]
    fixed = sum(r["fixed_est"] for r in runs)
    history = sum(r["history_est"] for r in runs)
    calls = max(1, a["totals"]["calls"])
    lines = [
        "## Fixed per-call overhead vs history",
        "",
        "Fixed: system prompt, tool schemas, the user message and the per-call "
        "constant, paid on every call. History: everything the run added "
        "(assistant turns, tool calls, tool results) and per-message framing.",
        "",
    ]
    rows = [
        ["Fixed", _n(fixed), _pct(fixed, fixed + history), _n(fixed / calls)],
        ["History", _n(history), _pct(history, fixed + history), _n(history / calls)],
    ]
    return lines + _table(["Part", "Tokens", "Share", "Per call"], rows)


def _rounds(a: Mapping[str, Any]) -> list[str]:
    rows = [
        [
            k,
            v["calls"],
            _n(v["median_input"]),
            _n(v["median_cached"]),
            _n(v["median_estimate"]),
        ]
        for k, v in a["by_round"].items()
    ]
    lines = ["## Input and cache by round index", ""]
    return lines + _table(
        ["Round", "Calls", "Median input", "Median cached", "Median estimate"], rows
    )


def _tools(a: Mapping[str, Any]) -> list[str]:
    total = a["totals"]["input"] or a["totals"]["estimate"]
    lines = ["## Tool results (re-sent cost, summed over every call)", ""]
    rows = [[k, _n(v), _pct(v, total)] for k, v in a["tool_results_by_tool"].items()]
    lines += _table(["Tool", "Tokens", "Share"], rows)
    parts: dict[str, float] = defaultdict(float)
    for key, tokens in a["components"].items():
        if key.startswith("tool_result."):
            parts[key.rsplit(".", 1)[1]] += tokens
    lines += ["### By part", ""]
    rows = [
        [k, _n(v), _pct(v, total)]
        for k, v in sorted(parts.items(), key=lambda kv: -kv[1])
    ]
    lines += _table(["Part", "Tokens", "Share"], rows)
    schemas = a.get("static_tools") or {}
    if schemas:
        cal = a["calibration"]["per_char"]
        probed = a.get("probed_components") or {}
        lines += ["### Tool schemas sent on every call", ""]
        rows = []
        for name, chars in sorted(schemas.items(), key=lambda kv: -kv[1]):
            measured = probed.get(f"tools.{name}")
            rows.append(
                [
                    name,
                    chars,
                    _n(measured if measured is not None else chars * cal),
                    "measured" if measured is not None else "estimated",
                ]
            )
        lines += _table(["Tool", "Chars", "Tokens per call", "Source"], rows)
    return lines


def _group(runs: list[Mapping[str, Any]], key: str) -> list[list[Any]]:
    groups: dict[str, list] = defaultdict(list)
    for r in runs:
        groups[r[key]].append(r)
    rows = []
    for name, rs in sorted(groups.items()):
        rows.append(
            [
                name,
                len(rs),
                _n(median(r["rounds"] for r in rs)),
                _n(median(r["input"] for r in rs)),
                _n(sum(r["input"] for r in rs)),
                _n(sum(r["cached_input"] for r in rs)),
                _n(median(r["output"] for r in rs)),
                _n(sum(r["output"] for r in rs)),
            ]
        )
    return rows


def _per_group(a: Mapping[str, Any]) -> list[str]:
    head = [
        "{}",
        "Runs",
        "Median rounds",
        "Median input",
        "Total input",
        "Total cached",
        "Median output",
        "Total output",
    ]
    lines = ["## By task", ""]
    lines += _table([head[0].format("Task"), *head[1:]], _group(a["runs"], "task_id"))
    lines += ["## By category", ""]
    lines += _table(
        [head[0].format("Category"), *head[1:]], _group(a["runs"], "category")
    )
    return lines


def _output(a: Mapping[str, Any]) -> list[str]:
    o = a["output_split"]
    rows = [
        ["Output (provider)", _n(o["output"])],
        ["  reasoning (provider)", _n(o["reasoning"])],
        ["  visible text (est.)", _n(o["visible_text_est"])],
        ["  tool-call arguments (est.)", _n(o["tool_args_est"])],
    ]
    if o["reasoning"] is not None:
        rest = o["output"] - o["reasoning"] - o["visible_text_est"] - o["tool_args_est"]
        rows.append(["  unattributed", _n(rest)])
    return ["## Output", ""] + _table(["Part", "Tokens"], rows)


def _rounds_vs_tokens(a: Mapping[str, Any]) -> list[str]:
    groups: dict[int, list] = defaultdict(list)
    for r in a["runs"]:
        groups[r["rounds"]].append(r)
    rows = [
        [
            k,
            len(v),
            _n(median(r["input"] for r in v)),
            _n(median(r["output"] for r in v)),
        ]
        for k, v in sorted(groups.items())
    ]
    return ["## Rounds vs tokens", ""] + _table(
        ["Rounds", "Runs", "Median input", "Median output"], rows
    )


PAGE_ROWS = (
    ("page_body", "Page bodies (all views)"),
    (
        "superseded_body",
        "  of which superseded by a later navigation or full snapshot of the tab",
    ),
    ("structural_lines", "  of which container-only lines (generic, group, ...)"),
    ("ref_marks", "  of which [ref=...] marks"),
    ("unused_ref_marks", "    of which refs the run never used"),
    ("wrapper", "page_content open/close tags"),
)


def _page_history(a: Mapping[str, Any]) -> list[str]:
    hist = a.get("page_history")
    if not hist or not hist["chars"].get("page_body"):
        return []
    total = a["totals"]["input"] or a["totals"]["estimate"]
    rate = hist["tokens_per_char"]
    lines = [
        "## Page content in history (re-sent cost)",
        "",
        "Characters of page content summed over every call that re-sent them, "
        f"turned into tokens at {rate:.3f} tokens/char ({hist['density_source']}). "
        "The rows below the first are overlapping subsets of it.",
        "",
    ]
    rows = [
        [label, f"{hist['chars'].get(key, 0):,}", _n(hist["chars"].get(key, 0) * rate)]
        + [_pct(hist["chars"].get(key, 0) * rate, total)]
        for key, label in PAGE_ROWS
    ]
    return lines + _table(["Part", "Chars", "Tokens (est.)", "Share of input"], rows)


def page_history(
    out: Path, calibration: Mapping[str, Any], probe: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Page-content costs of a run directory with the density used to price them."""
    sample = (probe or {}).get("sample") or {}
    if sample.get("tokens_per_char"):
        rate, source = sample["tokens_per_char"], "probe sample"
    else:
        rate, source = calibration["per_char"], "calibration"
    return {
        "chars": dir_costs(out),
        "tokens_per_char": rate,
        "density_source": source,
    }


def _excluded(a: Mapping[str, Any]) -> list[str]:
    ex = a["excluded"]
    if not ex:
        return ["## Excluded from totals", "", "None.", ""]
    rows = [
        [
            r["task_id"],
            r["repeat"],
            r["attempt"],
            r["outcome"],
            _n((r["tokens"] or {}).get("input")),
        ]
        for r in ex
    ]
    return [
        "## Excluded from totals (superseded attempts, harness errors)",
        "",
    ] + _table(["Task", "Repeat", "Attempt", "Outcome", "Input"], rows)


def render(a: Mapping[str, Any]) -> str:
    """The markdown tables of one analysis."""
    parts = [
        _totals,
        _areas,
        _fixed_history,
        _rounds,
        _tools,
        _page_history,
        _output,
        _per_group,
        _rounds_vs_tokens,
        _components,
        _excluded,
    ]
    return "\n".join(line for part in parts for line in part(a))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--calibrate-from", type=Path, default=None)
    parser.add_argument(
        "--probe", type=Path, default=None, help="a probe.py result for the fixed parts"
    )
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    calibration, tools, probe = None, None, None
    if args.calibrate_from is not None:
        ref = json.loads(
            (args.calibrate_from / "tokens.json").read_text(encoding="utf-8")
        )
        calibration = Calibration(**ref["calibration"])
        tools = ref.get("static_tools") or None
    if args.probe is not None:
        probe = json.loads(args.probe.read_text(encoding="utf-8-sig"))
    result = analyse(args.out, calibration, tools, probe)
    result["page_history"] = page_history(args.out, result["calibration"], probe)
    (args.out / "tokens.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8"
    )
    (args.out / "tokens.md").write_text(render(result), encoding="utf-8")
    print(args.out / "tokens.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
