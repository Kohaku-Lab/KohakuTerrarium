"""Token accounting over a run directory: calibration, per-call attribution, aggregates.

Counts are characters per context component (``usage.compose``) turned into
tokens with one calibration fitted on the calls that carry provider usage:
``input ≈ a·chars + b·messages + c``. The fit's residual is reported, never
hidden. Runs recorded before per-call accounting are rebuilt from their
transcripts: per-call composition is estimated, and only the run total of
provider usage is known.
"""

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any

from .store import RunStore
from .usage import compose, schema_chars

DEFAULT_TOKENS_PER_CHAR = 0.25
MIN_FIT_CALLS = 5
FIXED_AREAS = ("system", "tools", "user")


@dataclass(frozen=True)
class Calibration:
    """``tokens ≈ per_char·chars + per_message·messages + per_call``, with its fit quality."""

    per_char: float = DEFAULT_TOKENS_PER_CHAR
    per_message: float = 0.0
    per_call: float = 0.0
    calls: int = 0
    r2: float | None = None
    median_abs_residual_pct: float | None = None
    max_abs_residual_pct: float | None = None
    source: str = "default"

    def estimate(self, chars: int, messages: int) -> float:
        return self.per_char * chars + self.per_message * messages + self.per_call


def _solve(m: list[list[float]], v: list[float]) -> list[float] | None:
    """Solve a small linear system by Gaussian elimination; ``None`` when singular."""
    n = len(v)
    a = [row[:] + [rhs] for row, rhs in zip(m, v)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-9:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r != col:
                f = a[r][col] / a[col][col]
                a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return [a[i][n] / a[i][i] for i in range(n)]


def _least_squares(
    xs: list[tuple[float, ...]], ys: list[float], columns: list[int]
) -> list[float] | None:
    """Least squares on the chosen columns; zero for the others."""
    sub = [[x[c] for c in columns] for x in xs]
    k = len(columns)
    xtx = [[sum(r[i] * r[j] for r in sub) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * y for r, y in zip(sub, ys)) for i in range(k)]
    coef = _solve(xtx, xty)
    if coef is None:
        return None
    out = [0.0, 0.0, 0.0]
    for c, value in zip(columns, coef):
        out[c] = value
    return out


def _nonnegative_fit(xs: list[tuple[float, ...]], ys: list[float]) -> list[float]:
    """Least squares that drops a column whose coefficient comes out negative."""
    columns = [0, 1, 2]
    while columns:
        coef = _least_squares(xs, ys, columns)
        if coef is None:
            columns.pop()
            continue
        negative = [c for c in columns if coef[c] < 0]
        if not negative:
            return coef
        columns.remove(max(negative, key=lambda c: -coef[c]))
    return [DEFAULT_TOKENS_PER_CHAR, 0.0, 0.0]


def context_chars(context: Mapping[str, int]) -> int:
    return sum(v for k, v in context.items() if k != "image_parts")


def split_fixed(
    context: Mapping[str, int], probe: Mapping[str, int] | None
) -> tuple[int, int]:
    """Measured tokens of the probed components, and the characters of the rest."""
    probe = probe or {}
    measured = sum(probe[k] for k in context if k in probe)
    rest = sum(v for k, v in context.items() if k != "image_parts" and k not in probe)
    return measured, rest


def fit(samples: Iterable[tuple[int, ...]], source: str = "fit") -> Calibration:
    """Least squares over ``(chars, messages, target[, provider_input])``.

    ``target`` is the provider input less any measured fixed tokens; the
    residual percentages are taken against the full provider input (the
    fourth value, defaulting to ``target``). Too few samples give the default.
    """
    rows = [
        (float(s[0]), float(s[1]), float(s[2]), float(s[3] if len(s) > 3 else s[2]))
        for s in samples
    ]
    if len(rows) < MIN_FIT_CALLS:
        return Calibration(calls=len(rows))
    xs = [(c, m, 1.0) for c, m, _, _ in rows]
    ys = [r[2] for r in rows]
    coef = _nonnegative_fit(xs, ys)
    cal = Calibration(coef[0], coef[1], coef[2], len(rows), source=source)
    preds = [cal.estimate(int(c), int(m)) for c, m, _, _ in rows]
    totals = [r[3] for r in rows]
    mean = sum(totals) / len(totals)
    ss_tot = sum((t - mean) ** 2 for t in totals)
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, preds))
    pct = sorted(abs(y - p) / t * 100 for y, p, t in zip(ys, preds, totals) if t)
    return Calibration(
        coef[0],
        coef[1],
        coef[2],
        len(rows),
        r2=(1 - ss_res / ss_tot) if ss_tot else None,
        median_abs_residual_pct=median(pct) if pct else None,
        max_abs_residual_pct=pct[-1] if pct else None,
        source=source,
    )


def split_runs(
    records: Iterable[Mapping[str, Any]],
) -> tuple[list[dict], list[dict]]:
    """The judged latest record of each pair, and every record left out of totals.

    A pair's earlier attempts (superseded by a retry) and harness-error runs
    are excluded so a retried run is never counted twice and an aborted run
    never pollutes the totals.
    """
    records = [dict(r) for r in records]
    last = {(r["task_id"], int(r["repeat"])): i for i, r in enumerate(records)}
    judged, excluded = [], []
    for i, r in enumerate(records):
        is_last = last[(r["task_id"], int(r["repeat"]))] == i
        if is_last and r["outcome"] != "harness_error":
            judged.append(r)
        else:
            excluded.append(r)
    return judged, excluded


def _assistant_indices(messages: list[Mapping[str, Any]]) -> list[int]:
    return [i for i, m in enumerate(messages) if m.get("role") == "assistant"]


def _output_parts(message: Mapping[str, Any] | None) -> dict[str, int]:
    if not message:
        return {"text": 0, "tool_args": 0}
    content = message.get("content")
    if isinstance(content, list):
        text = sum(len(p.get("text", "")) for p in content if isinstance(p, dict))
    else:
        text = len(content or "")
    args = 0
    for call in message.get("tool_calls") or []:
        fn = call.get("function", call)
        raw = fn.get("arguments") or ""
        args += len(raw if isinstance(raw, str) else json.dumps(raw))
    return {"text": text, "tool_args": args}


def calls_of(
    record: Mapping[str, Any],
    transcript: Mapping[str, Any] | None,
    static_tools: Mapping[str, int] | None,
) -> list[dict[str, Any]]:
    """Per-call rows of one run: composition, provider usage if recorded, output parts."""
    messages = list((transcript or {}).get("messages") or [])
    assistants = [messages[i] for i in _assistant_indices(messages)]
    recorded = record.get("llm_calls")
    rows = []
    if recorded:
        for k, call in enumerate(recorded):
            rows.append(
                {
                    "round": call.get("round", k),
                    "messages": call.get("messages", 0),
                    "context": dict(call.get("context") or {}),
                    "usage": call.get("usage"),
                    "wire": call.get("wire"),
                    "prefix": call.get("prefix"),
                    "estimated": False,
                    "output_parts": _output_parts(
                        assistants[k] if k < len(assistants) else None
                    ),
                }
            )
        return rows
    for k, idx in enumerate(_assistant_indices(messages)):
        context = compose(messages[:idx])
        for name, chars in (static_tools or {}).items():
            context[f"tools.{name}"] = chars
        rows.append(
            {
                "round": k,
                "messages": idx,
                "context": context,
                "usage": None,
                "wire": None,
                "prefix": None,
                "estimated": True,
                "output_parts": _output_parts(messages[idx]),
            }
        )
    return rows


def static_tools_from(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Tool schema characters as recorded by an instrumented call."""
    for row in rows:
        tools = {
            k.split(".", 1)[1]: v
            for k, v in row["context"].items()
            if k.startswith("tools.")
        }
        if tools:
            return tools
    return {}


def schema_chars_of(tools: Iterable[Any]) -> dict[str, int]:
    return dict(schema_chars(t) for t in tools)


def area(component: str) -> str:
    """``system`` / ``tools`` / ``user`` / ``assistant`` / ``tool_result`` / other."""
    head = component.split(".", 1)[0]
    return (
        head
        if head in ("system", "tools", "user", "assistant", "tool_result")
        else "other"
    )


def load_transcript(store: RunStore, record: Mapping[str, Any]) -> dict | None:
    attempt = record.get("attempt", 0)
    tag = f".a{attempt}" if attempt else ""
    path = store.transcript_path(
        record["task_id"], int(record["repeat"]), f"{tag}.json"
    )
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _q(values: list[float]) -> float | None:
    return median(values) if values else None


def analyse(
    out: Path,
    calibration: Calibration | None = None,
    static_tools: Mapping[str, int] | None = None,
    probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything ``tokens.md`` and ``tokens.json`` show, for one run directory.

    ``probe`` is a ``probe.py`` result: its measured per-call tokens replace
    the character estimate for the system sections and tool schemas, and the
    calibration is fitted on what is left.
    """
    store = RunStore(out)
    judged, excluded = split_runs(store.load())
    fixed = dict((probe or {}).get("components") or {})
    tools = static_tools or (probe or {}).get("tool_chars")
    runs = []
    for record in judged:
        transcript = load_transcript(store, record)
        rows = calls_of(record, transcript, tools)
        runs.append({"record": record, "calls": rows})
    samples = []
    for run in runs:
        for c in run["calls"]:
            if c["usage"] and not c["usage"].get("source"):
                measured, rest = split_fixed(c["context"], fixed)
                total = c["usage"]["input"]
                samples.append((rest, c["messages"], total - measured, total))
    if calibration is None:
        calibration = fit(samples, source=str(out))
    tools = tools or static_tools_from(c for run in runs for c in run["calls"])
    return _aggregate(runs, excluded, calibration, tools, bool(samples), fixed)


def _aggregate(
    runs: list[dict],
    excluded: list[dict],
    cal: Calibration,
    static_tools: Mapping[str, int],
    per_call_usage: bool,
    probed: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    probed = probed or {}
    comp: dict[str, float] = defaultdict(float)
    comp_by_tool: dict[str, float] = defaultdict(float)
    by_round: dict[int, list[dict]] = defaultdict(list)
    per_run = []
    est_total = framing_total = 0.0
    calls_n = prefix_checked = prefix_changed = 0
    out_split = {"reasoning": 0, "text_est": 0.0, "tool_args_est": 0.0, "output": 0}
    reasoning_known = False
    for run in runs:
        rec = run["record"]
        run_est = 0.0
        fixed = history = 0.0
        prefixes = [c["prefix"] for c in run["calls"] if c.get("prefix")]
        prefix_checked += len(prefixes)
        prefix_changed += sum(p != prefixes[0] for p in prefixes[1:])
        for call in run["calls"]:
            calls_n += 1
            framing = cal.per_message * call["messages"] + cal.per_call
            framing_total += framing
            for key, chars in call["context"].items():
                if key == "image_parts":
                    continue
                tokens = probed[key] if key in probed else chars * cal.per_char
                comp[key] += tokens
                if key.startswith("tool_result."):
                    comp_by_tool[key.split(".")[1]] += tokens
                if area(key) in FIXED_AREAS:
                    fixed += tokens
                else:
                    history += tokens
            measured, rest = split_fixed(call["context"], probed)
            estimate = measured + cal.estimate(rest, call["messages"])
            run_est += estimate
            usage = call["usage"]
            by_round[call["round"]].append(
                {
                    "input": usage["input"] if usage else None,
                    "cached": usage["cached_input"] if usage else None,
                    "estimate": estimate,
                }
            )
            parts = call["output_parts"]
            out_split["text_est"] += parts["text"] * cal.per_char
            out_split["tool_args_est"] += parts["tool_args"] * cal.per_char
            if usage:
                out_split["output"] += usage.get("output") or 0
                if usage.get("reasoning") is not None:
                    reasoning_known = True
                    out_split["reasoning"] += usage["reasoning"]
        est_total += run_est
        tokens = rec.get("tokens") or {}
        per_run.append(
            {
                "task_id": rec["task_id"],
                "category": rec["category"],
                "repeat": rec["repeat"],
                "outcome": rec["outcome"],
                "rounds": len(run["calls"]) or rec.get("llm_rounds", 0),
                "input": tokens.get("input") or 0,
                "cached_input": tokens.get("cached_input") or 0,
                "output": tokens.get("output") or 0,
                "reasoning": tokens.get("reasoning"),
                "estimate": round(run_est),
                "fixed_est": round(fixed + cal.per_call * len(run["calls"])),
                "history_est": round(
                    history + cal.per_message * sum(c["messages"] for c in run["calls"])
                ),
            }
        )
    provider_input = sum(r["input"] for r in per_run)
    if not out_split["output"]:
        out_split["output"] = sum(r["output"] for r in per_run)
    return {
        "calibration": asdict(cal),
        "probed_components": dict(probed),
        "per_call_usage": per_call_usage,
        "prefix": {
            "calls_checked": prefix_checked,
            "changed_within_run": prefix_changed,
        },
        "static_tools": dict(static_tools),
        "totals": {
            "runs": len(per_run),
            "calls": calls_n,
            "input": provider_input,
            "cached_input": sum(r["cached_input"] for r in per_run),
            "output": sum(r["output"] for r in per_run),
            "reasoning": out_split["reasoning"] if reasoning_known else None,
            "estimate": round(est_total),
            "framing_est": round(framing_total),
            "residual": provider_input - round(est_total),
        },
        "components": {
            k: round(v) for k, v in sorted(comp.items(), key=lambda kv: -kv[1])
        },
        "tool_results_by_tool": {
            k: round(v) for k, v in sorted(comp_by_tool.items(), key=lambda kv: -kv[1])
        },
        "output_split": {
            "output": out_split["output"],
            "reasoning": out_split["reasoning"] if reasoning_known else None,
            "visible_text_est": round(out_split["text_est"]),
            "tool_args_est": round(out_split["tool_args_est"]),
        },
        "by_round": {
            str(k): {
                "calls": len(v),
                "median_input": _q([x["input"] for x in v if x["input"] is not None]),
                "median_cached": _q(
                    [x["cached"] for x in v if x["cached"] is not None]
                ),
                "median_estimate": round(_q([x["estimate"] for x in v]) or 0),
            }
            for k, v in sorted(by_round.items())
        },
        "runs": per_run,
        "excluded": [
            {
                "task_id": r["task_id"],
                "repeat": r["repeat"],
                "attempt": r.get("attempt", 0),
                "outcome": r["outcome"],
                "tokens": r.get("tokens"),
            }
            for r in excluded
        ],
    }
