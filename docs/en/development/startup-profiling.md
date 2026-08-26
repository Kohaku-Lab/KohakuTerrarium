# Startup profiling baseline

This document records the pre-optimization startup baseline at commit
`d8088ca9` and describes the opt-in trace used to compare later changes.

## Enable the trace

Set `KT_STARTUP_TRACE` to a JSONL base path before launching a surface. For
process-launch measurements, also set the wall-clock origin immediately before
starting the process:

```powershell
$env:KT_STARTUP_TRACE = "$PWD\startup.jsonl"
$env:KT_STARTUP_ORIGIN_NS = python -c "import time; print(time.time_ns())"
python -m kohakuterrarium web --dev
```

The tracer writes one process shard per PID, for example
`startup.1234.jsonl`, preventing desktop parent and child writes from corrupting
each other. Each line contains the event name, process ID, parent process ID,
run ID, process-local elapsed time, wall-clock time, and `startup_ms`. The latter
uses a shared `KT_STARTUP_ORIGIN_NS` propagated to child processes, so desktop
parent and child events are comparable. If callers do not supply a run ID or
origin, the first instrumented process creates and exports them, but that
automatic origin starts at trace-module import rather than OS process launch.

Tracing is disabled by default. File and serialization errors are ignored so
profiling cannot prevent startup.

## Baseline environment

- Windows
- Python 3.14.2
- Source checkout at `d8088ca9`
- Shared virtual environment, with `PYTHONPATH` forced to this worktree's `src`
- Warm operating-system file cache
- Five fresh process runs per reported median
- Minimal terminal fixture with `input: none`, `output: none`, no session
- `kt web --dev` used because `web_dist` is an untracked build artifact

These numbers are for comparison on this machine, not cross-machine budgets.

## Uninstrumented baseline

| Measurement | p50 |
| --- | ---: |
| Python empty process | 44.9 ms |
| `import kohakuterrarium` | 4953.3 ms |
| `import kohakuterrarium.__main__` | 4928.2 ms |
| `import kohakuterrarium.cli` | 5780.5 ms |
| `python -m kohakuterrarium --version` | 5881.0 ms |
| `kt web --dev` to `/healthz` HTTP 200 | 5218.9 ms |

`-X importtime` loaded 4695 modules for `kohakuterrarium.cli`. Its main
cumulative chain was `kohakuterrarium` -> `studio` ->
`studio.editors.workspace_fs` -> `core.config`.

Raw files are kept locally under `.startup-profile/before/`; this directory is
not a committed benchmark artifact.

## Instrumented surface baseline

| Surface boundary | Samples (ms) | p50 |
| --- | --- | ---: |
| Web `api_lifespan_ready` | 5109, 5069, 5097, 5212, 5264 | 5109.2 ms |
| Rich CLI `rich_cli_run_enter` | 8111, 8033, 8073, 8076, 8111 | 8076.2 ms |
| Textual TUI `tui_mounted` | 8311, 8144, 8161, 8153, 8272 | 8160.6 ms |

A single no-argument picker sample found 20 entries in 3 groups. Catalog-scanned
was 8525.9 ms for CLI and 8208.0 ms for TUI, about 240 ms after their parser
milestones.

A manually exercised desktop run reached these cross-process milestones:

| Desktop milestone | Time from parent launch |
| --- | ---: |
| Parent parser ready | 5062.9 ms |
| Child spawned | 5073.1 ms |
| Child app created | 9959.7 ms |
| Child API lifespan ready | 10059.9 ms |
| Desktop server ready | 10163.4 ms |
| Native window created | 10164.4 ms |
| Native window shown | 10750.7 ms |

Port 8001 was already occupied during this sample, so the desktop child used
its verified fallback port 8002. The duplicate `api_lifespan_ready` entry in
that raw trace is consistent with the failed first bind attempt starting the
app lifespan before the fallback server retried; it is a reminder to aggregate
by the final `desktop_server_ready` boundary rather than assuming a single
lifespan event.

Representative p50 phase decomposition:

### Web

| Milestone | p50 from process launch |
| --- | ---: |
| CLI parser ready | 5011.6 ms |
| Web app created | 5016.3 ms |
| Uvicorn run entered | 5017.2 ms |
| API lifespan ready | 5109.2 ms |

### Rich CLI

| Milestone | p50 from process launch |
| --- | ---: |
| Standalone parser ready | 8008.3 ms |
| Engine creation begins | 8021.1 ms |
| Creature added | 8067.8 ms |
| Creature started | 8075.8 ms |
| Rich CLI run entered | 8076.2 ms |

### Textual TUI

| Milestone | p50 from process launch |
| --- | ---: |
| Standalone parser ready | 8041.4 ms |
| Engine creation begins | 8054.3 ms |
| Creature added | 8101.9 ms |
| Creatures started | 8108.0 ms |
| Textual run entered | 8115.5 ms |
| Textual mounted | 8160.6 ms |

The dominant cost remains imports before the first parser milestone. Agent,
engine, and UI setup add tens of milliseconds for the minimal fixture after
imports complete.

## First import-graph optimization

The first optimization pass converted the public package facades to lazy
exports, isolated Studio group-hook registration, and deferred CLI command and
surface imports until dispatch. On the same machine and five-run methodology:

| Measurement | Before p50 | After p50 | Change |
| --- | ---: | ---: | ---: |
| `import kohakuterrarium` | 4953.3 ms | 50.6 ms | -99.0% |
| `import kohakuterrarium.cli` | 5780.5 ms | 200.1 ms | -96.5% |
| `python -m kohakuterrarium --version` | 5881.0 ms | 440.2 ms | -92.5% |
| Web `parser_ready` | 5011.6 ms | 183.7 ms | -96.3% |
| CLI `parser_ready` | 8008.3 ms | 3212.8 ms | -59.9% |
| TUI `parser_ready` | 8041.4 ms | 3204.9 ms | -60.1% |
| Web `api_lifespan_ready` | 5109.2 ms | 5059.9 ms | -1.0% |
| CLI `rich_cli_run_enter` | 8076.2 ms | 7292.6 ms | -9.7% |
| TUI `tui_mounted` | 8160.6 ms | 7175.5 ms | -12.1% |

The control result is important: parser latency fell sharply, while the final
surface boundaries improved much less. The remaining cost moved behind dispatch
into `serving.web` (about 5.0 seconds to import) and `cli.run` plus engine/UI
runtime imports (about 4.0 seconds before engine construction). The next pass
should therefore split those module-level dependency graphs rather than further
optimizing argparse or the public facade.

## Measurement limitations

- Desktop `desktop_window_shown` was exercised once on Windows, but remains a
  manual GUI measurement. Repeat it on every supported desktop platform and
  report a distribution before treating it as a budget.
- Browser FCP and Vue mount are not included. They need browser-side Performance
  API marks in a later frontend-specific pass.
- Cold filesystem-cache and packaged Briefcase measurements remain separate
  follow-ups.
- Enabling JSONL tracing added roughly 0.2 seconds to the very heavy
  `--version` route in a small five-run sample. Compare optimization results
  using the same trace configuration, and retain an uninstrumented control.
- The baseline harness supplied a fresh external wall-clock origin for every
  launched process. Runs that omit it measure from trace-module import instead.
