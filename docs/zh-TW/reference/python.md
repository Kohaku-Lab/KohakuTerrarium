---
title: Python API
summary: kohakuterrarium 套件介面 — Agent、AgentSession、TerrariumRuntime、compose、測試 helper。
tags:
  - reference
  - python
  - api
---

# Python API

`kohakuterrarium` 套件裡所有公開的類別、函式、協定。條目依模組分組。簽名用現代 type hint。

架構請看 [concepts/README](../concepts/README.md)。任務走讀請看 [guides/programmatic-usage](../guides/programmatic-usage.md) 與 [guides/custom-modules](../guides/custom-modules.md)。

## Import 介面

| 想做什麼 | 用這個 |
|---|---|
| 最簡單的串流聊天 wrapper | `kohakuterrarium.serving.agent_session.AgentSession` |
| 直接控制代理 | `kohakuterrarium.core.agent.Agent` |
| 多代理執行期 | `kohakuterrarium.terrarium.runtime.TerrariumRuntime` |
| 與 transport 無關的 manager | `kohakuterrarium.serving.manager.KohakuManager` |
| 載入 config | `kohakuterrarium.core.config.load_agent_config` / `kohakuterrarium.terrarium.config.load_terrarium_config` |
| 持久化 / 搜尋 | `kohakuterrarium.session.store.SessionStore`、`kohakuterrarium.session.memory.SessionMemory` |
| 寫 extension | `kohakuterrarium.modules.{tool,input,output,trigger,subagent}.base` |
| 組管線 | `kohakuterrarium.compose` |
| 寫測試 | `kohakuterrarium.testing` |

---

## `kohakuterrarium.core`

### `Agent`

模組：`kohakuterrarium.core.agent`。

主 orchestrator：把 LLM、controller、executor、觸發器、I/O、外掛串起來。繼承 `AgentInitMixin`、`AgentHandlersMixin`、`AgentMessagesMixin`。

Classmethod factory：

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

生命週期：

- `async start() -> None` — 啟動 I/O、輸出、觸發器、LLM、外掛。
- `async stop() -> None` — 乾淨地停下所有模組。
- `async run() -> None` — 完整事件迴圈。若尚未 start 會先呼叫 `start()`。
- `interrupt() -> None` — 非阻塞；任何 thread 呼叫都安全。

輸入與事件：

- `async inject_input(content: str | list[ContentPart], source: str = "programmatic") -> None`
- `async inject_event(event: TriggerEvent) -> None`

執行期控制：

- `switch_model(profile_name: str) -> str` — 回傳解析後的 model id。
- `async add_trigger(trigger: BaseTrigger, trigger_id: str | None = None) -> str`
- `async remove_trigger(trigger_id_or_trigger: str | BaseTrigger) -> bool`
- `update_system_prompt(content: str, replace: bool = False) -> None`
- `get_system_prompt() -> str`
- `attach_session_store(store: Any) -> None`
- `set_output_handler(handler: Any, replace_default: bool = False) -> None`
- `get_state() -> dict[str, Any]` — name、running、tools、subagents、message count、pending jobs。

屬性：

- `is_running: bool`
- `tools: list[str]`
- `subagents: list[str]`
- `conversation_history: list[dict]`

Attribute：

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

補充：

- `environment` 在多代理時由 `TerrariumRuntime` 提供；獨立代理時為 `None`。
- `Agent` 實例 `stop()` 之後不能重用；要從 `SessionStore` 接回來，請建新的。

```python
agent = Agent.from_path("creatures/my_agent", llm_override="claude-opus-4.7")
await agent.start()
await agent.inject_input("Hello")
await agent.stop()
```

### `AgentConfig`

模組：`kohakuterrarium.core.config_types`。Dataclass。

生物設定的每一個欄位。YAML 形式見 [configuration.md](configuration.md)。

欄位：

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

方法：

- `get_api_key() -> str | None` — 讀對應的環境變數。

### `InputConfig`、`OutputConfig`、`OutputConfigItem`、`TriggerConfig`、`ToolConfigItem`、`SubAgentConfigItem`

模組：`kohakuterrarium.core.config_types`。Dataclass。

**`InputConfig`**

- `type: str = "cli"` — 輸入模組型別（`cli`、`cli_nonblocking`、`tui`、`none`、`custom`、`package`）。
- `module: str | None = None`
- `class_name: str | None = None` — 由 YAML 的 `class` 鍵填入。
- `prompt: str = "> "`
- `options: dict[str, Any]`

**`TriggerConfig`**

- `type: str` — 內建型別是 `timer`、`context`、`channel`；custom/package trigger 用 `module` + YAML `class`。
- `module, class_name: str | None`
- `prompt: str | None = None`
- `options: dict[str, Any]`
- `name: str | None = None`

**`ToolConfigItem`**

- `name: str`
- `type: str = "builtin"` — `builtin`、`trigger`、`custom`、或 `package`。
- `module, class_name: str | None`
- `doc: str | None = None` — 覆寫 skill doc 路徑。
- `options: dict[str, Any]`

**`OutputConfigItem`**

- `type: str = "stdout"`
- `module, class_name: str | None`
- `options: dict[str, Any]`

**`OutputConfig`**

繼承 `OutputConfigItem` 加上：

- `controller_direct: bool = True`
- `named_outputs: dict[str, OutputConfigItem]`

**`SubAgentConfigItem`**

- `name: str`
- `type: str = "builtin"`
- `module, class_name, config_name, description: str | None` — `class_name` / `config_name` 分別由 YAML 的 `class` / `config` 鍵填入。
- `tools: list[str]`
- `can_modify: bool = False`
- `interactive: bool = False`
- `options: dict[str, Any]`

### `load_agent_config`

模組：`kohakuterrarium.core.config`。

```python
load_agent_config(config_path: str) -> AgentConfig
```

解析 YAML/JSON/TOML (`config.yaml` → `.yml` → `.json` → `.toml`)、套 `base_config` 繼承、環境變數插值、路徑解析。

### `Conversation`、`ConversationConfig`、`ConversationMetadata`

模組：`kohakuterrarium.core.conversation`。

Conversation 管訊息歷程與 OpenAI 格式序列化。

方法：

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

`ConversationConfig`：

- `max_messages: int = 0`
- `keep_system: bool = True`

`ConversationMetadata`：

- `created_at, updated_at: datetime`
- `message_count: int = 0`
- `total_chars: int = 0`

### `TriggerEvent`、`EventType`

模組：`kohakuterrarium.core.events`。

在輸入、觸發器、工具、子代理之間流的通用事件。

欄位：

- `type: str`
- `content: EventContent = ""` (`str` 或 `list[ContentPart]`)
- `context: dict[str, Any]`
- `timestamp: datetime`
- `job_id: str | None = None`
- `prompt_override: str | None = None`
- `stackable: bool = True`

方法：

- `get_text_content() -> str`
- `is_multimodal() -> bool`
- `with_context(**kwargs) -> TriggerEvent` — 不會 mutate 原物件。

`EventType` 常數：`USER_INPUT`、`IDLE`、`TIMER`、`CONTEXT_UPDATE`、`TOOL_COMPLETE`、`SUBAGENT_OUTPUT`、`CHANNEL_MESSAGE`、`MONITOR`、`ERROR`、`STARTUP`、`SHUTDOWN`。

Factory：

- `create_user_input_event(content, source="cli", **extra_context) -> TriggerEvent`
- `create_tool_complete_event(job_id, content, exit_code=None, error=None, **extra_context) -> TriggerEvent`
- `create_error_event(error_type, message, job_id=None, **extra_context) -> TriggerEvent` (`stackable=False`)。

### Channel

模組：`kohakuterrarium.core.channel`。

**`ChannelMessage`**

- `sender: str`
- `content: str | dict | list[dict]`
- `metadata: dict[str, Any]`
- `timestamp: datetime`
- `message_id: str`
- `reply_to: str | None = None`
- `channel: str | None = None`

**`BaseChannel`** (抽象)

- `async send(message: ChannelMessage) -> None`
- `on_send(callback) -> None`
- `remove_on_send(callback) -> None`
- `channel_type: str` — `"queue"` 或 `"broadcast"`。
- `empty: bool`
- `qsize: int`

**`SubAgentChannel`** (點對點 queue)

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
- `get_channel_info() -> list[dict]` — 給 prompt 注入用。

### `Session`、`Scratchpad`、`Environment`

模組：`kohakuterrarium.core.session`、`core.scratchpad`、`core.environment`。

**`Session`**

單隻生物的共享狀態 dataclass。

- `key: str`
- `channels: ChannelRegistry`
- `scratchpad: Scratchpad`
- `tui: Any | None = None`
- `extra: dict[str, Any]`

Module-level 函式：

- `get_session(key=None) -> Session`
- `set_session(session, key=None) -> None`
- `remove_session(key=None) -> None`
- `list_sessions() -> list[str]`
- `get_scratchpad() -> Scratchpad`
- `get_channel_registry() -> ChannelRegistry`

**`Scratchpad`**

Key-value 字串 store。

- `set(key, value) -> None`
- `get(key) -> str | None`
- `delete(key) -> bool`
- `list_keys() -> list[str]`
- `clear() -> None`
- `to_dict() -> dict[str, str]`
- `to_prompt_section() -> str`
- `__len__`、`__contains__`

**`Environment`**

生態瓶的共享執行 context。

- `env_id: str`
- `shared_channels: ChannelRegistry`
- `get_session(key) -> Session` — 生物私有。
- `list_sessions() -> list[str]`
- `register(key, value) -> None`
- `get(key, default=None) -> Any`

### Job

模組：`kohakuterrarium.core.job`。

**`JobType`** enum：`TOOL`、`SUBAGENT`、`COMMAND`。

**`JobState`** enum：`PENDING`、`RUNNING`、`DONE`、`ERROR`、`CANCELLED`。

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

Properties：`duration`、`is_complete`、`is_running`。

方法：`to_context_string() -> str`。

**`JobResult`**

- `job_id: str`
- `output: str = ""`
- `exit_code: int | None = None`
- `error: str | None = None`
- `metadata: dict[str, Any]`
- `success: bool` property。
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

工具：

- `generate_job_id(prefix="job") -> str`

### 終止

模組：`kohakuterrarium.core.termination`。

**`TerminationConfig`**

- `max_turns: int = 0`
- `max_tokens: int = 0` (保留)
- `max_duration: float = 0`
- `idle_timeout: float = 0`
- `keywords: list[str]`

**`TerminationChecker`**

- `start() -> None`
- `record_turn() -> None`
- `record_activity() -> None`
- `should_terminate(last_output: str = "") -> bool`
- `reason`、`turn_count`、`elapsed`、`is_active` properties。

---

## `kohakuterrarium.llm`

### `LLMProvider` (protocol)、`BaseLLMProvider`

模組：`kohakuterrarium.llm.base`。

Async protocol：

- `async chat(messages, *, stream=True, tools=None, **kwargs) -> AsyncIterator[str]`
- `async chat_complete(messages, **kwargs) -> ChatResponse`
- property `last_tool_calls: list[NativeToolCall]`

繼承 `BaseLLMProvider` 來實作：

- `async _stream_chat(messages, *, tools=None, **kwargs)`
- `async _complete_chat(messages, **kwargs) -> ChatResponse`

Base 屬性：`config: LLMConfig`、`last_usage: dict[str, int]`。

### Message 型別

模組：`kohakuterrarium.llm.base` / `kohakuterrarium.llm.message`。

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

- `content`、`finish_reason`、`model: str`
- `usage: dict[str, int]`

**`ToolSchema`**

- `name`、`description: str`
- `parameters: dict[str, Any]`
- `to_api_format() -> dict`

**`NativeToolCall`**

- `id`、`name`、`arguments: str`
- `parsed_arguments() -> dict`

**`Message`**

- `role: Role` (`"system"`、`"user"`、`"assistant"`、`"tool"`)
- `content: str | list[ContentPart]`
- `name`、`tool_call_id: str | None`
- `tool_calls: list[dict] | None`
- `metadata: dict`
- `to_dict() / from_dict(data)`
- `get_text_content() -> str`
- `has_images() -> bool`
- `get_images() -> list[ImagePart]`
- `is_multimodal() -> bool`

子類別 `SystemMessage`、`UserMessage`、`AssistantMessage`、`ToolMessage` 強制 role。

**`TextPart`** — `text: str`、`type: "text"`。

**`ImagePart`** — `url`、`detail ("auto"|"low"|"high")`、`source_type`、`source_name`；`get_description() -> str`。

**`FilePart`** — 對應的檔案參照。

Factory：

- `create_message(role, content, **kwargs) -> Message`
- `make_multimodal_content(text, images=None, prepend_images=False) -> str | list[ContentPart]`
- `normalize_content_parts(content) -> str | list[ContentPart] | None`

別名：`Role`、`MessageContent`、`ContentPart`、`MessageList`。

### Profile

模組：`kohakuterrarium.llm.profiles`、`kohakuterrarium.llm.profile_types`。

**`LLMBackend`** — `name`、`backend_type`、`base_url`、`api_key_env`。

**`LLMPreset`** — `name`、`model`、`provider`、`max_context`、`max_output`、`temperature`、`reasoning_effort`、`service_tier`、`extra_body`。

**`LLMProfile`** — preset + backend 的執行期合併結果：`name`、`model`、`provider`、`backend_type`、`max_context`、`max_output`、`base_url`、`api_key_env`、`temperature`、`reasoning_effort`、`service_tier`、`extra_body`。

Module-level 函式：

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

內建 provider 名稱：`codex`、`openai`、`openrouter`、`anthropic`、`gemini`、`mimo`。

### API key

模組：`kohakuterrarium.llm.api_keys`。

- `save_api_key(provider, key) -> None`
- `get_api_key(provider_or_env) -> str`
- `list_api_keys() -> dict[str, str]` (遮罩過)。
- `KT_DIR: Path`
- `KEYS_PATH: Path`
- `PROVIDER_KEY_MAP: dict[str, str]`

---

## `kohakuterrarium.session`

### `SessionStore`

模組：`kohakuterrarium.session.store`。底層 SQLite (KohakuVault)。

資料表：`meta`、`state`、`events`、`channels`、`subagents`、`jobs`、`conversation`、`fts`。

事件：

- `append_event(agent, event_type, data) -> str`
- `get_events(agent) -> list[dict]`
- `get_resumable_events(agent) -> list[dict]`
- `get_all_events() -> list[tuple[str, dict]]`

對話快照：

- `save_conversation(agent, messages) -> None`
- `load_conversation(agent) -> list[dict] | None`

狀態：

- `save_state(agent, *, scratchpad=None, turn_count=None, token_usage=None, triggers=None, compact_count=None) -> None`
- `load_scratchpad(agent) -> dict[str, str]`
- `load_turn_count(agent) -> int`
- `load_token_usage(agent) -> dict[str, int]`
- `load_triggers(agent) -> list[dict]`

頻道：

- `save_channel_message(channel, data) -> str`
- `get_channel_messages(channel) -> list[dict]`

子代理：

- `next_subagent_run(parent, name) -> int`
- `save_subagent(parent, name, run, meta, conv_json=None) -> None`
- `load_subagent_meta(parent, name, run) -> dict | None`
- `load_subagent_conversation(parent, name, run) -> str | None`

Job：

- `save_job(job_id, data) -> None`
- `load_job(job_id) -> dict | None`

Metadata：

- `init_meta(session_id, config_type, config_path, pwd, agents, config_snapshot=None, terrarium_name=None, terrarium_channels=None, terrarium_creatures=None) -> None`
- `update_status(status) -> None`
- `touch() -> None`
- `load_meta() -> dict[str, Any]`

雜項：

- `search(query, k=10) -> list[dict]` — FTS5 BM25。
- `flush() -> None`
- `close(update_status=True) -> None`
- `path: str` property。

### `SessionMemory`

模組：`kohakuterrarium.session.memory`。

索引後搜尋 (FTS + 向量 + hybrid)。

- `index_events(agent) -> None`
- `async search(query, mode="hybrid", k=5) -> list[SearchResult]`

**`SearchResult`**

- `content: str`
- `round_num`、`block_num: int`
- `agent: str`
- `block_type: str` — `"text"`、`"tool"`、`"trigger"`、`"user"`。
- `score: float`
- `ts: float`
- `tool_name`、`channel: str`

### Embedding provider

模組：`kohakuterrarium.session.embedding`。

Provider 類型：`model2vec`、`sentence-transformer`、`api`。API provider 含 `GeminiEmbedder`。別名：`@tiny`、`@base`、`@retrieval`、`@best`、`@multilingual`、`@multilingual-best`、`@science`、`@nomic`、`@gemma`。

---

## `kohakuterrarium.terrarium`

### `TerrariumRuntime`

模組：`kohakuterrarium.terrarium.runtime`。多代理 orchestrator；繼承 `HotPlugMixin`。

生命週期：

- `async start() -> None`
- `async stop() -> None`
- `async run() -> None`

熱插拔：

- `async add_creature(name, creature: Agent, ...) -> CreatureHandle`
- `async remove_creature(name) -> bool`
- `async add_channel(name, channel_type) -> None`
- `async wire_channel(creature_name, channel_name, direction) -> None`

Properties：`api: TerrariumAPI`、`observer: ChannelObserver`。

Attribute：`config: TerrariumConfig`、`environment: Environment`、`_creatures: dict[str, CreatureHandle]`。

### `TerrariumConfig`、`CreatureConfig`、`ChannelConfig`、`RootConfig`

模組：`kohakuterrarium.terrarium.config`。Dataclass。

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

函式：

- `load_terrarium_config(config_path: str) -> TerrariumConfig`
- `build_channel_topology_prompt(config, creature) -> str`

### `TerrariumAPI`、`ChannelObserver`、`CreatureHandle`

程式化控制介面。`TerrariumAPI` 對映 root 代理可用的生態瓶工具。`ChannelObserver` 提供非破壞性觀察。`CreatureHandle` 把一隻 `Agent` 加上它的生態瓶接線包起來。

---

## `kohakuterrarium.serving`

### `KohakuManager`

模組：`kohakuterrarium.serving.manager`。與 transport 無關的 manager；HTTP API 與任何自訂 transport 都用它。

Agent 方法：

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

Terrarium 方法：

- `async terrarium_create(config_path, ...) -> str`
- `async terrarium_stop(terrarium_id) -> None`
- `async terrarium_run(terrarium_id) -> AsyncIterator[str]`
- 另外有 creature / channel / observer 操作，對映 HTTP 介面。

### `AgentSession`

模組：`kohakuterrarium.serving.agent_session`。`Agent` 的薄包裝，支援併發輸入注入與輸出串流。

Factory：

- `async from_path(config_path, llm_override=None, pwd=None) -> AgentSession`
- `async from_config(config: AgentConfig) -> AgentSession`
- `async from_agent(agent: Agent) -> AgentSession`

方法：

- `async start() / async stop()`
- `async chat(message: str | list[dict]) -> AsyncIterator[str]`
- `get_status() -> dict`

Attribute：`agent_id: str`、`agent: Agent`。

---

## 模組協定 (extension API)

### `Tool`

模組：`kohakuterrarium.modules.tool.base`。

Protocol / `BaseTool` 基底類別。

- `async execute(args: dict, context: ToolContext | None = None) -> ToolResult` — 必要。
- `needs_context: bool = False`
- `parallel_allowed: bool = True`
- `timeout: float = 60.0`
- `max_output: int = 0`

### `InputModule`

模組：`kohakuterrarium.modules.input.base`。`BaseInputModule` 提供 user command 派發。

- `async start() / async stop()`
- `async get_input() -> TriggerEvent | None`

### `OutputModule`

模組：`kohakuterrarium.modules.output.base`。`BaseOutputModule` 基底類別。

- `async start() / async stop()`
- `async write(content: str) -> None`
- `async write_stream(chunk: str) -> None`
- `async flush() -> None`
- `async on_processing_start() / async on_processing_end()`
- `on_activity(activity_type: str, detail: str) -> None`
- `async on_user_input(text: str) -> None` (選用)
- `async on_resume(events: list[dict]) -> None` (選用)

Activity 類型：`tool_start`、`tool_done`、`tool_error`、`subagent_start`、`subagent_done`、`subagent_error`。

### `BaseTrigger`

模組：`kohakuterrarium.modules.trigger.base`。

- `async wait_for_trigger() -> TriggerEvent | None` — 必要。
- `async _on_start() / async _on_stop()` — 選用。
- `_on_context_update(context: dict) -> None` — 選用。
- `resumable: bool = False`
- `universal: bool = False`
- `to_resume_dict() -> dict` / `from_resume_dict(data) -> BaseTrigger`
- `__init__(prompt: str | None = None, **options)`

### `SubAgent`

模組：`kohakuterrarium.modules.subagent.base`。

- `async run(input_text: str) -> SubAgentResult`
- `async cancel() -> None`
- `get_status() -> SubAgentJob`
- `get_pending_count() -> int`

Attribute：`config: SubAgentConfig`、`llm`、`registry`、`executor`、`conversation`。

`kohakuterrarium.modules.subagent` 底下的支援類別：`SubAgentResult`、`SubAgentJob`、`SubAgentManager`、`InteractiveSubAgent`、`InteractiveManagerMixin`、`SubAgentConfig`。

### 外掛 hook

模組：`kohakuterrarium.modules.plugin`。每一個 hook、簽名、時機請看 [plugin-hooks.md](plugin-hooks.md)。

---

## `kohakuterrarium.compose`

組合代理與純函式的管線代數。

### `BaseRunnable`

- `async run(input) -> Any`
- `async __call__(input) -> Any`
- `__rshift__(other)` — `>>` sequence。
- `__and__(other)` — `&` parallel。
- `__or__(other)` — `|` fallback。
- `__mul__(n)` — `*` retry。
- `iterate(initial_input) -> PipelineIterator`
- `map(fn) -> BaseRunnable` — 輸出後變換。
- `contramap(fn) -> BaseRunnable` — 輸入前變換。
- `fails_when(predicate) -> BaseRunnable`

### Factory

模組：`kohakuterrarium.compose.core`。

- `Pure(fn)` / `pure(fn)` — 包 sync 或 async callable。
- `Sequence(*stages)` — 串接。
- `Product(*stages)` — 平行 (`asyncio.gather`)。
- `Fallback(*stages)`
- `Retry(stage, attempts)`
- `Router(mapping)` — dict 派發。
- `Iterator(...)` — 對 async 來源做 iteration。
- `effects.Effects()` — 副作用紀錄 handle。

### 代理組合

模組：`kohakuterrarium.compose.agent`。

- `async agent(config_path: str) -> AgentRunnable` — 持久代理，跨呼叫重用 (async context manager)。
- `factory(config: AgentConfig) -> AgentRunnable` — 臨時 factory；每次呼叫都生新代理。

運算子優先順序：`* > | > & > >>`。

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

模組：`kohakuterrarium.testing.agent`。供決定性代理測試用的 fluent builder。

Builder 方法 (回傳 `self`)：

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

`TestAgentEnv`：

- Properties：`llm: ScriptedLLM`、`output: OutputRecorder`、`session: Session`。
- 方法：`async inject(content)`、`async chat(content) -> str`。

### `ScriptedLLM`

模組：`kohakuterrarium.testing.llm`。

建構子：`ScriptedLLM(script: list[ScriptEntry] | list[str] | None = None)`。

**`ScriptEntry`**：`response: str`、`match: str | None = None`、`delay_per_chunk: float = 0`、`chunk_size: int = 10`。

方法：`async chat`、`async chat_complete`。

Assert 介面：`call_count: int`、`call_log: list[list[dict]]`。

### `OutputRecorder`

模組：`kohakuterrarium.testing.output`。

- `all_text: str`
- `chunks: list[str]`
- `writes: list[str]`
- `activities: list[tuple[str, str]]`

### `EventRecorder`

模組：`kohakuterrarium.testing.events`。

- `record(event) -> None`
- `get_all() -> list[TriggerEvent]`
- `get_by_type(event_type) -> list[TriggerEvent]`
- `clear() -> None`

---

## 套件

模組：`kohakuterrarium.packages`。

- `is_package_ref(path: str) -> bool`
- `resolve_package_path(ref: str) -> Path`
- `list_packages() -> list[str]`
- `install_package(source, name=None, editable=False) -> None`
- `uninstall_package(name) -> bool`

套件根目錄：`~/.kohakuterrarium/packages/`。Editable 安裝用 `<name>.link` 指標取代複製。

---

## 延伸閱讀

- 概念：[組合一個 agent](../concepts/foundations/composing-an-agent.md)、[modules/tool](../concepts/modules/tool.md)、[modules/sub-agent](../concepts/modules/sub-agent.md)、[impl-notes/session-persistence](../concepts/impl-notes/session-persistence.md)。
- 指南：[程式化使用](../guides/programmatic-usage.md)、[自訂模組](../guides/custom-modules.md)、[外掛](../guides/plugins.md)。
- 參考：[CLI](cli.md)、[HTTP](http.md)、[設定](configuration.md)、[內建模組](builtins.md)、[外掛 hook](plugin-hooks.md)。
