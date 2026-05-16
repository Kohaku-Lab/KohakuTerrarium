---
title: Testing
summary: Test layout, the ScriptedLLM and TestAgentBuilder helpers, and how to write deterministic agent tests.
tags:
  - dev
  - testing
---

# Testing

The test suite lives under `tests/` and splits into **three tiers**
(`tests/unit/`, `tests/integration/`, `tests/e2e/`). Each tier is a
*different shape of test*, not just a different size — see
[Three-tier discipline](#three-tier-discipline) below. A reusable
harness under `src/kohakuterrarium/testing/` (and `tests/e2e/_lab_harness.py`)
covers fake LLMs, real lab workers, and journey scaffolding.

## Three-tier discipline

The tiering is enforced by the [CLAUDE.md](../../../CLAUDE.md) test
convention. Read `tests/README.md` for the full spec; the summary:

### `tests/unit/` — one source file → one test (or test-class)

Tests an individual class / method against its real dependencies
(deterministic stubs only for genuine I/O). **Shape checks**
(`isinstance`, `key in dict`, `is not None`) are legitimate **here
and only here**. Target: 95–100% line coverage per core-lib file;
any sub-95% file needs a written justification in the test or a
tracking issue.

### `tests/integration/` — one core-lib folder → one test-class

Each test method runs a **complete feature workflow end-to-end in a
single function** (init → drive → read back → resume → verify),
mirroring how the real consumer drives that folder. Splitting a
workflow into separate "init" / "read" / "resume" tests is
unit-tier thinking and cannot catch cross-step bugs. The
integration test for a folder *is* that folder's most comprehensive
usage example.

### `tests/e2e/` — whole project → a handful of fat journey tests

Each is a single function simulating an entire user session
(chat → switch model → toggle plugin → interrupt → resume → branch
…). ~10 journeys cover `{programmatic, HTTP+WS} × {creature,
terrarium, studio}` plus multi-node. e2e answers one question:
*is the system runnable, end to end?*

### Tier rules

- **Behavior asserts, not shape asserts.** Every mutation test
  observes the side effect, not just the return shape.
- **Real collaborators, not mocks.** The only seam is the LLM —
  use `kohakuterrarium.testing.llm.ScriptedLLM`, monkeypatched at
  **both** `bootstrap.llm.create_llm_provider` and
  `bootstrap.agent_init.create_llm_provider`. Everything else is
  real (real session store, real engine, real lab clients).
- **To raise integration / e2e coverage, fatten the existing
  workflow functions — do NOT add more test functions.** This is
  the most common review comment for new contributors. A new
  scenario goes inside the existing journey via the
  [`_BugLog`](../../../tests/e2e/test_multinode_journey.py)
  fail-accumulator pattern, not a new top-level test.
- **All three tiers run in CI** on the full OS × Python matrix.

### Carve-outs

Some files are intentionally excluded from the 95% per-file
coverage target — third-party providers (`llm/codex_provider.py`),
platform PTY (`api/ws/pty.py`), the end-user CLI/UI
(`builtins/cli_rich/*`, `builtins/tui/*`), the pywebview boot path.
The full list is in `tests/README.md`.

## Audit loop (required for multi-step impl work)

For any task larger than a one-file change, do **not** stop at
"tests pass." Run this loop until it converges:

1. **Implement** the slice.
2. **Write new tests** that pin the behaviour you added. Negative
   cases (the bug you'd accidentally introduce) count more than
   positive cases.
3. **Execute the full test suite** for the affected tiers
   (unit/integration/e2e + frontend vitest). Lint too (`black`,
   `ruff`, `prettier`).
4. **Audit** the diff with a critical eye — three categories:
   - **Clear bugs:** typos, wrong field names, off-by-ones,
     `await` missing on async calls, dead branches.
   - **Integrity bugs:** invariants you broke — state that's
     supposed to be in sync now drifts, two writers race a single
     dict, a cache outlives the thing it caches.
   - **Behavior bugs:** the code does what's typed but the wrong
     thing for the spec — wrong default, silently-swallowed error,
     condition gates the wrong branch.
5. **If you find any bug the tests didn't catch:** first augment
   the test so it *would* have caught it, confirm the augmented
   test fails on the unfixed code, then fix the bug. Tests that
   miss real bugs are evidence the test suite is the bug; patching
   tests first prevents the same blind spot next time.
6. **Loop** to step 3. Stop only when the audit finds nothing AND
   every test is green.

This loop is the difference between "I wrote code and tests
passed" and "I delivered working code." Treat the loop as part of
the definition-of-done, not optional polish.

## Running tests

```bash
pytest                                    # full suite
pytest tests/unit                         # unit only
pytest tests/integration                  # integration only
pytest -k channel                         # anything with "channel" in name
pytest tests/unit/test_phase3_4.py::test_executor_parallel
pytest -x                                 # stop at first failure
pytest --no-header -q                     # quieter output
```

Tests should run in full asyncio. Use `pytest-asyncio` (`@pytest.mark.asyncio`)
for async test functions. Avoid `asyncio.run()` inside a test — let
the plugin own the loop.

## The testing harness

`src/kohakuterrarium/testing/` exports four primitives. Import from
the package root:

```python
from kohakuterrarium.testing import (
    ScriptedLLM, ScriptEntry,
    OutputRecorder,
    EventRecorder, RecordedEvent,
    TestAgentBuilder,
)
```

### ScriptedLLM — deterministic LLM mock

`testing/llm.py`. Implements the `LLMProvider` protocol without a real
API. Feed it a list of responses and it hands them out in order.

```python
# Simplest: just strings
llm = ScriptedLLM(["Hello.", "I'll use a tool.", "Done."])

# Advanced: ScriptEntry with match-based selection and streaming control.
# Tool-call syntax must match the parser's tool_format — the default is
# bracket: [/name]@@arg=value\nbody[name/]
llm = ScriptedLLM([
    ScriptEntry("I'll search.", match="find"),   # only fires if last user msg contains "find"
    ScriptEntry("Sorry, can't.", match="help"),
    ScriptEntry("[/bash]@@command=echo hi\n[bash/]", chunk_size=5),
])
```

`ScriptEntry` (`testing/llm.py:12`) fields:

- `response: str` — full text, may include framework-format tool calls.
- `match: str | None` — if set, only this entry if the last user
  message contains the substring; otherwise skipped.
- `delay_per_chunk: float` — seconds between chunk yields.
- `chunk_size: int` — characters per yield (default 10).

After a run, inspect:

- `llm.call_count`
- `llm.call_log` — list of message lists seen per call
- `llm.last_user_message` — convenience extractor

If you need a single non-streaming response, call
`await llm.chat_complete(messages)` (returns a `ChatResponse`).

### TestAgentBuilder — lightweight agent wiring

`testing/agent.py`. Builds a `Controller` + `Executor` + `OutputRouter`
trio without loading a YAML config or running the full `Agent.start()`
bootstrap. Useful for unit-testing the controller loop and tool
dispatch in isolation.

```python
from kohakuterrarium.testing import TestAgentBuilder

env = (
    TestAgentBuilder()
    .with_llm_script(["[/bash]@@command=echo hi\n[bash/]", "Done."])
    .with_builtin_tools(["bash", "read"])
    .with_system_prompt("You are a test agent.")
    .with_session("test_session")
    .build()
)

await env.inject("please echo")

assert env.llm.call_count >= 1
env.output.assert_text_contains("Done")
```

`env` is a `TestAgentEnv` exposing `llm`, `output`, `controller`,
`executor`, `registry`, `router`, `session`. `env.inject(text)` runs
one turn: push a user-input event, stream from the scripted LLM,
parse tool/command events, dispatch tools through the executor, route
everything else to the `OutputRouter`. For raw events use
`env.inject_event(TriggerEvent(...))`.

Builder methods (see `testing/agent.py:19`):

- `with_llm_script(list)` / `with_llm(ScriptedLLM)`
- `with_output(OutputRecorder)`
- `with_system_prompt(str)`
- `with_session(key)`
- `with_builtin_tools(list[str])` — resolves via `get_builtin_tool`
- `with_tool(instance)` — register a custom tool
- `with_named_output(name, output)`
- `with_ephemeral(bool)`

### OutputRecorder — capture for assertions

`testing/output.py`. A `BaseOutputModule` subclass that records every
write, stream chunk, and activity notification.

```python
recorder = OutputRecorder()
await recorder.write("final text")
await recorder.write_stream("chunk1")
await recorder.write_stream("chunk2")
recorder.on_activity("tool_start", "[bash] job_123")

assert recorder.all_text == "chunk1chunk2final text"
assert recorder.stream_text == "chunk1chunk2"
assert recorder.writes == ["final text"]
recorder.assert_text_contains("chunk1")
recorder.assert_activity_count("tool_start", 1)
```

State captured separately: `writes`, `streams`, `activities`,
`processing_starts`, `processing_ends`. `reset()` clears writes and
streams between turns (the `OutputRouter` calls this); `clear_all()`
also clears activities and lifecycle counts.

Assertion helpers: `assert_no_text`, `assert_text_contains`,
`assert_activity_count`.

### EventRecorder — timing and ordering

`testing/events.py`. Tracks events with monotonic timestamps and a
source label.

```python
er = EventRecorder()
er.record("tool_complete", "bash ok", source="tool")
er.record("channel_message", "hello", source="channel")

assert er.count == 2
er.assert_order("tool_complete", "channel_message")
er.assert_before("tool_complete", "channel_message")
```

Useful when the thing you care about is *when* something fires, not
the text content.

## Conventions

- **Use `ScriptedLLM`, not provider-level mocks.** Don't monkey-patch
  `httpx` or the OpenAI SDK. The scripted LLM sits at the
  `LLMProvider` protocol boundary, which is where the controller
  interacts with it.
- **No session store in tests unless you're testing persistence.** The
  harness skips `SessionStore` by default. For CLI integration tests
  that invoke `kt run`, pass `--no-session` (or its equivalent).
- **Clean up.** Pytest fixtures should construct one agent per test
  and tear it down. `TestAgentBuilder.build()` calls `set_session`,
  which writes to a module-level registry — if tests leak session
  keys, use distinct `with_session(...)` keys or clear in a
  `yield`-style fixture.
- **No real network.** If something wants to hit an HTTP endpoint, mock
  it at the transport layer or skip the test.
- **Async marks.** Decorate async tests with `@pytest.mark.asyncio`
  and set `asyncio_mode = "auto"` in `pyproject.toml` if you want
  implicit marking.

## Where to add tests

Mirror `src/` layout under `tests/unit/`:

| You changed             | Add tests under                    |
|-------------------------|------------------------------------|
| `core/agent.py`         | `tests/unit/test_phase5.py` or a new file |
| `core/controller.py`    | `tests/unit/test_phase3_4.py`      |
| `core/executor.py`      | `tests/unit/test_phase3_4.py`      |
| `parsing/`              | `tests/unit/test_phase2.py`        |
| `modules/subagent/`     | `tests/unit/test_phase6.py`        |
| `modules/trigger/`      | `tests/unit/test_phase7.py`        |
| `core/environment.py`   | `tests/unit/test_environment.py`   |
| `session/store.py`      | `tests/unit/test_session_store.py` |
| `session/resume.py`     | `tests/unit/test_session_resume.py`|
| `bootstrap/`            | `tests/unit/test_bootstrap.py`     |
| `terrarium/`            | `tests/unit/test_terrarium_modules.py` |

Cross-component flows go under `tests/integration/`:

- channels — `test_channels.py`
- output routing — `test_output_isolation.py`
- full pipeline (controller → executor → output) — `test_pipeline.py`

If the subsystem has no existing test file, add one and match the
naming convention.

Full user journeys go under `tests/e2e/` — one fat function per
journey. Examples:

- `test_multinode_journey.py` — `{programmatic, HTTP+WS} × multi-node`,
  drives two real lab workers (in-process via `RealLabWorker`)
  through the entire dashboard surface: spawn, chat, cross-node
  connect, hot-plug, close, list saved, resume, cluster resume.
- `test_prog_studio.py`, `test_prog_terrarium.py` — programmatic
  journeys exercising the Studio + Terrarium APIs directly.
- `test_api_creature.py` — the dashboard's HTTP+WS surface for a
  single creature.

## Testing multi-node code

Multi-node code (Lab adapters, `MultiNodeTerrariumService`,
session sync, cluster fold) needs at least one worker to be
meaningful. Three patterns:

### Unit: `_FakeNode` / `_RecordingNode`

When testing a worker-side adapter or `IdentityCache`, use a tiny
fake that implements `LabSender` / `LabRegistrar`. Example:
`tests/unit/laboratory/test_worker_session.py` builds a
`_FakeEngine` + `_RecordingNode` and drives the attacher directly.
No Lab transport is started — these are sub-millisecond.

### Integration: `InProcTransport`

For workflows that span the actual Lab dispatch logic
(handshake → APP request → response), use `InProcTransport` from
`laboratory/_internal/transport_inproc.py`. It implements the
same `LabTransport` Protocol as the WebSocket transport but
keeps everything in one event loop. See
`tests/unit/laboratory/test_client_host.py::_start_host` for
the canonical setup helper.

### E2E: `RealLabWorker`

The journey tier uses `tests/e2e/_lab_harness.RealLabWorker` —
spins up a real `ClientConnector` with the full ten-adapter
stack (runtime, events, attach, pty, broadcast, output-wire,
files, deploy, session, identity-cache, catalog, identity)
against a real `HostEngine` on a real WebSocket transport.
Despite "real lab," it shares the test's event loop, so
breakpoints work.

For full prod-like isolation (separate process), `_lab_harness.py`
also has a subprocess-launched variant — used in the multinode
journey to verify Win32 process boundaries and signal handling.

Conventions:

- Spawn workers with `--home-dir` pointed at a `tmp_path`
  subdirectory so each test has its own credential store.
- Use the `_BugLog` fail-accumulator pattern (see
  `test_multinode_journey.py`) for journeys that should report
  multiple failures in one run instead of bailing on the first
  red assertion.
- Multi-node tests live alongside single-node ones — there's no
  separate `tests/multinode/` directory. Tag with descriptive
  test function names (`test_full_creature_session_on_subprocess_worker`).

## Fast vs integration

- **Fast unit tests** should use `TestAgentBuilder` (no file I/O, no
  real LLM) and complete in well under a second. Most of the suite
  should be this.
- **Integration tests** exercise two or more subsystems together — for
  example, the controller's feedback loop with a real executor and
  real tools. They can touch the filesystem and use real session
  stores, but should still finish in single-digit seconds.
- **Manual / slow tests** (real LLM calls, long-running agents) do not
  belong in the default suite. Mark them with
  `@pytest.mark.slow` or put them in `tests/manual/`.

## Linting and formatting

Before committing:

```bash
python -m black src/ tests/
python -m ruff check src/ tests/
python -m isort src/ tests/
```

Ruff config lives in `pyproject.toml`. The `[dev]` extra installs all
three. Import ordering follows [CLAUDE.md](../../CLAUDE.md) — built-in,
third-party, then `kohakuterrarium.*`, alphabetical within groups,
`import` before `from`, shorter dotted paths before longer.

## Post-impl checklist

Cross-check [CLAUDE.md](../../CLAUDE.md) §Post-impl tasks:

1. No in-function imports (except optional deps or deliberate lazy
   loading for init-order issues).
2. Black + ruff + isort clean.
3. New behavior has a test.
4. Logically separated commits. Don't push drafts unless asked.
