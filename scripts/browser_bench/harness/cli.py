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

import kohakuterrarium
from kohakuterrarium.llm.codex.presets import PRESETS as CODEX_PRESETS
from kohakuterrarium.llm.profiles.types import LLMProfile

from .. import model as bench_model
from ..model import Task
from ..tasks import BY_ID, TASKS
from .report import render
from .runner import Limits, run_all
from .store import RunStore, plan
from .wire import OMIT_HINT, install, tool_rewriter

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
    min_free_gb: float = 5.0
    websocket: bool = True
    expect_root: Path | None = None
    cache_key: str = "shared"
    tool_strict: str = "unset"
    act_hint: str = "default"

    def meta(self) -> dict:
        return {
            "model": self.model,
            "effort": self.effort,
            "provider": "codex",
            "max_tool_calls": self.max_tool_calls,
            "timeout": self.timeout,
            "websocket": self.websocket,
            "cache_key": self.cache_key,
            "tool_strict": self.tool_strict,
            "act_hint": self.act_hint,
        }

    def tool_rewriter(self):
        """The outgoing tool-list rewrite these settings ask for."""
        strict = {"unset": None, "false": False, "true": True}[self.tool_strict]
        hint = OMIT_HINT if self.act_hint == "omit" else None
        return tool_rewriter(strict=strict, act_hint=hint)


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


def model_profile(model: str, effort: str, websocket: bool = True) -> LLMProfile:
    """A Codex (ChatGPT subscription) profile for any model id, at ``effort``.

    A listed Codex preset lends its context and output limits; any other id
    runs with the Codex defaults, and the provider decides whether it exists.
    ``websocket=False`` sends every call over HTTP with the full context.
    """
    preset = CODEX_PRESETS.get(model, {})
    extra = deepcopy(preset.get("extra_body", {"websocket_mode": True}))
    extra["websocket_mode"] = websocket
    return LLMProfile(
        name=f"browser-bench/{model}",
        model=model,
        provider="codex",
        backend_type="codex",
        max_context=preset.get("max_context", DEFAULT_CONTEXT),
        max_output=preset.get("max_output", DEFAULT_OUTPUT),
        reasoning_effort=effort,
        extra_body=extra,
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
        "--min-free-gb",
        type=float,
        default=5.0,
        help="start a run only while this much memory is free",
    )
    parser.add_argument(
        "--force", action="store_true", help="rerun pairs that already have a record"
    )
    parser.add_argument(
        "--report-only", action="store_true", help="rewrite report.md and exit"
    )
    parser.add_argument(
        "--no-websocket",
        dest="websocket",
        action="store_false",
        help="send every call over HTTP instead of the WebSocket continuation",
    )
    parser.add_argument(
        "--cache-key",
        choices=("shared", "per-run"),
        default="shared",
        help="prompt_cache_key: the provider default (shared) or one per run",
    )
    parser.add_argument(
        "--tool-strict",
        choices=("unset", "false", "true"),
        default="unset",
        help="send this `strict` on every function tool (unset: as the provider does)",
    )
    parser.add_argument(
        "--act-hint",
        choices=("default", "omit"),
        default="default",
        help="browser_act actions description: the product's, or 'give only used fields'",
    )
    parser.add_argument(
        "--expect-root",
        type=Path,
        default=None,
        help="refuse to run unless the framework and the bench load from this tree",
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
        min_free_gb=args.min_free_gb,
        websocket=args.websocket,
        expect_root=args.expect_root,
        cache_key=args.cache_key,
        tool_strict=args.tool_strict,
        act_hint=args.act_hint,
    )


def _line(record: dict) -> str:
    tokens = record["tokens"]
    return (
        f"{record['outcome']:<16} {record['task_id']:<24} r{record['repeat']} "
        f"calls={record['tool_calls']['total']:<3} rounds={record['llm_rounds']:<3} "
        f"in={tokens['input']} out={tokens['output']} {record['wall_s']:.0f}s"
        + (f"  [{record['reason']}]" if record.get("reason") else "")
    )


def code_roots() -> dict[str, str]:
    """Where the framework and the bench package were imported from."""
    return {
        "kohakuterrarium": str(Path(kohakuterrarium.__file__).resolve().parent),
        "browser_bench": str(Path(bench_model.__file__).resolve().parent),
    }


def outside(roots: dict[str, str], root: Path) -> list[str]:
    """The names in ``roots`` whose path does not lie under ``root``."""
    base = root.resolve()
    return [name for name, path in roots.items() if not Path(path).is_relative_to(base)]


def write_report(store: RunStore, meta: dict) -> Path:
    path = store.out / "report.md"
    path.write_text(render(store.load(), meta), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    settings = parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    roots = code_roots()
    for name, path in roots.items():
        print(f"{name}: {path}")
    if settings.expect_root is not None:
        stray = outside(roots, settings.expect_root)
        if stray:
            print(
                f"error: loaded outside {settings.expect_root}: {stray}",
                file=sys.stderr,
            )
            return 3
    store = RunStore(settings.out)
    meta = {**settings.meta(), "code_roots": roots}
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
    install(settings.tool_rewriter())
    started = time.monotonic()
    asyncio.run(
        run_all(
            [(BY_ID[task_id], repeat) for task_id, repeat in todo],
            llm_for_run=lambda: model_profile(
                settings.model, settings.effort, settings.websocket
            ),
            limits=Limits(
                settings.max_tool_calls,
                settings.timeout,
                per_run_cache_key=settings.cache_key == "per-run",
            ),
            store=store,
            meta=meta,
            concurrency=settings.concurrency,
            on_record=lambda r: print(_line(r), flush=True),
            min_free_gb=settings.min_free_gb,
        )
    )
    path = write_report(store, meta)
    print(f"done in {time.monotonic() - started:.0f}s; report: {path}")
    return 0
