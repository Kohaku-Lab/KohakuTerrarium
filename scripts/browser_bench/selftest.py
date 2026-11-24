"""The benchmark self-test: run every oracle and adversary against the site and the checks.

Prints one line per run (verdict, run kind, task id, category, check summary,
seconds) and exits non-zero when any run's check outcome differs from what
that run must produce. Needs Playwright and Chromium.

    python -m scripts.browser_bench [--only ID,ID] [--kind oracle|adversary] [-v]
"""

import argparse
import asyncio
import shutil
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kohakuterrarium.browser.options import BrowserOptions
from kohakuterrarium.browser.session import BrowserSession

from .model import CheckResult, check
from .oracles import RUNS, Run
from .oracles.bot import Bot
from .site import BenchSite, running_site
from .tasks import BY_ID

RUN_TIMEOUT_S = 120.0


@dataclass
class Outcome:
    """What one scripted run produced and whether it matched its expectation."""

    run: Run
    result: CheckResult | None
    seconds: float
    error: str = ""
    findings: list[str] = field(default_factory=list)
    trace: list[tuple[str, float]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.error and self.result is not None and self.run.expect(self.result)
        )


def base_options(site: BenchSite) -> dict[str, Any]:
    """Session options for the site: loopback on, only the site's two web ports allowed."""
    return {
        "allow_loopback": True,
        "allow_hosts": [
            f"127.0.0.1:{site.port('main')}",
            f"127.0.0.1:{site.port('third')}",
        ],
        "action_timeout_s": 5.0,
        "nav_timeout_s": 15.0,
    }


async def run_one(
    site: BenchSite, run: Run, base: Mapping[str, Any], root: Path
) -> Outcome:
    """Reset the task, drive it with a fresh session, judge the recorded state."""
    task = BY_ID[run.task_id]
    site.reset(task.id)
    workdir = root / f"{task.id}-{run.kind}"
    files_dir = workdir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    options = BrowserOptions.from_mapping({**base, **run.overrides(site)})
    session = BrowserSession(options, downloads_dir=workdir / "downloads")
    bot = Bot(session, site, task, workdir)
    for name, content in task.files.items():
        path = files_dir / name
        path.write_bytes(content)
        bot.files[name] = str(path)
    started = time.monotonic()
    error = ""
    try:
        await asyncio.wait_for(run.script(bot), RUN_TIMEOUT_S)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        seconds = time.monotonic() - started
        closing = time.monotonic()
        await session.close()
        bot.trace.append(("close", time.monotonic() - closing))
    state = site.state(task.id)
    state["egress"] = bot.egress
    return Outcome(run, check(task, state), seconds, error, bot.findings, bot.trace)


def line(outcome: Outcome) -> str:
    task = BY_ID[outcome.run.task_id]
    verdict = "OK  " if outcome.ok else "FAIL"
    summary = outcome.result.summary() if outcome.result else "no result"
    return (
        f"{verdict} {outcome.run.kind:<9} {task.id:<24} {task.category:<12} "
        f"{summary:<62} {outcome.seconds:5.1f}s"
    )


async def run_all(runs: Sequence[Run], verbose: bool) -> list[Outcome]:
    outcomes = []
    root = Path(tempfile.mkdtemp(prefix="browser-bench-"))
    try:
        with running_site() as site:
            base = base_options(site)
            for run in runs:
                outcome = await run_one(site, run, base, root)
                outcomes.append(outcome)
                print(line(outcome), flush=True)
                if outcome.error and (verbose or not outcome.ok):
                    print(f"     error: {outcome.error}", flush=True)
                if verbose or not outcome.ok:
                    if outcome.result is not None:
                        print(
                            f"     details: {dict(outcome.result.details)}", flush=True
                        )
                    print(f"     expected: {outcome.run.expectation}", flush=True)
                if verbose:
                    steps = ", ".join(f"{w} {s:.1f}s" for w, s in outcome.trace)
                    print(f"     steps ({len(outcome.trace)}): {steps}", flush=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return outcomes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default="", help="comma-separated task ids")
    parser.add_argument("--kind", choices=("oracle", "adversary"), default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    wanted = {t for t in args.only.split(",") if t}
    unknown = wanted - set(BY_ID)
    if unknown:
        parser.error(f"unknown task ids: {', '.join(sorted(unknown))}")
    runs = [
        r
        for r in RUNS
        if (not wanted or r.task_id in wanted) and (args.kind in (None, r.kind))
    ]
    started = time.monotonic()
    outcomes = asyncio.run(run_all(runs, args.verbose))
    failed = [o for o in outcomes if not o.ok]
    findings = sorted({f"{o.run.task_id}: {f}" for o in outcomes for f in o.findings})
    if findings:
        print("\nFindings:")
        for finding in findings:
            print(f"  - {finding}")
    print(
        f"\n{len(outcomes) - len(failed)}/{len(outcomes)} runs matched their expected "
        f"check outcome in {time.monotonic() - started:.1f}s"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
