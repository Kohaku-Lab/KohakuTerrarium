# Agents (Creatures)

## The Idea

A creature is a complete, self-contained agent. It has everything it needs to operate independently: an LLM brain, tools to interact with the world, sub-agents for delegation, and memory for persistence.

The name "creature" comes from the terrarium metaphor. You build a creature, test it standalone, then place it in a terrarium where it collaborates with others. The creature does not change; it does not know it is in a terrarium.

## Anatomy of a Creature

```
                    +---------------------------+
                    |        Creature            |
                    |                           |
Input ------+       |   +-------------------+   |
            +------>|   |   Controller      |   |
Trigger ----+       |   |   (LLM brain)     |   |
                    |   +--------+----------+   |
                    |            |               |
                    |   +--------v----------+   |
                    |   |  Dispatches to:    |   |
                    |   |  - Tools (parallel)|   |
                    |   |  - Sub-agents      |   |       +--------+
                    |   +--------+----------+   +------>| Output |
                    |            |               |       +--------+
                    |   +--------v----------+   |
                    |   | Results feed back  |   |
                    |   | to controller for  |   |
                    |   | next decision      |   |
                    |   +-------------------+   |
                    +---------------------------+
```

**Input** brings events from the outside: user typing, API calls, speech.

**Triggers** generate events automatically: timers, channel messages, conditions.

**Controller** is the LLM. It receives events, thinks, and dispatches work. It orchestrates but does not do heavy work itself.

**Tools** execute actions: read files, run shell commands, search code, send messages. They start immediately during LLM streaming and run in parallel.

**Sub-agents** are nested creatures with their own LLM and limited tools. The controller delegates complex subtasks to them.

**Output** routes the controller's text to the right destination: terminal, TTS, Discord, named API endpoints.

## The Controller Pattern

The controller is the brain, but its job is to **dispatch, not execute**.

```
Good:  Controller decides -> calls bash tool -> gets result -> decides next step
Bad:   Controller writes a 2000-word essay in one response
```

Long outputs (user-facing content, prose, detailed analysis) should come from **output sub-agents**, not from the controller directly. This keeps the controller lightweight and its context window small.

## Everything Is an Event

All inputs flow through the same `TriggerEvent` type:

```
User types "hello"        -> TriggerEvent(type="user_input")
Timer fires               -> TriggerEvent(type="timer")
Tool finishes             -> TriggerEvent(type="tool_complete")
Channel message arrives   -> TriggerEvent(type="channel_message")
Sub-agent returns         -> TriggerEvent(type="subagent_output")
```

This unified model keeps the controller loop simple: receive event, call LLM, dispatch results, repeat. For the full processing loop and event source details, see [Execution Model](execution.md).

## Sub-Agents

A creature can delegate to sub-agents, which are smaller creatures with restricted capabilities:

```
Controller (full access)
  |
  +-- explore sub-agent (read-only tools: glob, grep, read)
  |
  +-- worker sub-agent (write tools: edit, multi_edit, write, bash)
  |
  +-- critic sub-agent (read-only, reviews worker's output)
```

Sub-agents have their own LLM conversation and tool registry. They return results to the parent controller. This is the **vertical hierarchy**, task decomposition within one creature.

Sub-agents are background jobs. The `SubAgentManager` registers and spawns sub-agents, sharing the `JobStore` with the executor. Results are delivered via the executor's `_on_complete` callback, the same path as background tools.

## Defining a Creature

A creature is defined by a YAML config and a system prompt:

```yaml
name: swe_agent
controller:
  model: gpt-5.4
  auth_mode: codex-oauth
  tool_format: native
system_prompt_file: prompts/system.md
input: { type: cli }
tools:
  - name: bash
  - name: read
  - name: write
subagents:
  - name: explore
  - name: plan
```

The system prompt defines personality and workflow. The tool list and call syntax are auto-generated; never write them in the prompt manually. For details on prompt assembly, see [Prompt System](prompts.md).

See [Configuration Reference](../guides/configuration.md) for all fields. See [Examples](../guides/examples.md) for walkthroughs.

---

## Framework Internals

### Agent (`core/agent.py`)

The top-level orchestrator that wires all components together.

**Responsibilities:**
- Load configuration from folder
- Delegate component initialization to `bootstrap/` factories
- Build system prompt via aggregation
- Process events through controller
- Track job status and completion
- Route output to appropriate modules

**Lifecycle:**
```python
agent = Agent.from_path("examples/agent-apps/my_agent")  # Load config
await agent.start()                          # Initialize modules
await agent.run()                            # Main event loop
await agent.stop()                           # Cleanup
```

### Bootstrap Package (`bootstrap/`)

Agent initialization is split into focused factory modules that each create one subsystem from an `AgentConfig`. This reduces the import fan-out of `bootstrap/agent_init.py` and keeps each factory independently testable.

| Module | Responsibility |
|--------|---------------|
| `bootstrap/llm.py` | Create LLM provider from config |
| `bootstrap/tools.py` | Load and register tools (builtin + custom) |
| `bootstrap/io.py` | Create input and output modules |
| `bootstrap/subagents.py` | Load sub-agent configs and create manager |
| `bootstrap/triggers.py` | Create trigger modules from config |

`bootstrap/agent_init.py` calls these factories in sequence during `Agent.start()`.

### Controller (`core/controller.py`)

The LLM conversation loop with event queue management.

**Responsibilities:**
- Maintain conversation history with context limits
- Stream LLM output and parse events
- Execute framework commands (read, info, jobs, wait) inline
- Push events via async queue
- Manage job tracking and status

**Key method, `run_once()`:**
1. Add event content to conversation
2. Stream LLM response via `_run_internal()`
3. Parse response for tool calls, commands, output blocks
4. Yield ParseEvents to caller

`run_once()` handles the outer conversation setup and result packaging, while `_run_internal()` owns the streaming loop and parse-event dispatch.

Commands like `[/info]bash[info/]` are handled inline during streaming; the result is converted to a TextEvent and yielded.

### Executor (`core/executor.py`)

Manages async tool execution in the background. For execution flow, tool modes, and background tool lifecycle, see [Execution Model](execution.md).

### JobStore (`core/job.py`)

In-memory storage for job status and results.

```python
@dataclass
class JobStatus:
    job_id: str
    job_type: JobType          # TOOL, SUBAGENT, BASH
    type_name: str             # "bash", "explore", etc.
    state: JobState            # PENDING, RUNNING, DONE, ERROR, CANCELLED
    start_time: datetime
    duration: float | None
    output_lines: int
    output_bytes: int
    preview: str               # First 200 chars
    error: str | None

@dataclass
class JobResult:
    job_id: str
    output: str
    exit_code: int | None
    error: str | None
    metadata: dict
```

### Conversation (`core/conversation.py`)

Manages message history with OpenAI-compatible format.

**Features:**
- Supports multimodal messages (text + images)
- Automatic truncation policies: `max_messages`, `max_context_chars`, `keep_system`
- JSON serialization/deserialization
- Metadata tracking (creation time, message count, total chars)

### Auto-Compaction (`core/compact.py`)

When an agent's conversation approaches the model's token limit, the `CompactManager` triggers a background summarization task. An LLM call summarizes older messages into a structured summary, then atomically replaces them in the conversation. The controller keeps running during compaction; it is non-blocking.

The two-zone model splits the conversation into a "compact zone" (old messages, eligible for summarization) and a "live zone" (recent turns, kept verbatim). After compaction, the compact zone is replaced by a single summary message.

Configure auto-compaction in the agent's `compact` section. See [Configuration Reference](../guides/configuration.md) for all options (`max_tokens`, `threshold`, `target`, `keep_recent_turns`, `compact_model`).

### Session Registry (`core/session.py`)

Keyed shared state for session-scoped objects. A `Session` holds channels, scratchpad, TUI state, and user-provided extras for one agent (or a group of cooperating agents).

```python
@dataclass
class Session:
    key: str
    channels: ChannelRegistry
    scratchpad: Scratchpad
    tui: Any | None = None
    extra: dict[str, Any]
```

Agents with the same `session_key` share the same Session instance. See [Environment-Session](environment.md) for the full isolation model.

### Registry (`core/registry.py`)

Per-agent registration for tools, sub-agents, and commands. Supports both programmatic registration and decorator-based registration (`@tool("name")`, `@command("name")`).

### Tool Catalog (`builtins/tool_catalog.py`)

Global registry of builtin tool classes. A leaf module with zero side effects: individual tool modules use `@register_builtin` to register themselves at import time, but the catalog never imports any tool module itself. Supports deferred loaders for lazy registration of tool groups (e.g., terrarium tools are only loaded on first demand). Internal code (core, terrarium) should import from `tool_catalog`, not from `builtins.tools`, to avoid pulling in all tool modules and their transitive dependencies.

### Sub-Agent Catalog (`builtins/subagent_catalog.py`)

Global registry of builtin sub-agent configurations. Same leaf-module pattern as `tool_catalog`.

## Module System

All modules follow a protocol-based design with base class implementations.

### Input Modules (`modules/input/base.py`)

```python
class InputModule(Protocol):
    async def start(self) -> None
    async def stop(self) -> None
    async def get_input(self) -> TriggerEvent | None
```

Built-in types: `cli`, `tui`, `whisper`, `none`. Custom modules implement the same protocol.

### Trigger Modules (`modules/trigger/base.py`)

```python
class TriggerModule(Protocol):
    async def start(self) -> None
    async def stop(self) -> None
    async def wait_for_trigger(self) -> TriggerEvent | None
    def set_context(self, context: dict[str, Any]) -> None
```

### Tool Modules (`modules/tool/base.py`)

```python
class Tool(Protocol):
    @property
    def tool_name(self) -> str
    @property
    def description(self) -> str
    @property
    def execution_mode(self) -> ExecutionMode  # DIRECT, BACKGROUND, STATEFUL
    async def execute(self, args: dict[str, Any]) -> ToolResult
```

Tools with `needs_context = True` receive a `ToolContext` with agent name, session, working directory, memory path, environment, and tool_format.

### Output Modules (`modules/output/base.py`)

```python
class OutputModule(Protocol):
    async def start(self) -> None
    async def stop(self) -> None
    async def write(self, content: str) -> None
    async def write_stream(self, chunk: str) -> None
    async def flush(self) -> None
    async def on_processing_start(self) -> None
```

### Output Router (`modules/output/router.py`)

Routes parse events using a state machine:

```
State: NORMAL
  TextEvent           -> write_stream() to default output
  BlockStartEvent     -> transition to TOOL_BLOCK / SUBAGENT_BLOCK / OUTPUT_BLOCK
  OutputEvent         -> route to named output module

State: TOOL_BLOCK
  TextEvent           -> SUPPRESSED
  BlockEndEvent       -> transition to NORMAL

State: OUTPUT_BLOCK
  TextEvent           -> SUPPRESSED (content comes via OutputEvent)
  BlockEndEvent       -> transition to NORMAL
```

Activity notifications (`on_activity()`) are separate from text output; they are used for tool_start, tool_done, tool_error, etc. The router also supports `notify_activity(metadata=)` for structured metadata delivery and `add_secondary()` / `remove_secondary()` for adding output modules that observe without replacing the primary output (used by SessionOutput and WebSocket StreamOutput).

### Token Usage Tracking

The controller tracks per-LLM-call token usage via the `_last_usage` attribute on the LLM provider. Both the OpenAI provider and Codex OAuth provider capture usage from streaming (final SSE chunk) and non-streaming responses. Usage is emitted as a `token_usage` activity event after each LLM call.

### Stream Parser (`parsing/state_machine.py`)

Stateful parser for streaming LLM output using a character-by-character state machine. Parse events include `TextEvent`, `ToolCallEvent`, `SubAgentCallEvent`, `CommandEvent`, `OutputEvent`, and `BlockStartEvent` / `BlockEndEvent`. The parser uses `ToolCallFormat` to support multiple tool call syntaxes. See [Tool Formats](tool-formats.md) for format details, and [Execution Model](execution.md) for how parse events drive the processing loop.

## File Organization

```
src/kohakuterrarium/
+-- core/                    # Core abstractions and runtime
|   +-- agent.py             # Agent orchestrator
|   +-- agent_handlers.py    # Event processing handlers
|   +-- agent_tools.py       # Tool/subagent dispatch mixin (extracted from agent_handlers)
|   +-- config_types.py      # Config dataclasses
|   +-- controller.py        # LLM conversation loop + token usage tracking
|   +-- conversation.py      # Message history management
|   +-- executor.py          # Background tool execution
|   +-- job.py               # Job status tracking
|   +-- events.py            # TriggerEvent model
|   +-- session.py           # Session registry (keyed shared state)
|   +-- trigger_manager.py   # Trigger lifecycle + on_trigger_fired callback
|   +-- config.py            # Configuration loading
|   +-- registry.py          # Module registration
|   +-- loader.py            # Dynamic module loading
|
+-- modules/                 # Plugin APIs
|   +-- input/base.py        # InputModule protocol
|   +-- output/              # OutputModule + Router (supports secondary outputs)
|   +-- tool/base.py         # Tool protocol + BaseTool
|   +-- trigger/base.py      # TriggerModule protocol
|   +-- subagent/            # SubAgent system (session-aware, tool activity tracking)
|
+-- session/                 # Session persistence
|   +-- store.py             # SessionStore (9 KohakuVault tables in .kohakutr file)
|   +-- output.py            # SessionOutput (OutputModule that records to store)
|   +-- resume.py            # Resume agent/terrarium from .kohakutr file
|
+-- parsing/                 # Stream parsing
|   +-- state_machine.py     # StreamParser
|   +-- events.py            # ParseEvent types
|   +-- patterns.py          # Parser patterns
|   +-- format.py            # ToolCallFormat definitions
|
+-- prompt/                  # Prompt system
|   +-- aggregator.py        # System prompt building
|   +-- loader.py            # Prompt file loading
|   +-- template.py          # Jinja2 rendering
|   +-- plugins.py           # Extensible plugins
|
+-- bootstrap/               # Agent initialization factories
|   +-- agent_init.py       # AgentInitMixin - orchestrates component init
|   +-- llm.py              # LLM provider creation
|   +-- tools.py            # Tool loading and registration
|   +-- io.py               # Input/output module creation
|   +-- subagents.py        # Sub-agent config loading
|   +-- triggers.py         # Trigger module creation
|
+-- builtins/                # Built-in implementations
|   +-- tool_catalog.py     # Global builtin tool registry (leaf module)
|   +-- subagent_catalog.py # Global builtin sub-agent registry (leaf module)
|   +-- tools/               # 18 general + 8 terrarium tools
|   +-- inputs/              # cli, whisper, none
|   +-- outputs/             # stdout, tts
|   +-- tui/                 # TUI session, input, output
|   +-- subagents/           # 10 sub-agents: explore, plan, worker, critic, etc.
|
+-- llm/                     # LLM integration
|   +-- base.py              # LLMProvider protocol (with last_usage property)
|   +-- openai.py            # OpenAI-compatible provider (token usage capture)
|   +-- codex_provider.py    # Codex OAuth provider (ChatGPT subscription)
|   +-- message.py           # Message formatting
|
+-- utils/                   # Utilities
    +-- logging.py           # Structured logging
    +-- async_utils.py       # Async helpers
```
