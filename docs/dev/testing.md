# Testing

The test suite lives under `tests/` and splits into unit tests
(`tests/unit/`) and integration tests (`tests/integration/`). There is
a reusable harness under `src/kohakuterrarium/testing/` for constructing
agents with fake LLMs.

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
