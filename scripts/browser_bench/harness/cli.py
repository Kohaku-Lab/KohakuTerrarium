"""Command line of the real-model harness: select tasks, pick the model, run, report.

python -m scripts.browser_bench.harness --out DIR [--model ID] [--effort LEVEL]
    [--tasks IDS_OR_CATEGORIES] [--repeats N] [--concurrency N]
    [--max-tool-calls N] [--timeout S] [--force] [--report-only]
"""

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from kohakuterrarium.llm.codex.presets import PRESETS as CODEX_PRESETS
from kohakuterrarium.llm.profiles.types import LLMProfile

from ..model import Task
from ..tasks import BY_ID, TASKS
from .report import render
from .runner import Limits, run_all
from .store import RunStore, plan

DEFAULT_MODEL = "gpt-6.2-sol"
DEFAULT_EFFORT = "high"
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
DEFAULT_CONTEXT = 272000
DEFAULT_OUTPUT = 128000


@dataclass
class Settings:
    """Everything one harness invocation needs."""

    model: str
    effort: str
    tasks: list[Task]
    repeats: int
    concurrency: int
    max_tool_calls: int
    timeout: float
    out: Path
    force: bool = False
    report_only: bool = False

    def meta(self) -> dict:
        return {
            "model": self.model,
            "effort": self.effort,
            "provider": "codex",
            "max_tool_calls": self.max_tool_calls,
            "timeout": self.timeout,
        }


def select_tasks(spec: str, tasks: Sequence[Task] = TASKS) -> list[Task]:
    """Tasks named by ``spec``: ``all``, or comma-separated task ids and categories."""
    by_id = {t.id: t for t in tasks}
    chosen: dict[str, Task] = {}
    for token in (t.strip() for t in spec.split(",")):
        if not token:
            continue
        if token == "all":
            matched = list(tasks)
        elif token in by_id:
            matched = [by_id[token]]
        else:
            matched = [t for t in tasks if t.category == token]
        if not matched:
            raise ValueError(f"unknown task id or category: {token!r}")
        for task in matched:
            chosen.setdefault(task.id, task)
    if not chosen:
        raise ValueError("no tasks selected")
    return list(chosen.values())


def model_profile(model: str, effort: str) -> LLMProfile:
    """A Codex (ChatGPT subscription) profile for any model id, at ``effort``.

    A listed Codex preset lends its context and output limits; any other id
    runs with the Codex defaults, and the provider decides whether it exists.
    """
    preset = CODEX_PRESETS.get(model, {})
    return LLMProfile(
        name=f"browser-bench/{model}",
        model=model,
        provider="codex",
        backend_type="codex",
        max_context=preset.get("max_context", DEFAULT_CONTEXT),
        max_output=preset.get("max_output", DEFAULT_OUTPUT),
        reasoning_effort=effort,
        extra_body=deepcopy(preset.get("extra_body", {"websocket_mode": True})),
    )


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.browser_bench.harness",
        description="Run a real model through the browser agent on the bench tasks.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Codex model id")
    parser.add_argument("--effort", default=DEFAULT_EFFORT, choices=EFFORTS)
    parser.add_argument(
        "--tasks", default="all", help="comma-separated task ids or categories"
    )
    parser.add_argument("--repeats", type=_positive_int, default=3)
    parser.add_argument("--concurrency", type=_positive_int, default=2)
    parser.add_argument("--max-tool-calls", type=_positive_int, default=50)
    parser.add_argument(
        "--timeout", type=_positive_float, default=900.0, help="seconds per run"
    )
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument(
        "--force", action="store_true", help="rerun pairs that already have a record"
    )
    parser.add_argument(
        "--report-only", action="store_true", help="rewrite report.md and exit"
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> Settings:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        tasks = select_tasks(args.tasks)
    except ValueError as exc:
        parser.error(str(exc))
    return Settings(
        model=args.model,
        effort=args.effort,
        tasks=tasks,
        repeats=args.repeats,
        concurrency=args.concurrency,
        max_tool_calls=args.max_tool_calls,
        timeout=args.timeout,
        out=args.out,
        force=args.force,
        report_only=args.report_only,
    )


def _line(record: dict) -> str:
    tokens = record["tokens"]
    return (
        f"{record['outcome']:<16} {record['task_id']:<24} r{record['repeat']} "
        f"calls={record['tool_calls']['total']:<3} rounds={record['llm_rounds']:<3} "
        f"in={tokens['input']} out={tokens['output']} {record['wall_s']:.0f}s"
        + (f"  [{record['reason']}]" if record.get("reason") else "")
    )


def write_report(store: RunStore, meta: dict) -> Path:
    path = store.out / "report.md"
    path.write_text(render(store.load(), meta), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    settings = parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    store = RunStore(settings.out)
    meta = settings.meta()
    if settings.report_only:
        print(write_report(store, meta))
        return 0
    try:
        store.check_meta(meta, force=settings.force)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    todo = plan(
        [t.id for t in settings.tasks],
        settings.repeats,
        store.load(),
        force=settings.force,
        model=settings.model,
    )
    print(f"{len(todo)} runs to do ({settings.model}, effort {settings.effort})")
    started = time.monotonic()
    asyncio.run(
        run_all(
            [(BY_ID[task_id], repeat) for task_id, repeat in todo],
            llm_for_run=lambda: model_profile(settings.model, settings.effort),
            limits=Limits(settings.max_tool_calls, settings.timeout),
            store=store,
            meta=meta,
            concurrency=settings.concurrency,
            on_record=lambda r: print(_line(r), flush=True),
        )
    )
    path = write_report(store, meta)
    print(f"done in {time.monotonic() - started:.0f}s; report: {path}")
    return 0
