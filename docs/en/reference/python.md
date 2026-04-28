---
title: Python API
summary: The kohakuterrarium package surface — Studio, Terrarium engine, Creature, Agent, compose, and testing helpers.
tags:
  - reference
  - python
  - api
---

# Python API

Every public class, function, and protocol in the `kohakuterrarium`
Python package. Entries are grouped by module package. Signatures use
modern type hints.

For the architecture, read [concepts/README](../concepts/README.md).
For task walkthroughs, see
[guides/programmatic-usage](../guides/programmatic-usage.md) and
[guides/custom-modules](../guides/custom-modules.md).

## Import surfaces

| When you want | Use |
|---|---|
| The management facade (catalog, identity, sessions, persistence, attach, editors) | `kohakuterrarium.Studio` |
| The runtime engine (solo or multi-creature) | `kohakuterrarium.Terrarium` |
| A running creature handle | `kohakuterrarium.Creature` |
| Engine events | `kohakuterrarium.{EngineEvent, EventKind, EventFilter}` |
| Topology results | `kohakuterrarium.{ConnectionResult, DisconnectionResult}` |
| Direct agent control | `kohakuterrarium.core.agent.Agent` |
| Config loading | `kohakuterrarium.core.config.load_agent_config` / `kohakuterrarium.terrarium.config.load_terrarium_config` |
| Persistence / search | `kohakuterrarium.session.store.SessionStore`, `kohakuterrarium.session.memory.SessionMemory` |
| Extension author | `kohakuterrarium.modules.{tool,input,output,trigger,subagent}.base` |
| Pipeline composition | `kohakuterrarium.compose` |
| Tests | `kohakuterrarium.testing` |

---

## Top-level package re-exports

The new public surface is re-exported directly from
`kohakuterrarium`:

```python
from kohakuterrarium import (
    Studio,
    Terrarium,
    Creature,
    EngineEvent,
    EventKind,
    EventFilter,
    ConnectionResult,
    DisconnectionResult,
)
```

### `Studio`

Module: `kohakuterrarium.studio.studio`. Programmatic facade for the
studio tier. Wraps a `Terrarium` engine and exposes catalog, identity,
active-session, persistence, editor, and attach namespaces.

Constructor and lifecycle:

- `Studio(engine: Terrarium | None = None)` — create a facade over an
  existing engine, or create an empty engine if omitted.
- `async with Studio() as studio: ...` — enters/exits the underlying
  engine context.
- `await studio.shutdown()` — stop every running creature through the
  engine.
- `studio.engine: Terrarium` — drop down to raw runtime operations.

Classmethod constructors:

- `async Studio.with_creature(config, *, pwd=None, llm_override=None) -> Studio`
- `async Studio.from_recipe(recipe, *, pwd=None) -> Studio`
- `async Studio.resume(store_or_path, *, pwd=None, llm_override=None) -> Studio`

Namespaces:

- `studio.sessions` — active engine-backed sessions:
  `start_creature`, `start_terrarium`, `list`, `get`, `stop`,
  `find_creature`, `add_creature`, `remove_creature`, `add_channel`,
  `connect`, `disconnect`, `wire_output`, `unwire_output`,
  `search_memory`.
- `studio.sessions.chat` — `chat`, `regenerate`, `edit_message`,
  `rewind`, `history`, `branches`. `chat(...)` is awaited to produce
  an async iterator of text chunks.
- `studio.sessions.ctl` — `interrupt`, `list_jobs`, `cancel_job`,
  `promote_job`.
- `studio.sessions.state` — `scratchpad`, `patch_scratchpad`,
  `triggers`, `env`, `system_prompt`, `working_dir`,
  `set_working_dir`.
- `studio.sessions.plugins` — `list`, `toggle`.
- `studio.sessions.model` — `switch`, `native_tool_options`.
- `studio.sessions.command` — `execute`.
- `studio.catalog` — `packages`, `creatures`, `modules`, `builtins`,
  `introspect` namespaces.
- `studio.identity` — `llm`, `keys`, `codex`, `mcp`, `ui_prefs`,
  `settings` namespaces.
- `studio.persistence` — `list`, `resume`, `fork`, `delete`,
  `history_index`, `history`, `resolve_path`, plus `viewer` payload
  builders (`tree`, `summary`, `turns`, `events`, `diff`, `export`).
- `studio.editors` — `creatures` and `modules` CRUD/scaffold helpers.
- `studio.attach` — `policies_for_creature`, `policies_for_session`.

See [guides/studio](../guides/studio.md) for task-oriented examples and
[concepts/studio](../concepts/studio.md) for the layer model.

### `Terrarium`

Module: `kohakuterrarium.terrarium.engine`. The runtime engine — one
per process. Hosts every running creature and owns graph-level state.

Classmethod factories:

- `async Terrarium.with_creature(config, *, pwd=None) -> tuple[Terrarium, Creature]`
  — engine + one creature.
- `async Terrarium.from_recipe(recipe, *, pwd=None) -> Terrarium` —
  engine with a recipe applied. ``recipe`` is a `TerrariumConfig` or
  YAML path.
- `async Terrarium.resume(store, *, pwd=None, llm_override=None) -> Terrarium`
  — build a fresh engine and adopt a saved session.

Constructor:

- `Terrarium(*, pwd=None, session_dir=None)` — bare engine. Use
  `add_creature` / `apply_recipe` to populate.

Async context manager:

- `async with Terrarium() as engine: ...` — `__aexit__` calls
  `shutdown()`.

Creature CRUD:

- `async add_creature(config, *, graph=None, creature_id=None, llm_override=None, pwd=None, start=True) -> Creature`
- `async remove_creature(creature) -> None`
- `get_creature(creature_id) -> Creature`
- `list_creatures() -> list[Creature]`
- Pythonic accessors: `engine[id]`, `id in engine`, `for c in engine`,
  `len(engine)`.

Channel CRUD:

- `async add_channel(graph, name, kind=ChannelKind.BROADCAST, description="") -> ChannelInfo`
- `async connect(sender, receiver, *, channel=None, kind=ChannelKind.QUEUE) -> ConnectionResult`
  — cross-graph connect merges graphs (environment union, session
  stores merge).
- `async disconnect(sender, receiver, *, channel=None) -> DisconnectionResult`
  — may split a graph (parent session copied to each side).

Output wiring:

- `async wire_output(creature, sink: OutputModule) -> str`
- `async unwire_output(creature, sink_id: str) -> bool`

Graphs:

- `get_graph(graph_id) -> GraphTopology`
- `list_graphs() -> list[GraphTopology]`

Recipe:

- `async apply_recipe(recipe, *, graph=None, pwd=None, creature_builder=None) -> GraphTopology`

Lifecycle:

- `async start(creature) / async stop(creature)`
- `async stop_graph(graph) -> None`
- `async shutdown() -> None` — idempotent.

Observability:

- `async subscribe(filter=None) -> AsyncIterator[EngineEvent]`
- `status() -> dict` (roll-up) / `status(creature) -> dict`

Session:

- `async attach_session(graph, store: SessionStore) -> None`

`Creature` and graph IDs may be passed as the object or as a string ID
anywhere a `CreatureRef` / `GraphRef` is accepted.

### `Creature`

Module: `kohakuterrarium.terrarium.creature_host`. A running creature
handle. Returned by `add_creature` / `with_creature`; never
constructed directly.

Attributes:

- `creature_id: str`, `name: str`, `graph_id: str`
- `agent: Agent` — the underlying LLM controller.
- `listen_channels: list[str]`, `send_channels: list[str]`
- `is_running: bool`

Methods:

- `async start() / async stop()` — idempotent.
- `async inject_input(message, *, source="chat") -> None`
- `async chat(message) -> AsyncIterator[str]` — push input, stream
  the text response.
- `get_status() -> dict` — model, max_context, compact_threshold,
  provider, session_id, tools, subagents, pwd, graph_id, listen /
  send channels.
- `get_log_entries(last_n=20) -> list[LogEntry]`
- `get_log_text(last_n=10) -> str`

### `EngineEvent`, `EventKind`, `EventFilter`

Module: `kohakuterrarium.terrarium.events`.

Every observable thing the engine does surfaces as an `EngineEvent`.

**`EventKind`** (`str`-valued enum):

- `TEXT`, `ACTIVITY`, `CHANNEL_MESSAGE`
- `TOPOLOGY_CHANGED`, `SESSION_FORKED`
- `CREATURE_STARTED`, `CREATURE_STOPPED`
- `PROCESSING_START`, `PROCESSING_END`
- `ERROR`

**`EngineEvent`** dataclass:

- `kind: EventKind`
- `creature_id: str | None`
- `graph_id: str | None`
- `channel: str | None`
- `payload: dict`
- `ts: float`

**`EventFilter`** dataclass:

- `kinds: set[EventKind] | None`
- `creature_ids: set[str] | None`
- `graph_ids: set[str] | None`
- `channels: set[str] | None`
- `matches(ev: EngineEvent) -> bool`

Fields are AND-combined; `None` means "any". Pass `EventFilter()` (or
omit) to receive everything.

### `ConnectionResult`, `DisconnectionResult`

Module: `kohakuterrarium.terrarium.events`.

**`ConnectionResult`** — returned by `Terrarium.connect`:

- `channel: str` — channel name (created if needed).
- `trigger_id: str` — the receiver's injected `ChannelTrigger` id.
- `delta_kind: str` — `"nothing"` or `"merge"`.

**`DisconnectionResult`** — returned by `Terrarium.disconnect`:

- `channels: list[str]` — channels that were unwired.
- `delta_kind: str` — `"nothing"` or `"split"`.

### `GraphTopology`, `ChannelKind`, `ChannelInfo`

Module: `kohakuterrarium.terrarium.topology`. Pure-data topology
model — no live agent references.

**`GraphTopology`** — one connected component:

- `graph_id: str`
- `creature_ids: set[str]`
- `channels: dict[str, ChannelInfo]`
- `listen_edges: dict[str, set[str]]`
- `send_edges: dict[str, set[str]]`
- `has_creature(creature_id) -> bool`
- `has_channel(name) -> bool`

**`ChannelKind`** enum: `BROADCAST`, `QUEUE`.

**`ChannelInfo`** — `name, kind, description`.

### Compose interop

`Creature` is intended to satisfy the `Runnable` protocol from
`kohakuterrarium.compose.core`, so creatures slot into pipelines via
`>>` / `&` / `|` / `*`. Integration tests covering this composition
path still need to be added.

---

## `kohakuterrarium.core`

### `Agent`

Module: `kohakuterrarium.core.agent`.

Main orchestrator: wires LLM, controller, executor, triggers, I/O, and
plugins together. Subclasses `AgentInitMixin`, `AgentHandlersMixin`,
and `AgentMessagesMixin`.

Classmethod factory:

```python
Agent.from_path(
    config_path: str,
    *,
    input_module: InputModule | None = None,
    output_module: OutputModule | None = None,
    session: Session | None = None,
    environment: Environment | None = None,
    llm_override: str | None = None,
    pwd: str | None = None,
) -> Agent
```

Lifecycle:

- `async start() -> None` — start I/O, output, triggers, LLM, plugins.
- `async stop() -> None` — stop all modules cleanly.
- `async run() -> None` — full event loop. Calls `start()` if not
  already started.
- `interrupt() -> None` — non-blocking; safe to call from any thread.

Input and events:

- `async inject_input(content: str | list[ContentPart], source: str = "programmatic") -> None`
- `async inject_event(event: TriggerEvent) -> None`

Runtime controls:

- `switch_model(profile_name: str) -> str` — returns the canonical `provider/name[@variations]` identifier.
- `llm_identifier() -> str` — return the currently-bound canonical model identifier.
- `async add_trigger(trigger: BaseTrigger, trigger_id: str | None = None) -> str`
- `async remove_trigger(trigger_id_or_trigger: str | BaseTrigger) -> bool`
- `update_system_prompt(content: str, replace: bool = False) -> None`
- `get_system_prompt() -> str`
- `attach_session_store(store: Any) -> None`
- `set_output_handler(handler: Any, replace_default: bool = False) -> None`
- `get_state() -> dict[str, Any]` — name, running, tools, subagents,
  message count, pending jobs.

Properties:

- `is_running: bool`
- `tools: list[str]`
- `subagents: list[str]`
- `conversation_history: list[dict]`

Attributes:

- `config: AgentConfig`
- `llm: LLMProvider`
- `controller: Controller`
- `executor: Executor`
- `registry: Registry`
- `session: Session`
- `environment: Environment | None`
- `input: InputModule`
- `output_router: OutputRouter`
- `trigger_manager: TriggerManager`
- `session_store: Any`
- `compact_manager: Any`
- `plugins: Any`
- `skills: SkillRegistry | None`

Notes:

- `environment` is provided by the `Terrarium` graph hosting the creature;
  direct standalone `Agent` use may leave it as `None`.
- An `Agent` instance is not reusable after `stop()`; build a new one to
  resume from a `SessionStore`.

```python
agent = Agent.from_path("creatures/my_agent", llm_override="claude-opus-4.7")
await agent.start()
await agent.inject_input("Hello")
await agent.stop()
```

### `AgentConfig`

Module: `kohakuterrarium.core.config_types`. Dataclass.

Every creature configuration field. See
[configuration.md](configuration.md) for the YAML form.

Fields:

- `name: str`
- `version: str = "1.0"`
- `base_config: str | None = None`
- `llm_profile: str = ""`
- `model: str = ""`
- `provider: str = ""`
- `variation_selections: dict[str, str]`
- `variation: str = ""`
- `auth_mode: str = ""`
- `api_key_env: str = ""`
- `base_url: str = ""`
- `temperature: float = 0.7`
- `max_tokens: int | None = None`
- `reasoning_effort: str = "medium"`
- `service_tier: str | None = None`
- `extra_body: dict[str, Any]`
- `system_prompt: str = "You are a helpful assistant."`
- `system_prompt_file: str | None = None`
- `prompt_context_files: dict[str, str]`
- `skill_mode: str = "dynamic"`
- `include_tools_in_prompt: bool = True`
- `include_hints_in_prompt: bool = True`
- `max_messages: int = 0`
- `ephemeral: bool = False`
- `input: InputConfig`
- `triggers: list[TriggerConfig]`
- `tools: list[ToolConfigItem]`
- `subagents: list[SubAgentConfigItem]`
- `output: OutputConfig`
- `compact: dict[str, Any] | None = None`
- `startup_trigger: dict[str, Any] | None = None`
- `termination: dict[str, Any] | None = None`
- `max_subagent_depth: int = 3`
- `tool_format: str | dict = "bracket"`
- `agent_path: Path | None = None`
- `session_key: str | None = None`
- `mcp_servers: list[dict[str, Any]]`
- `plugins: list[dict[str, Any]]`
- `memory: dict[str, Any]`
- `output_wiring: list[Any]`
- `disable_provider_tools: list[str]`
- `max_iterations: int | None = None`
- `sanitize_orphan_tool_calls: bool = True`
- `skills: list[str]`
- `skill_index_budget_bytes: int = 4096`
- `framework_hint_overrides: dict[str, str]`

Methods:

- `get_api_key() -> str | None` — read the configured env var.

### `InputConfig`, `OutputConfig`, `OutputConfigItem`, `TriggerConfig`, `ToolConfigItem`, `SubAgentConfigItem`

Module: `kohakuterrarium.core.config_types`. Dataclasses.

**`InputConfig`**

- `type: str = "cli"` — input module type (`cli`, `cli_nonblocking`, `tui`, `none`, `custom`, `package`).
- `module: str | None = None`
- `class_name: str | None = None` — populated from the YAML key `class`.
- `prompt: str = "> "`
- `options: dict[str, Any]`

**`TriggerConfig`**

- `type: str` — built-ins are `timer`, `context`, `channel`; custom/package triggers use `module` + YAML `class`.
- `module, class_name: str | None`
- `prompt: str | None = None`
- `options: dict[str, Any]`
- `name: str | None = None`

**`ToolConfigItem`**

- `name: str`
- `type: str = "builtin"` — `builtin`, `trigger`, `custom`, or `package`.
- `module, class_name: str | None`
- `doc: str | None = None` — override skill doc path.
- `options: dict[str, Any]`

**`OutputConfigItem`**

- `type: str = "stdout"`
- `module, class_name: str | None`
- `options: dict[str, Any]`

**`OutputConfig`**

Inherits `OutputConfigItem` plus:

- `controller_direct: bool = True`
- `named_outputs: dict[str, OutputConfigItem]`

**`SubAgentConfigItem`**

- `name: str`
- `type: str = "builtin"`
- `module, class_name, config_name, description: str | None` — `class_name` / `config_name` are populated from YAML `class` / `config`.
- `tools: list[str]`
- `can_modify: bool = False`
- `interactive: bool = False`
- `options: dict[str, Any]`

### `load_agent_config`

Module: `kohakuterrarium.core.config`.

```python
load_agent_config(config_path: str) -> AgentConfig
```

Resolves YAML/JSON/TOML (`config.yaml` → `.yml` → `.json` → `.toml`),
applies `base_config` inheritance, env-var interpolation, and path
resolution.

### `Conversation`, `ConversationConfig`, `ConversationMetadata`

Module: `kohakuterrarium.core.conversation`.

Conversation manages message history and OpenAI-format serialisation.

Methods:

- `append(role, content, **kwargs) -> Message`
- `append_message(message: Message) -> None`
- `to_messages() -> list[dict]`
- `get_messages() -> MessageList`
- `get_context_length() -> int`
- `get_image_count() -> int`
- `get_system_message() -> Message | None`
- `get_last_message() -> Message | None`
- `get_last_assistant_message() -> Message | None`
- `truncate_from(index: int) -> list[Message]`
- `find_last_user_index() -> int`
- `clear(keep_system: bool = True) -> None`
- `to_json() -> str`
- `from_json(json_str: str) -> Conversation`

`ConversationConfig`:

- `max_messages: int = 0`
- `keep_system: bool = True`

`ConversationMetadata`:

- `created_at, updated_at: datetime`
- `message_count: int = 0`
- `total_chars: int = 0`

### `TriggerEvent`, `EventType`

Module: `kohakuterrarium.core.events`.

Universal event carried between inputs, triggers, tools, sub-agents.

Fields:

- `type: str`
- `content: EventContent = ""` (`str` or `list[ContentPart]`)
- `context: dict[str, Any]`
- `timestamp: datetime`
- `job_id: str | None = None`
- `prompt_override: str | None = None`
- `stackable: bool = True`

Methods:

- `get_text_content() -> str`
- `is_multimodal() -> bool`
- `with_context(**kwargs) -> TriggerEvent` — non-mutating.

`EventType` constants: `USER_INPUT`, `IDLE`, `TIMER`,
`CONTEXT_UPDATE`, `TOOL_COMPLETE`, `SUBAGENT_OUTPUT`,
`CHANNEL_MESSAGE`, `MONITOR`, `ERROR`, `STARTUP`, `SHUTDOWN`.

Factories:

- `create_user_input_event(content, source="cli", **extra_context) -> TriggerEvent`
- `create_tool_complete_event(job_id, content, exit_code=None, error=None, **extra_context) -> TriggerEvent`
- `create_error_event(error_type, message, job_id=None, **extra_context) -> TriggerEvent`
  (`stackable=False`).

### Channels

Module: `kohakuterrarium.core.channel`.

**`ChannelMessage`**

- `sender: str`
- `content: str | dict | list[dict]`
- `metadata: dict[str, Any]`
- `timestamp: datetime`
- `message_id: str`
- `reply_to: str | None = None`
- `channel: str | None = None`

**`BaseChannel`** (abstract)

- `async send(message: ChannelMessage) -> None`
- `on_send(callback) -> None`
- `remove_on_send(callback) -> None`
- `channel_type: str` — `"queue"` or `"broadcast"`.
- `empty: bool`
- `qsize: int`

**`SubAgentChannel`** (point-to-point queue)

- `async receive(timeout: float | None = None) -> ChannelMessage`
- `try_receive() -> ChannelMessage | None`

**`AgentChannel`** (broadcast)

- `subscribe(subscriber_id: str) -> ChannelSubscription`
- `unsubscribe(subscriber_id: str) -> None`
- `subscriber_count: int`

**`ChannelSubscription`**

- `async receive(timeout=None) -> ChannelMessage`
- `try_receive() -> ChannelMessage | None`
- `unsubscribe() -> None`
- `empty, qsize`

**`ChannelRegistry`**

- `get_or_create(name, channel_type="queue", maxsize=0, description="") -> BaseChannel`
- `get(name) -> BaseChannel | None`
- `list_channels() -> list[str]`
- `remove(name) -> bool`
- `get_channel_info() -> list[dict]` — for prompt injection.

### `Session`, `Scratchpad`, `Environment`

Module: `kohakuterrarium.core.session`, `core.scratchpad`, `core.environment`.

**`Session`**

Dataclass of per-creature shared state.

- `key: str`
- `channels: ChannelRegistry`
- `scratchpad: Scratchpad`
- `tui: Any | None = None`
- `extra: dict[str, Any]`

Module-level functions:

- `get_session(key=None) -> Session`
- `set_session(session, key=None) -> None`
- `remove_session(key=None) -> None`
- `list_sessions() -> list[str]`
- `get_scratchpad() -> Scratchpad`
- `get_channel_registry() -> ChannelRegistry`

**`Scratchpad`**

Key-value string store.

- `set(key, value) -> None`
- `get(key) -> str | None`
- `delete(key) -> bool`
- `list_keys() -> list[str]`
- `clear() -> None`
- `to_dict() -> dict[str, str]`
- `to_prompt_section() -> str`
- `__len__`, `__contains__`

**`Environment`**

Shared execution context for a terrarium.

- `env_id: str`
- `shared_channels: ChannelRegistry`
- `get_session(key) -> Session` — creature-private.
- `list_sessions() -> list[str]`
- `register(key, value) -> None`
- `get(key, default=None) -> Any`

### Jobs

Module: `kohakuterrarium.core.job`.

**`JobType`** enum: `TOOL`, `SUBAGENT`, `COMMAND`.

**`JobState`** enum: `PENDING`, `RUNNING`, `DONE`, `ERROR`, `CANCELLED`.

**`JobStatus`**

- `job_id: str`
- `job_type: JobType`
- `type_name: str`
- `state: JobState = PENDING`
- `start_time: datetime`
- `end_time: datetime | None = None`
- `output_lines: int = 0`
- `output_bytes: int = 0`
- `preview: str = ""`
- `error: str | None = None`
- `context: dict[str, Any]`

Properties: `duration`, `is_complete`, `is_running`.

Methods: `to_context_string() -> str`.

**`JobResult`**

- `job_id: str`
- `output: str = ""`
- `exit_code: int | None = None`
- `error: str | None = None`
- `metadata: dict[str, Any]`
- `success: bool` property.
- `get_lines(start=0, count=None) -> list[str]`
- `truncated(max_chars=1000) -> str`

**`JobStore`**

- `register(status) -> None`
- `get_status(job_id) -> JobStatus | None`
- `update_status(job_id, state=None, output_lines=None, ...) -> JobStatus | None`
- `store_result(result) -> None`
- `get_result(job_id) -> JobResult | None`
- `get_running_jobs() -> list[JobStatus]`
- `get_pending_jobs() -> list[JobStatus]`
- `get_completed_jobs() -> list[JobStatus]`
- `get_all_statuses() -> list[JobStatus]`
- `format_context(include_completed=False) -> str`

Utilities:

- `generate_job_id(prefix="job") -> str`

### Termination

Module: `kohakuterrarium.core.termination`.

**`TerminationConfig`**

- `max_turns: int = 0`
- `max_tokens: int = 0` (reserved)
- `max_duration: float = 0`
- `idle_timeout: float = 0`
- `keywords: list[str]`

**`TerminationChecker`**

- `start() -> None`
- `record_turn() -> None`
- `record_activity() -> None`
- `record_tool_result(result) -> None`
- `attach_plugins(manager) -> None`
- `attach_scratchpad(scratchpad) -> None`
- `should_terminate(last_output: str = "") -> bool`
- `force_terminate(reason: str) -> None`
- `reason, turn_count, elapsed, is_active` properties.

**`TerminationDecision`** — `should_stop: bool`, `reason: str = ""`.

**`TerminationContext`** — `turn_count`, `elapsed`, `idle_time`, `last_output`, `scratchpad`, `recent_tool_results`.

---

## `kohakuterrarium.llm`

### `LLMProvider` (protocol), `BaseLLMProvider`

Module: `kohakuterrarium.llm.base`.

Async protocol:

- `async chat(messages, *, stream=True, tools=None, **kwargs) -> AsyncIterator[str]`
- `async chat_complete(messages, **kwargs) -> ChatResponse`
- property `last_tool_calls: list[NativeToolCall]`

Subclass `BaseLLMProvider` to implement:

- `async _stream_chat(messages, *, tools=None, **kwargs)`
- `async _complete_chat(messages, **kwargs) -> ChatResponse`

Base attributes: `config: LLMConfig`, `last_usage: dict[str, int]`.

### Message types

Module: `kohakuterrarium.llm.base` / `kohakuterrarium.llm.message`.

**`LLMConfig`**

- `model: str`
- `temperature: float = 0.7`
- `max_tokens: int | None = None`
- `top_p: float = 1.0`
- `stop: list[str] | None = None`
- `extra: dict[str, Any] | None = None`

**`ChatChunk`**

- `content: str = ""`
- `finish_reason: str | None = None`
- `usage: dict[str, int] | None = None`

**`ChatResponse`**

- `content, finish_reason, model: str`
- `usage: dict[str, int]`

**`ToolSchema`**

- `name, description: str`
- `parameters: dict[str, Any]`
- `to_api_format() -> dict`

**`NativeToolCall`**

- `id, name, arguments: str`
- `parsed_arguments() -> dict`

**`Message`**

- `role: Role` (`"system"`, `"user"`, `"assistant"`, `"tool"`)
- `content: str | list[ContentPart]`
- `name, tool_call_id: str | None`
- `tool_calls: list[dict] | None`
- `metadata: dict`
- `to_dict() / from_dict(data)`
- `get_text_content() -> str`
- `has_images() -> bool`
- `get_images() -> list[ImagePart]`
- `is_multimodal() -> bool`

Subclasses `SystemMessage`, `UserMessage`, `AssistantMessage`,
`ToolMessage` enforce role.

**`TextPart`** — `text: str`, `type: "text"`.

**`ImagePart`** — `url, detail ("auto"|"low"|"high"), source_type, source_name`;
`get_description() -> str`.

**`FilePart`** — file reference counterpart.

Factories:

- `create_message(role, content, **kwargs) -> Message`
- `make_multimodal_content(text, images=None, prepend_images=False) -> str | list[ContentPart]`
- `normalize_content_parts(content) -> str | list[ContentPart] | None`

Aliases: `Role`, `MessageContent`, `ContentPart`, `MessageList`.

### Profiles

Module: `kohakuterrarium.llm.profiles`, `kohakuterrarium.llm.profile_types`.

**`LLMBackend`** — `name, backend_type, base_url, api_key_env, provider_name, provider_native_tools`.

**`LLMPreset`** — `name, model, provider, max_context, max_output, temperature, reasoning_effort, service_tier, extra_body`.

**`LLMProfile`** — resolved runtime merge of preset + backend:
`name, model, provider, backend_type, max_context, max_output, base_url, api_key_env, temperature, reasoning_effort, service_tier, extra_body`.

Module-level functions:

- `load_backends() -> dict[str, LLMBackend]`
- `load_presets() -> dict[str, LLMPreset]`
- `load_profiles() -> dict[str, LLMProfile]`
- `save_backend(backend) -> None`
- `delete_backend(name) -> bool`
- `save_profile(profile) -> None`
- `delete_profile(name) -> bool`
- `get_profile(name) -> LLMProfile | None`
- `get_preset(name) -> LLMProfile | None`
- `get_default_model() -> str`
- `set_default_model(model_name) -> None`
- `resolve_controller_llm(controller_config, llm_override=None) -> LLMProfile | None`
- `list_all() -> list[dict]`

Built-in provider names: `codex`, `openai`, `openrouter`, `anthropic`,
`gemini`, `mimo`.

### API keys

Module: `kohakuterrarium.llm.api_keys`.

- `save_api_key(provider, key) -> None`
- `get_api_key(provider_or_env) -> str`
- `list_api_keys() -> dict[str, str]` (masked).
- `KT_DIR: Path`
- `KEYS_PATH: Path`
- `PROVIDER_KEY_MAP: dict[str, str]`

---

## `kohakuterrarium.session`

### `SessionStore`

Module: `kohakuterrarium.session.store`. SQLite-backed (KohakuVault).

Tables: `meta`, `state`, `events`, `channels`, `subagents`, `jobs`,
`conversation`, `fts`.

Events:

- `append_event(agent, event_type, data) -> str`
- `get_events(agent) -> list[dict]`
- `get_resumable_events(agent) -> list[dict]`
- `get_all_events() -> list[tuple[str, dict]]`

Conversation snapshots:

- `save_conversation(agent, messages) -> None`
- `load_conversation(agent) -> list[dict] | None`

State:

- `save_state(agent, *, scratchpad=None, turn_count=None, token_usage=None, triggers=None, compact_count=None) -> None`
- `load_scratchpad(agent) -> dict[str, str]`
- `load_turn_count(agent) -> int`
- `load_token_usage(agent) -> dict[str, int]`
- `load_triggers(agent) -> list[dict]`

Channels:

- `save_channel_message(channel, data) -> str`
- `get_channel_messages(channel) -> list[dict]`

Sub-agents:

- `next_subagent_run(parent, name) -> int`
- `save_subagent(parent, name, run, meta, conv_json=None) -> None`
- `load_subagent_meta(parent, name, run) -> dict | None`
- `load_subagent_conversation(parent, name, run) -> str | None`

Jobs:

- `save_job(job_id, data) -> None`
- `load_job(job_id) -> dict | None`

Metadata:

- `init_meta(session_id, config_type, config_path, pwd, agents, config_snapshot=None, terrarium_name=None, terrarium_channels=None, terrarium_creatures=None) -> None`
- `update_status(status) -> None`
- `touch() -> None`
- `load_meta() -> dict[str, Any]`

Misc:

- `search(query, k=10) -> list[dict]` — FTS5 BM25.
- `flush() -> None`
- `close(update_status=True) -> None`
- `path: str` property.

### Session artifacts

Module: `kohakuterrarium.session.artifacts`.

Helpers for binary artifacts stored beside a session file:

- `artifacts_dir_for(session_path: Path) -> Path`
- `resolve_artifact_relpath(filename: str) -> Path`
- `write_artifact_bytes(artifacts_dir: Path, filename: str, data: bytes) -> Path`

### `SessionMemory`

Module: `kohakuterrarium.session.memory`.

Indexed search (FTS + vector + hybrid).

- `index_events(agent) -> None`
- `async search(query, mode="hybrid", k=5) -> list[SearchResult]`

**`SearchResult`**

- `content: str`
- `round_num, block_num: int`
- `agent: str`
- `block_type: str` — `"text"`, `"tool"`, `"trigger"`, `"user"`.
- `score: float`
- `ts: float`
- `tool_name, channel: str`

### Embedding providers

Module: `kohakuterrarium.session.embedding`.

Provider types: `model2vec`, `sentence-transformer`, `api`. API
providers include `GeminiEmbedder`. Aliases: `@tiny`, `@base`,
`@retrieval`, `@best`, `@multilingual`, `@multilingual-best`,
`@science`, `@nomic`, `@gemma`.

---

## `kohakuterrarium.terrarium` recipe compatibility

The config dataclasses and loader below describe terrarium recipe files
consumed by `Terrarium.from_recipe(...)` / `apply_recipe(...)`.


### `TerrariumConfig`, `CreatureConfig`, `ChannelConfig`, `RootConfig`

Module: `kohakuterrarium.terrarium.config`. Dataclasses.

**`TerrariumConfig`**

- `name: str`
- `creatures: list[CreatureConfig]`
- `channels: list[ChannelConfig]`
- `root: RootConfig | None = None`

**`CreatureConfig`**

- `name: str`
- `config_data: dict`
- `base_dir: Path`
- `listen_channels: list[str]`
- `send_channels: list[str]`
- `output_log: bool = False`
- `output_log_size: int = 100`

**`ChannelConfig`**

- `name: str`
- `channel_type: str = "queue"`
- `description: str = ""`

**`RootConfig`**

- `config_data: dict`
- `base_dir: Path`

Functions:

- `load_terrarium_config(config_path: str) -> TerrariumConfig`
- `build_channel_topology_prompt(config, creature) -> str`

---

## `kohakuterrarium.serving`

Serving helpers launch or support the HTTP API and web / desktop frontends.
User-facing session management should go through `Studio`; route handlers
should delegate catalog, identity, session, persistence, attach, and editor
policy to the corresponding Studio namespaces.

---

## Module protocols (extension API)

### `Tool`

Module: `kohakuterrarium.modules.tool.base`.

Protocol / `BaseTool` base class.

- `async execute(args: dict, context: ToolContext | None = None) -> ToolResult` — required.
- `needs_context: bool = False`
- `is_provider_native: bool = False`
- `provider_support: frozenset[str]`
- `is_concurrency_safe: bool = True`
- `prompt_contribution_bucket: str = "normal"`
- `prompt_contribution() -> str | None`
- `provider_native_options() -> dict[str, Any]`

### `InputModule`

Module: `kohakuterrarium.modules.input.base`. `BaseInputModule`
provides user-command dispatch.

- `async start() / async stop()`
- `async get_input() -> TriggerEvent | None`

### `OutputModule`

Module: `kohakuterrarium.modules.output.base`. `BaseOutputModule`
base class.

- `async start() / async stop()`
- `async write(content: str) -> None`
- `async write_stream(chunk: str) -> None`
- `async flush() -> None`
- `async on_processing_start() / async on_processing_end()`
- `on_activity(activity_type: str, detail: str) -> None`
- `async on_user_input(text: str) -> None` (optional)
- `async on_resume(events: list[dict]) -> None` (optional)

Activity types: `tool_start`, `tool_done`, `tool_error`,
`subagent_start`, `subagent_done`, `subagent_error`.

### `BaseTrigger`

Module: `kohakuterrarium.modules.trigger.base`.

- `async wait_for_trigger() -> TriggerEvent | None` — required.
- `async _on_start() / async _on_stop()` — optional.
- `_on_context_update(context: dict) -> None` — optional.
- `resumable: bool = False`
- `universal: bool = False`
- `to_resume_dict() -> dict` / `from_resume_dict(data) -> BaseTrigger`
- `__init__(prompt: str | None = None, **options)`

### `SubAgent`

Module: `kohakuterrarium.modules.subagent.base`.

- `async run(input_text: str) -> SubAgentResult`
- `async cancel() -> None`
- `get_status() -> SubAgentJob`
- `get_pending_count() -> int`

Attributes: `config: SubAgentConfig`, `llm`, `registry`, `executor`,
`conversation`.

Support classes in `kohakuterrarium.modules.subagent`:
`SubAgentResult`, `SubAgentJob`, `SubAgentManager`,
`InteractiveSubAgent`, `InteractiveManagerMixin`, `SubAgentConfig`.

### Plugin hooks

Module: `kohakuterrarium.modules.plugin`. See
[plugin-hooks.md](plugin-hooks.md) for every hook, signature, and
timing.

---

## `kohakuterrarium.compose`

Pipeline algebra for composing agents and pure functions.

### `BaseRunnable`

- `async run(input) -> Any`
- `async __call__(input) -> Any`
- `__rshift__(other)` — `>>` sequence.
- `__and__(other)` — `&` parallel.
- `__or__(other)` — `|` fallback.
- `__mul__(n)` — `*` retry.
- `iterate(initial_input) -> PipelineIterator`
- `map(fn) -> BaseRunnable` — post-transform output.
- `contramap(fn) -> BaseRunnable` — pre-transform input.
- `fails_when(predicate) -> BaseRunnable`

### Factories

Module: `kohakuterrarium.compose.core`.

- `Pure(fn)` / `pure(fn)` — wrap sync or async callable.
- `Sequence(*stages)` — chain.
- `Product(*stages)` — parallel (`asyncio.gather`).
- `Fallback(*stages)`
- `Retry(stage, attempts)`
- `Router(mapping)` — dict-based dispatch.
- `Iterator(...)` — iteration over async source.
- `effects.Effects()` — side-effect logging handle.

### Agent composition

Module: `kohakuterrarium.compose.agent`.

- `async agent(config_path: str) -> AgentRunnable` — persistent agent,
  reused across calls (async context manager).
- `factory(config: AgentConfig) -> AgentRunnable` — ephemeral factory;
  a fresh agent per call.

Operator precedence: `* > | > & > >>`.

```python
from kohakuterrarium.compose import agent, pure

async with await agent("@kt-biome/creatures/swe") as swe:
    async with await agent("@kt-biome/creatures/researcher") as reviewer:
        pipeline = swe >> pure(extract_code) >> reviewer
        result = await pipeline("Implement feature")
```

---

## `kohakuterrarium.testing`

### `TestAgentBuilder`

Module: `kohakuterrarium.testing.agent`. Fluent builder for
deterministic agent tests.

Builder methods (return `self`):

- `with_llm_script(script)`
- `with_llm(llm: ScriptedLLM)`
- `with_output(output: OutputRecorder)`
- `with_system_prompt(prompt)`
- `with_session(key)`
- `with_builtin_tools(tool_names)`
- `with_tool(tool)`
- `with_named_output(name, output)`
- `with_ephemeral(ephemeral=True)`
- `build() -> TestAgentEnv`

`TestAgentEnv`:

- Properties: `llm: ScriptedLLM`, `output: OutputRecorder`, `session: Session`.
- Methods: `async inject(content)`, `async chat(content) -> str`.

### `ScriptedLLM`

Module: `kohakuterrarium.testing.llm`.

Constructor: `ScriptedLLM(script: list[ScriptEntry] | list[str] | None = None)`.

**`ScriptEntry`**: `response: str`, `match: str | None = None`,
`delay_per_chunk: float = 0`, `chunk_size: int = 10`.

Methods: `async chat`, `async chat_complete`.

Assertion surface: `call_count: int`, `call_log: list[list[dict]]`.

### `OutputRecorder`

Module: `kohakuterrarium.testing.output`.

- `all_text: str`
- `chunks: list[str]`
- `writes: list[str]`
- `activities: list[tuple[str, str]]`

### `EventRecorder`

Module: `kohakuterrarium.testing.events`.

- `record(event) -> None`
- `get_all() -> list[TriggerEvent]`
- `get_by_type(event_type) -> list[TriggerEvent]`
- `clear() -> None`

---

## Packages

Module: `kohakuterrarium.packages`.

- `is_package_ref(path: str) -> bool`
- `resolve_package_path(ref: str) -> Path`
- `list_packages() -> list[dict]`
- `install_package(source, name=None, editable=False) -> None`
- `update_package(name) -> str`
- `uninstall_package(name) -> bool`
- `resolve_package_tool(name) -> tuple[str, str] | None`
- `resolve_package_io(name) -> tuple[str, str] | None`
- `resolve_package_trigger(name) -> tuple[str, str] | None`
- `resolve_package_command(name) -> dict | None`
- `resolve_package_user_command(name) -> dict | None`
- `resolve_package_prompt(name) -> Path | None`
- `resolve_package_skills(name) -> list[dict] | None`
- `find_package_root_for_path(path) -> Path | None`
- `get_package_framework_hints(pkg_root) -> dict[str, str]`

Package root: `~/.kohakuterrarium/packages/`. Editable installs use
`<name>.link` pointers instead of copies.

---

## See also

- Concepts:
  [composing an agent](../concepts/foundations/composing-an-agent.md),
  [modules/tool](../concepts/modules/tool.md),
  [modules/sub-agent](../concepts/modules/sub-agent.md),
  [impl-notes/session-persistence](../concepts/impl-notes/session-persistence.md).
- Guides:
  [programmatic usage](../guides/programmatic-usage.md),
  [custom modules](../guides/custom-modules.md),
  [plugins](../guides/plugins.md).
- Reference: [cli](cli.md), [http](http.md),
  [configuration](configuration.md), [builtins](builtins.md),
  [plugin-hooks](plugin-hooks.md).
