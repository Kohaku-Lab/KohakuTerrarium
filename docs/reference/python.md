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
| The easiest streaming chat wrapper | `kohakuterrarium.serving.agent_session.AgentSession` |
| Direct agent control | `kohakuterrarium.core.agent.Agent` |
| Multi-agent runtime | `kohakuterrarium.terrarium.runtime.TerrariumRuntime` |
| Transport-agnostic manager | `kohakuterrarium.serving.manager.KohakuManager` |
| Config loading | `kohakuterrarium.core.config.load_agent_config` / `kohakuterrarium.terrarium.config.load_terrarium_config` |
| Persistence / search | `kohakuterrarium.session.store.SessionStore`, `kohakuterrarium.session.memory.SessionMemory` |
| Extension author | `kohakuterrarium.modules.{tool,input,output,trigger,subagent}.base` |
| Pipeline composition | `kohakuterrarium.compose` |
| Tests | `kohakuterrarium.testing` |

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

- `switch_model(profile_name: str) -> str` — returns the resolved model id.
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

Notes:

- `environment` is provided by `TerrariumRuntime` for multi-agent; `None`
  for standalone.
- An `Agent` instance is not reusable after `stop()`; build a new one to
  resume from a `SessionStore`.

```python
agent = Agent.from_path("creatures/my_agent", llm_override="claude-opus-4.6")
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

Methods:

- `get_api_key() -> str | None` — read the configured env var.

### `InputConfig`, `OutputConfig`, `OutputConfigItem`, `TriggerConfig`, `ToolConfigItem`, `SubAgentConfigItem`

Module: `kohakuterrarium.core.config_types`. Dataclasses.

**`InputConfig`**

- `type: str = "cli"` — `builtin`, `custom`, or `package`.
- `module: str | None = None`
- `class_name: str | None = None`
- `prompt: str = "> "`
- `options: dict[str, Any]`

**`TriggerConfig`**

- `type: str`
- `module, class_name: str | None`
- `prompt: str | None = None`
- `options: dict[str, Any]`

**`ToolConfigItem`**

- `name: str`
- `type: str = "builtin"`
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
- `module, class_name, config_name, description: str | None`
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
- `should_terminate(last_output: str = "") -> bool`
- `reason, turn_count, elapsed, is_active` properties.

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

**`LLMBackend`** — `name, backend_type, base_url, api_key_env`.

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

## `kohakuterrarium.terrarium`

### `TerrariumRuntime`

Module: `kohakuterrarium.terrarium.runtime`. Multi-agent orchestrator;
subclasses `HotPlugMixin`.

Lifecycle:

- `async start() -> None`
- `async stop() -> None`
- `async run() -> None`

Hot-plug:

- `async add_creature(name, creature: Agent, ...) -> CreatureHandle`
- `async remove_creature(name) -> bool`
- `async add_channel(name, channel_type) -> None`
- `async wire_channel(creature_name, channel_name, direction) -> None`

Properties: `api: TerrariumAPI`, `observer: ChannelObserver`.

Attributes: `config: TerrariumConfig`, `environment: Environment`,
`_creatures: dict[str, CreatureHandle]`.

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

### `TerrariumAPI`, `ChannelObserver`, `CreatureHandle`

Programmatic control surfaces. `TerrariumAPI` mirrors the terrarium
tools available to the root agent. `ChannelObserver` provides
non-destructive observation. `CreatureHandle` wraps an `Agent` plus
its terrarium wiring.

---

## `kohakuterrarium.serving`

### `KohakuManager`

Module: `kohakuterrarium.serving.manager`. Transport-agnostic manager;
used by the HTTP API and any custom transport.

Agent methods:

- `async agent_create(config_path=None, config=None, llm_override=None, pwd=None) -> str`
- `async agent_stop(agent_id) -> None`
- `async agent_chat(agent_id, message) -> AsyncIterator[str]`
- `agent_status(agent_id) -> dict`
- `agent_list() -> list[dict]`
- `agent_interrupt(agent_id) -> None`
- `agent_get_jobs(agent_id) -> list[dict]`
- `async agent_cancel_job(agent_id, job_id) -> bool`
- `agent_switch_model(agent_id, profile_name) -> str`
- `async agent_execute_command(agent_id, command, args="") -> dict`

Terrarium methods:

- `async terrarium_create(config_path, ...) -> str`
- `async terrarium_stop(terrarium_id) -> None`
- `async terrarium_run(terrarium_id) -> AsyncIterator[str]`
- creature / channel / observer operations mirroring the HTTP surface.

### `AgentSession`

Module: `kohakuterrarium.serving.agent_session`. Thin wrapper around
`Agent` with concurrent input-injection and output streaming.

Factories:

- `async from_path(config_path, llm_override=None, pwd=None) -> AgentSession`
- `async from_config(config: AgentConfig) -> AgentSession`
- `async from_agent(agent: Agent) -> AgentSession`

Methods:

- `async start() / async stop()`
- `async chat(message: str | list[dict]) -> AsyncIterator[str]`
- `get_status() -> dict`

Attributes: `agent_id: str`, `agent: Agent`.

---

## Module protocols (extension API)

### `Tool`

Module: `kohakuterrarium.modules.tool.base`.

Protocol / `BaseTool` base class.

- `async execute(args: dict, context: ToolContext | None = None) -> ToolResult` — required.
- `needs_context: bool = False`
- `parallel_allowed: bool = True`
- `timeout: float = 60.0`
- `max_output: int = 0`

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

async with await agent("@kt-defaults/creatures/swe") as swe:
    async with await agent("@kt-defaults/creatures/reviewer") as reviewer:
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
- `list_packages() -> list[str]`
- `install_package(source, name=None, editable=False) -> None`
- `uninstall_package(name) -> bool`

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
