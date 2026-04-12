# Testing Infrastructure and Behavior Documentation

## Overview

KohakuTerrarium has a two-tier test suite:
- **Unit tests** (`tests/unit/`): Component-level tests, 9 files organized by architectural phase
- **Integration tests** (`tests/integration/`): Cross-component tests for channels, output routing, and full pipeline

### Current Test Results

```
515 passed

Unit tests:       331 passed
Integration tests: 184 passed
```

---

## Test Infrastructure (`src/kohakuterrarium/testing/`)

A reusable module providing fake/mock primitives for testing the agent framework without real LLMs.

### ScriptedLLM - Deterministic LLM Mock

Implements `LLMProvider` protocol with scripted responses.

```python
from kohakuterrarium.testing import ScriptedLLM, ScriptEntry

# Simple: list of strings
llm = ScriptedLLM(["Hello!", "I'll use a tool.", "Done."])

# Advanced: match-based entries with streaming control
llm = ScriptedLLM([
    ScriptEntry("I'll search for it.", match="find"),     # only if input contains "find"
    ScriptEntry("I don't understand."),                    # fallback
    ScriptEntry("[/bash]echo hi[bash/]", chunk_size=5),   # tool call, 5 chars/chunk
])

# After running:
assert llm.call_count == 2
assert llm.last_user_message == "find the bug"
assert len(llm.call_log) == 2
```

**Key features:**
- Accepts `list[str]` or `list[ScriptEntry]`
- Match-based entry selection (checks last user message)
- Configurable chunk size and streaming delay
- Records all received messages for assertions
- `chat_complete()` for non-streaming use

### OutputRecorder - Output Capture

Extends `BaseOutputModule` to capture all output.

```python
from kohakuterrarium.testing import OutputRecorder

recorder = OutputRecorder()
await recorder.write("complete text")
await recorder.write_stream("chunk1")
await recorder.write_stream("chunk2")
recorder.on_activity("tool_start", "[bash] job_123")

assert recorder.all_text == "chunk1chunk2complete text"
assert recorder.stream_text == "chunk1chunk2"
assert recorder.writes == ["complete text"]
recorder.assert_text_contains("chunk1")
recorder.assert_activity_count("tool_start", 1)
```

**Captures separately:** `writes` (complete calls), `streams` (chunks), `activities` (notifications), `processing_starts` / `processing_ends` (lifecycle counts).

### EventRecorder - Event Flow Tracking

```python
from kohakuterrarium.testing import EventRecorder

recorder = EventRecorder()
recorder.record("tool_complete", "bash result", source="tool")
recorder.record("channel_message", "hello", source="channel")

assert recorder.count == 2
recorder.assert_order("tool_complete", "channel_message")
```

### TestAgentBuilder - Agent Assembly

Builder pattern for wiring `ScriptedLLM` + `OutputRecorder` + real `Controller` + real `Executor` into a test harness.

```python
from kohakuterrarium.testing import TestAgentBuilder

env = (
    TestAgentBuilder()
    .with_llm_script(["Hello!", "[/bash]echo hi[bash/]", "Done."])
    .with_builtin_tools(["bash", "read"])
    .with_system_prompt("You are a test agent.")
    .with_session("test_session")
    .with_named_output("discord", discord_recorder)
    .build()
)

await env.inject("Please help me")
# Access: env.llm, env.output, env.controller, env.executor, env.registry, env.router, env.session
```

`env.inject(text)` simulates one full controller turn: push event -> run LLM -> parse -> route to output. Tool calls are submitted to the executor. Command results are routed to activity.

---

## Unit Test Coverage by Phase

### Phase 1 (`test_phase1.py`) - Core Foundation
- Logging, TriggerEvent, Message types, Conversation

### Phase 2 (`test_phase2.py`) - Stream Parsing
- ParseEvent types, tag parsing, extraction helpers, StreamParser

### Phase 3-4 (`test_phase3_4.py`) - Controller and Tool Execution
- JobStatus/JobResult, JobStore, Registry, BashTool, Executor

### Phase 5 (`test_phase5.py`) - Agent Assembly
- Config loading, prompt templating, input/output modules, Agent initialization

### Phase 6 (`test_phase6.py`) - Sub-Agent System
- SubAgentConfig, SubAgentManager, builtin sub-agents

### Phase 7 (`test_phase7.py`) - Custom Modules and Triggers
- ModuleLoader, TimerTrigger, ContextUpdateTrigger, interactive sub-agents

### Environment (`test_environment.py`) - Environment-Session Isolation
- Environment creation, session lifecycle, session isolation, shared state, channel isolation, multi-environment, async channel isolation

### Phase 8 (`test_phase8.py`) - Advanced Coverage
- SkillDoc/frontmatter, SubAgent with MockLLM, SubAgentJob, Controller, InteractiveSubAgent, Commands, Aggregator

### Session Store (`test_session_store.py`) - SessionStore CRUD
- Table creation, event append/retrieve, conversation save/load, scratchpad persistence, channel messages, FTS search, counter restoration, lifecycle

### Session Resume (`test_session_resume.py`) - Resume Roundtrips
- Agent session roundtrip, terrarium multi-agent roundtrip, conversation injection, tool_calls preservation via msgpack, multiple resume cycles, empty conversation/scratchpad edge cases, session type detection

### Bootstrap (`test_bootstrap.py`) - Agent Initialization Factories
- LLM provider creation, tool loading/registration, input/output module creation, sub-agent config loading, trigger creation

### Terrarium Modules (`test_terrarium_modules.py`) - Terrarium Subsystem Tests
- Factory functions (build_creature, build_root_agent), persistence helpers, tool_registration deferred loading, events.py StreamOutput and event log

---

## Integration Test Coverage

### Channel Communication (`test_channels.py`) - 23 Tests

**SubAgentChannel (queue):**
- Basic send/receive, single consumer semantics, FIFO ordering, timeout, non-blocking try_receive, unique message IDs, reply-to threading, metadata preservation

**AgentChannel (broadcast):**
- All subscribers receive, sender receives own message, late subscriber misses old messages, unsubscribe stops delivery, subscriber count tracking, resubscribe

**ChannelRegistry:**
- Default creation, explicit broadcast, existing channel type ignored, list and remove

**ChannelTrigger:**
- Queue trigger, broadcast trigger, sender filtering, prompt template, subscription cleanup

### Output Isolation (`test_output_isolation.py`) - 10 Tests

**Router state machine:**
- Normal text routing, tool block suppression, subagent block suppression, text before/after tool blocks, named output routing, unknown target fallback, completed output tracking, activity notifications

**Router lifecycle:**
- Processing start/end propagation, reset vs clear_all behavior

### Full Pipeline (`test_pipeline.py`) - 8 Tests

- Simple text response, multiple turns, system prompt verification, ephemeral mode, sequential responses, match-based responses, named output routing, mixed text + named output

---

## Behavior Documentation

### Background Tool Execution

**Direct (blocking):** Agent waits for completion before next LLM turn.
```
LLM response with tool call
  -> Tool started immediately during streaming (asyncio.create_task)
  -> LLM continues generating
  -> After LLM finishes, agent waits for direct tools (asyncio.gather)
  -> Results batched into feedback event -> next LLM turn
```

**Background (non-blocking):** Tool runs independently, status reported in subsequent turns.
```
LLM response with tool call
  -> Tool started as background task, NOT waited for
  -> Agent checks background status each loop iteration
  -> Reports "RUNNING" or "DONE" to LLM
  -> LLM can use [/wait]job_id[wait/] to explicitly block
```

**Key behaviors:**
1. Tools start immediately when parsed during streaming, not queued until response ends
2. Multiple tools run in parallel via `asyncio.gather()`
3. Direct tool results are batched into a single feedback event
4. Background tools are checked each iteration; completed results reported
5. The agent loop continues while any background jobs are pending

### Sub-Agent Execution

Sub-agents are always background jobs. The parent agent loop reports "RUNNING" status, and the LLM can use `[/wait]` to get results sooner.

**Sub-agent isolation:**
- Own Registry with only the allowed tools
- `can_modify=False` filters out file-modifying tools such as write/edit/multi_edit/bash
- Output goes to `SubAgentResult.output` only, never to parent's OutputRouter
- Max nesting depth configurable (default 3)

### Channel Communication

See [Channels](../concepts/channels.md) for the full reference.

### Output Router State Machine

See [Framework Internals](../concepts/agents.md) for the state machine diagram.

---

## Coverage Gaps

### Not Yet Tested

| Area | What's Missing | Priority |
|------|---------------|----------|
| **Multi-agent channel flow** | Two agents exchanging messages through channels end-to-end | High |
| **Background + direct tool parallel** | Background wait_channel running while direct tools complete | High |
| **FastAPI HTTP API** | `kohakuterrarium/api/` REST endpoints, unified WebSocket, config discovery | High |
| **Event ordering under concurrency** | Multiple events arriving simultaneously, batching behavior | Medium |
| **Agent._process_event_with_controller** | Full 6-phase loop with tool execution and feedback | Medium |
| **Termination conditions** | max_turns, keywords, idle_timeout, max_duration | Medium |
| **Conversation compaction** | Context truncation under limits | Low |
| **Module loader** | Loading custom tools from external Python files | Low |
| **TUI output correctness** | Verify TUI only receives correct agent's output | Low |

### Notes

The FastAPI HTTP API (`kohakuterrarium/api/`) is an application layer separate from the core library. It needs its own test suite using `httpx.AsyncClient` and `TestClient`. The serving layer is partially tested via `tests/integration/test_service_api.py`, but the HTTP transport is not yet covered.

### Recommended Next Tests

1. **Multi-agent pipeline test**: ScriptedLLM Agent A sends to channel -> Agent B receives via trigger -> processes -> sends result back
2. **Background tool non-blocking test**: Start `wait_channel` (background) and a direct tool simultaneously
3. **Feedback loop test**: ScriptedLLM makes tool call -> tool returns result -> verify result appears in next LLM call
4. **HTTP API endpoint tests**: Use FastAPI `TestClient` for REST CRUD and WebSocket framing
