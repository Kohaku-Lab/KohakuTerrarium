---
title: Python API
summary: kohakuterrarium 套件接口 — Terrarium 引擎、Creature、Agent、compose、测试辅助工具。
tags:
  - reference
  - python
  - api
---

# Python API

`kohakuterrarium` 套件里所有公开的类别、函式、协定。条目依模组分组。签名用现代 type hint。

架构请参见 [核心概念](../concepts/README.md)。任务阅读路径请参见 [编程方式使用指南](../guides/programmatic-usage.md) 和 [自定义模块指南](../guides/custom-modules.md)。

## Import 接口

| 想做什么 | 用这个 |
|---|---|
| 运行时引擎 (独立或多 Creature) | `kohakuterrarium.Terrarium` |
| 一只运行中 Creature 的 handle | `kohakuterrarium.Creature` |
| 引擎事件 | `kohakuterrarium.{EngineEvent, EventKind, EventFilter}` |
| 拓扑结果 | `kohakuterrarium.{ConnectionResult, DisconnectionResult}` |
| 直接控制代理 | `kohakuterrarium.core.agent.Agent` |
| 流式聊天 (旧版 wrapper) | `kohakuterrarium.serving.agent_session.AgentSession` |
| 加载 config | `kohakuterrarium.core.config.load_agent_config` / `kohakuterrarium.terrarium.config.load_terrarium_config` |
| 持久化 / 搜索 | `kohakuterrarium.session.store.SessionStore`、`kohakuterrarium.session.memory.SessionMemory` |
| 写 extension | `kohakuterrarium.modules.{tool,input,output,trigger,subagent}.base` |
| 组管线 | `kohakuterrarium.compose` |
| 写测试 | `kohakuterrarium.testing` |

旧版 `kohakuterrarium.terrarium.runtime.TerrariumRuntime` 与 `kohakuterrarium.serving.manager.KohakuManager` 在过渡期间仍存在，文档放在下面。

---

## 顶层套件 re-export

新的公开介面直接从 `kohakuterrarium` re-export：

```python
from kohakuterrarium import (
    Terrarium,
    Creature,
    EngineEvent,
    EventKind,
    EventFilter,
    ConnectionResult,
    DisconnectionResult,
)
```

### `Terrarium`

模块：`kohakuterrarium.terrarium.engine`。运行时引擎 — 每进程一个。托管所有运行中 Creature，拥有 graph 级状态。

Classmethod factory：

- `async Terrarium.with_creature(config, *, pwd=None) -> tuple[Terrarium, Creature]` — 引擎 + 一只 Creature。
- `async Terrarium.from_recipe(recipe, *, pwd=None) -> Terrarium` — 引擎并套用一份 recipe。`recipe` 可以是 `TerrariumConfig` 或 YAML 路径。
- `async Terrarium.resume(store, *, recipe=None) -> Terrarium` — **尚未实现**；会抛 `NotImplementedError`。

构造函式：

- `Terrarium(*, pwd=None, session_dir=None)` — 空引擎。用 `add_creature` / `apply_recipe` 填它。

Async context manager：

- `async with Terrarium() as engine: ...` — `__aexit__` 调用 `shutdown()`。

Creature CRUD：

- `async add_creature(config, *, graph=None, creature_id=None, llm_override=None, pwd=None, start=True) -> Creature`
- `async remove_creature(creature) -> None`
- `get_creature(creature_id) -> Creature`
- `list_creatures() -> list[Creature]`
- Pythonic accessor：`engine[id]`、`id in engine`、`for c in engine`、`len(engine)`。

Channel CRUD：

- `async add_channel(graph, name, description="") -> ChannelInfo` —— 所有图频道都是广播。
- `async connect(sender, receiver, *, channel=None) -> ConnectionResult` —— 跨图 connect 会合并图（environment 取联集、session store 合并）。
- `async disconnect(sender, receiver, *, channel=None) -> DisconnectionResult` — 可能拆 graph (parent session 复制到两边)。

输出接线：

- `async wire_output(creature, sink: OutputModule) -> str`
- `async unwire_output(creature, sink_id: str) -> bool`

Graphs：

- `get_graph(graph_id) -> GraphTopology`
- `list_graphs() -> list[GraphTopology]`

Recipe：

- `async apply_recipe(recipe, *, graph=None, pwd=None, creature_builder=None) -> GraphTopology`

生命周期：

- `async start(creature) / async stop(creature)`
- `async stop_graph(graph) -> None`
- `async shutdown() -> None` — 幂等。

可观测性：

- `async subscribe(filter=None) -> AsyncIterator[EngineEvent]`
- `status() -> dict` (整体) / `status(creature) -> dict`

Session：

- `async attach_session(graph, store: SessionStore) -> None`

接受 `CreatureRef` / `GraphRef` 的地方都可以传物件本身或字符串 ID。

### `Creature`

模块：`kohakuterrarium.terrarium.creature_host`。一只运行中的 Creature handle。由 `add_creature` / `with_creature` 返回；不要直接构造。

属性：

- `creature_id: str`、`name: str`、`graph_id: str`
- `agent: Agent` — 背后的 LLM 控制器。
- `listen_channels: list[str]`、`send_channels: list[str]`
- `is_running: bool`

方法：

- `async start() / async stop()` — 幂等。
- `async inject_input(message, *, source="chat") -> None`
- `async chat(message) -> AsyncIterator[str]` — 推输入并串流文字回应。
- `get_status() -> dict` — model、max_context、compact_threshold、provider、session_id、tools、subagents、pwd、graph_id、listen / send 频道。
- `get_log_entries(last_n=20) -> list[LogEntry]`
- `get_log_text(last_n=10) -> str`

### `EngineEvent`、`EventKind`、`EventFilter`

模块：`kohakuterrarium.terrarium.events`。

引擎里所有可观察的事都以 `EngineEvent` 形式浮现。

**`EventKind`** (`str` 值的 enum)：

- `TEXT`、`ACTIVITY`、`CHANNEL_MESSAGE`
- `TOPOLOGY_CHANGED`、`SESSION_FORKED`
- `CREATURE_STARTED`、`CREATURE_STOPPED`
- `PROCESSING_START`、`PROCESSING_END`
- `ERROR`

**`EngineEvent`** dataclass：

- `kind: EventKind`
- `creature_id: str | None`
- `graph_id: str | None`
- `channel: str | None`
- `payload: dict`
- `ts: float`

**`EventFilter`** dataclass：

- `kinds: set[EventKind] | None`
- `creature_ids: set[str] | None`
- `graph_ids: set[str] | None`
- `channels: set[str] | None`
- `matches(ev: EngineEvent) -> bool`

栏位 AND 组合；`None` 表示「任意」。传 `EventFilter()` 或省略表示收全部。

### `ConnectionResult`、`DisconnectionResult`

模块：`kohakuterrarium.terrarium.events`。

**`ConnectionResult`** — `Terrarium.connect` 返回：

- `channel: str` — 频道名 (需要时建立)。
- `trigger_id: str` — 接收端注入的 `ChannelTrigger` id。
- `delta_kind: str` — `"nothing"` 或 `"merge"`。

**`DisconnectionResult`** — `Terrarium.disconnect` 返回：

- `channels: list[str]` — 被断开的频道。
- `delta_kind: str` — `"nothing"` 或 `"split"`。

### `GraphTopology`、`ChannelInfo`

模块：`kohakuterrarium.terrarium.topology`。纯数据拓扑模型 — 没有 live agent 引用。

**`GraphTopology`** — 一个连通分量：

- `graph_id: str`
- `creature_ids: set[str]`
- `channels: dict[str, ChannelInfo]`
- `listen_edges: dict[str, set[str]]`
- `send_edges: dict[str, set[str]]`
- `has_creature(creature_id) -> bool`
- `has_channel(name) -> bool`

**`ChannelInfo`** — `name, description`。所有图频道都是广播 —— 拓扑层不存在 kind 选择。

### Compose 协作

`Creature` 设计上要满足 `kohakuterrarium.compose.core` 的 `Runnable` 协定，让 Creature 可以用 `>>` / `&` / `|` / `*` 接进 pipeline。这条组合路径的整合测试还需要补。

---

## `kohakuterrarium.core`

### `Agent`

模块：`kohakuterrarium.core.agent`。

主 orchestrator：把 LLM、controller、executor、trigger、I/O、插件串起来。继承 `AgentInitMixin`、`AgentHandlersMixin`、`AgentMessagesMixin`。

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

生命周期：

- `async start() -> None` — 启动 I/O、输出、trigger、LLM、插件。
- `async stop() -> None` — 乾净地停下所有模组。
- `async run() -> None` — 完整事件回圈。若尚未 start 会先调用 `start()`。
- `interrupt() -> None` — 非阻塞；任何 thread 调用都安全。

输入与事件：

- `async inject_input(content: str | list[ContentPart], source: str = "programmatic") -> None`
- `async inject_event(event: TriggerEvent) -> None`

执行期控制：

- `switch_model(profile_name: str) -> str` — 返回解析后的 model id。
- `async add_trigger(trigger: BaseTrigger, trigger_id: str | None = None) -> str`
- `async remove_trigger(trigger_id_or_trigger: str | BaseTrigger) -> bool`
- `update_system_prompt(content: str, replace: bool = False) -> None`
- `get_system_prompt() -> str`
- `attach_session_store(store: Any) -> None`
- `set_output_handler(handler: Any, replace_default: bool = False) -> None`
- `get_state() -> dict[str, Any]` — name、running、tools、subagents、message count、pending jobs。

属性：

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

补充：

- `environment` 在多代理时由 `TerrariumRuntime` 提供；独立代理时为 `None`。
- `Agent` 实例 `stop()` 之后不能重用；要从 `SessionStore` 接回来，请建新的。

```python
agent = Agent.from_path("creatures/my_agent", llm_override="claude-opus-4.7")
await agent.start()
await agent.inject_input("Hello")
await agent.stop()
```

### `AgentConfig`

模块：`kohakuterrarium.core.config_types`。Dataclass。

Creature配置的每一个字段。YAML 形式见 [configuration.md](configuration.md)。

字段：

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

- `get_api_key() -> str | None` — 读对应的环境变数。

### `InputConfig`、`OutputConfig`、`OutputConfigItem`、`TriggerConfig`、`ToolConfigItem`、`SubAgentConfigItem`

模块：`kohakuterrarium.core.config_types`。Dataclass。

 **`InputConfig`**

- `type: str = "cli"` — 输入模块类型（`cli`、`cli_nonblocking`、`tui`、`none`、`custom`、`package`）。
- `module: str | None = None`
- `class_name: str | None = None` — 由 YAML 的 `class` 键填入。
- `prompt: str = "> "`
- `options: dict[str, Any]`

 **`TriggerConfig`**

- `type: str` — 内建类型是 `timer`、`context`、`channel`；custom/package trigger 用 `module` + YAML `class`。
- `module, class_name: str | None`
- `prompt: str | None = None`
- `options: dict[str, Any]`
- `name: str | None = None`

 **`ToolConfigItem`**

- `name: str`
- `type: str = "builtin"` — `builtin`、`trigger`、`custom`、或 `package`。
- `module, class_name: str | None`
- `doc: str | None = None` — 覆盖 skill doc 路径。
- `options: dict[str, Any]`

 **`OutputConfigItem`**

- `type: str = "stdout"`
- `module, class_name: str | None`
- `options: dict[str, Any]`

 **`OutputConfig`**

继承 `OutputConfigItem` 加上：

- `controller_direct: bool = True`
- `named_outputs: dict[str, OutputConfigItem]`

 **`SubAgentConfigItem`**

- `name: str`
- `type: str = "builtin"`
- `module, class_name, config_name, description: str | None` — `class_name` / `config_name` 分别由 YAML 的 `class` / `config` 键填入。
- `tools: list[str]`
- `can_modify: bool = False`
- `interactive: bool = False`
- `options: dict[str, Any]`

`subagents:` YAML 条目可以是裸字符串（`"explore"` → 内置 `SubAgentConfigItem(name="explore")`）或 dict。`type: custom` 且没有 `module`/`config` 的 dict 条目会被当作内联 `SubAgentConfig` 数据；额外字段会通过 `options` 转发，并由 `SubAgentConfig.from_dict` 解析。

### `load_agent_config`

模块：`kohakuterrarium.core.config`。

```python
load_agent_config(config_path: str) -> AgentConfig
```

解析 YAML/JSON/TOML (`config.yaml` → `.yml` → `.json` → `.toml`)、套 `base_config` 继承、环境变数插值、路径解析。

### `Conversation`、`ConversationConfig`、`ConversationMetadata`

模块：`kohakuterrarium.core.conversation`。

Conversation 管消息历程与 OpenAI 格式序列化。

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

模块：`kohakuterrarium.core.events`。

在输入、trigger、工具、子代理之间流的通用事件。

字段：

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
- `with_context(**kwargs) -> TriggerEvent` — 不会 mutate 原对象。

`EventType` 常数：`USER_INPUT`、`IDLE`、`TIMER`、`CONTEXT_UPDATE`、`TOOL_COMPLETE`、`SUBAGENT_OUTPUT`、`CHANNEL_MESSAGE`、`MONITOR`、`ERROR`、`STARTUP`、`SHUTDOWN`。

Factory：

- `create_user_input_event(content, source="cli", **extra_context) -> TriggerEvent`
- `create_tool_complete_event(job_id, content, exit_code=None, error=None, **extra_context) -> TriggerEvent`
- `create_error_event(error_type, message, job_id=None, **extra_context) -> TriggerEvent` (`stackable=False`)。

### Channel

模块：`kohakuterrarium.core.channel`。

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

 **`SubAgentChannel`** (点对点 queue)

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
- `get_channel_info() -> list[dict]` — 给 prompt 注入用。

### `Session`、`Scratchpad`、`Environment`

模块：`kohakuterrarium.core.session`、`core.scratchpad`、`core.environment`。

 **`Session`**

单只Creature的共享状态 dataclass。

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

Key-value 字符串 store。

- `set(key, value) -> None`
- `get(key) -> str | None`
- `delete(key) -> bool`
- `list_keys() -> list[str]`
- `clear() -> None`
- `to_dict() -> dict[str, str]`
- `to_prompt_section() -> str`
- `__len__`、`__contains__`

 **`Environment`**

Terrarium的共享执行 context。

- `env_id: str`
- `shared_channels: ChannelRegistry`
- `get_session(key) -> Session` — Creature私有。
- `list_sessions() -> list[str]`
- `register(key, value) -> None`
- `get(key, default=None) -> Any`

### Job

模块：`kohakuterrarium.core.job`。

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

### 终止

模块：`kohakuterrarium.core.termination`。

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

模块：`kohakuterrarium.llm.base`。

Async protocol：

- `async chat(messages, *, stream=True, tools=None, **kwargs) -> AsyncIterator[str]`
- `async chat_complete(messages, **kwargs) -> ChatResponse`
- property `last_tool_calls: list[NativeToolCall]`

继承 `BaseLLMProvider` 来实作：

- `async _stream_chat(messages, *, tools=None, **kwargs)`
- `async _complete_chat(messages, **kwargs) -> ChatResponse`

Base 属性：`config: LLMConfig`、`last_usage: dict[str, int]`。

### Message 型别

模块：`kohakuterrarium.llm.base` / `kohakuterrarium.llm.message`。

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

子类别 `SystemMessage`、`UserMessage`、`AssistantMessage`、`ToolMessage` 强制 role。

 **`TextPart`** — `text: str`、`type: "text"`。

 **`ImagePart`** — `url`、`detail ("auto"|"low"|"high")`、`source_type`、`source_name`；`get_description() -> str`。

 **`FilePart`** — 对应的文件参照。

Factory：

- `create_message(role, content, **kwargs) -> Message`
- `make_multimodal_content(text, images=None, prepend_images=False) -> str | list[ContentPart]`
- `normalize_content_parts(content) -> str | list[ContentPart] | None`

别名：`Role`、`MessageContent`、`ContentPart`、`MessageList`。

### Profile

模块：`kohakuterrarium.llm.profiles`、`kohakuterrarium.llm.profile_types`。

 **`LLMBackend`** — `name`、`backend_type`、`base_url`、`api_key_env`。

 **`LLMPreset`** — `name`、`model`、`provider`、`max_context`、`max_output`、`temperature`、`reasoning_effort`、`service_tier`、`extra_body`。

 **`LLMProfile`** — preset + backend 的执行期合并结果：`name`、`model`、`provider`、`backend_type`、`max_context`、`max_output`、`base_url`、`api_key_env`、`temperature`、`reasoning_effort`、`service_tier`、`extra_body`。

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

内置 provider 名称：`codex`、`openai`、`openrouter`、`anthropic`、`gemini`、`mimo`。

### API key

模块：`kohakuterrarium.llm.api_keys`。

- `save_api_key(provider, key) -> None`
- `get_api_key(provider_or_env) -> str`
- `list_api_keys() -> dict[str, str]` (遮罩过)。
- `KT_DIR: Path`
- `KEYS_PATH: Path`
- `PROVIDER_KEY_MAP: dict[str, str]`

---

## `kohakuterrarium.session`

### `SessionStore`

模块：`kohakuterrarium.session.store`。底层 SQLite (KohakuVault)。

数据表：`meta`、`state`、`events`、`channels`、`subagents`、`jobs`、`conversation`、`fts`。

事件：

- `append_event(agent, event_type, data) -> str`
- `get_events(agent) -> list[dict]`
- `get_resumable_events(agent) -> list[dict]`
- `get_all_events() -> list[tuple[str, dict]]`

对话快照：

- `save_conversation(agent, messages) -> None`
- `load_conversation(agent) -> list[dict] | None`

状态：

- `save_state(agent, *, scratchpad=None, turn_count=None, token_usage=None, triggers=None, compact_count=None) -> None`
- `load_scratchpad(agent) -> dict[str, str]`
- `load_turn_count(agent) -> int`
- `load_token_usage(agent) -> dict[str, int]`
- `load_triggers(agent) -> list[dict]`

频道：

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

杂项：

- `search(query, k=10) -> list[dict]` — FTS5 BM25。
- `flush() -> None`
- `close(update_status=True) -> None`
- `path: str` property。

### `SessionMemory`

模块：`kohakuterrarium.session.memory`。

索引后搜索 (FTS + 向量 + hybrid)。

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

模块：`kohakuterrarium.session.embedding`。

Provider 类型：`model2vec`、`sentence-transformer`、`api`。API provider 含 `GeminiEmbedder`。别名：`@tiny`、`@base`、`@retrieval`、`@best`、`@multilingual`、`@multilingual-best`、`@science`、`@nomic`、`@gemma`。

---

## `kohakuterrarium.terrarium` (旧版执行期)

下面这些 类别 在统一的 `Terrarium` 引擎之前 就 存在 ， 在 过渡期间 还在 硬盘上 ； 新代码请用上面 的 `Terrarium` 。

### `TerrariumRuntime` (旧版)

模块：`kohakuterrarium.terrarium.runtime`。多代理 orchestrator；继承 `HotPlugMixin`。

生命周期：

- `async start() -> None`
- `async stop() -> None`
- `async run() -> None`

热插拔：

- `async add_creature(name, creature: Agent, ...) -> CreatureHandle`
- `async remove_creature(name) -> bool`
- `async add_channel(name, channel_type) -> None`
- `async wire_channel(creature_name, channel_name, direction) -> None`

Properties：`api: TerrariumAPI`、`observer: ChannelObserver`。

Attribute：`config: TerrariumConfig`、`environment: Environment`、`_creatures: dict[str, CreatureHandle]`。

### `TerrariumConfig`、`CreatureConfig`、`ChannelConfig`、`RootConfig`

模块：`kohakuterrarium.terrarium.config`。Dataclass。

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

程式化控制接口。`TerrariumAPI` 对映特权节点透过组工具暴露的图操作（`group_add_node`、`group_remove_node`、`group_channel`、`group_wire` 等）。`ChannelObserver` 提供非破坏性观察。`CreatureHandle` 把一只 `Agent` 加上它的 Terrarium 接线包起来。

---

## `kohakuterrarium.serving`

### `KohakuManager` (旧版)

模块：`kohakuterrarium.serving.manager`。在 `Terrarium` 引擎 之前 的 transport-agnostic manager 。 较旧 的 HTTP route 在 过渡期间 还会 用 ； 新代码请用 `Terrarium` 。

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
- 另外有 creature / channel / observer 操作，对映 HTTP 接口。

### `AgentSession`

模块：`kohakuterrarium.serving.agent_session`。`Agent` 的薄包装，支持并发输入注入与输出流式。

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

## 模组协定 (extension API)

### `Tool`

模块：`kohakuterrarium.modules.tool.base`。

Protocol / `BaseTool` 基底类别。

- `async execute(args: dict, context: ToolContext | None = None) -> ToolResult` — 必要。
- `needs_context: bool = False`
- `parallel_allowed: bool = True`
- `timeout: float = 60.0`
- `max_output: int = 0`

### `InputModule`

模块：`kohakuterrarium.modules.input.base`。`BaseInputModule` 提供 user command 派发。

- `async start() / async stop()`
- `async get_input() -> TriggerEvent | None`

### `OutputModule`

模块：`kohakuterrarium.modules.output.base`。`BaseOutputModule` 基底类别。

- `async start() / async stop()`
- `async write(content: str) -> None`
- `async write_stream(chunk: str) -> None`
- `async flush() -> None`
- `async on_processing_start() / async on_processing_end()`
- `on_activity(activity_type: str, detail: str) -> None`
- `async on_user_input(text: str) -> None` (选用)
- `async on_resume(events: list[dict]) -> None` (选用)

Activity 类型：`tool_start`、`tool_done`、`tool_error`、`subagent_start`、`subagent_done`、`subagent_error`。

### `BaseTrigger`

模块：`kohakuterrarium.modules.trigger.base`。

- `async wait_for_trigger() -> TriggerEvent | None` — 必要。
- `async _on_start() / async _on_stop()` — 选用。
- `_on_context_update(context: dict) -> None` — 选用。
- `resumable: bool = False`
- `universal: bool = False`
- `to_resume_dict() -> dict` / `from_resume_dict(data) -> BaseTrigger`
- `__init__(prompt: str | None = None, **options)`

### `SubAgent`

模块：`kohakuterrarium.modules.subagent.base`。

- `async run(input_text: str) -> SubAgentResult`
- `async cancel() -> None`
- `get_status() -> SubAgentJob`
- `get_pending_count() -> int`

Attribute：`config: SubAgentConfig`、`llm`、`registry`、`executor`、`conversation`。

`kohakuterrarium.modules.subagent` 下面的支持类别：`SubAgentResult`、`SubAgentJob`、`SubAgentManager`、`InteractiveSubAgent`、`InteractiveManagerMixin`、`SubAgentConfig`。

### 插件 Hook

模块：`kohakuterrarium.modules.plugin`。每个 Hook 的签名和触发时机请参见 [插件 Hook 参考](plugin-hooks.md)。

---

## `kohakuterrarium.compose`

组合代理与纯函式的管线代数。

### `BaseRunnable`

- `async run(input) -> Any`
- `async __call__(input) -> Any`
- `__rshift__(other)` — `>>` sequence。
- `__and__(other)` — `&` parallel。
- `__or__(other)` — `|` fallback。
- `__mul__(n)` — `*` retry。
- `iterate(initial_input) -> PipelineIterator`
- `map(fn) -> BaseRunnable` — 输出后变换。
- `contramap(fn) -> BaseRunnable` — 输入前变换。
- `fails_when(predicate) -> BaseRunnable`

### Factory

模块：`kohakuterrarium.compose.core`。

- `Pure(fn)` / `pure(fn)` — 包 sync 或 async callable。
- `Sequence(*stages)` — 串接。
- `Product(*stages)` — 平行 (`asyncio.gather`)。
- `Fallback(*stages)`
- `Retry(stage, attempts)`
- `Router(mapping)` — dict 派发。
- `Iterator(...)` — 对 async 来源做 iteration。
- `effects.Effects()` — 副作用纪录 handle。

### 代理组合

模块：`kohakuterrarium.compose.agent`。

- `async agent(config_path: str) -> AgentRunnable` — 持久代理，跨调用重用 (async context manager)。
- `factory(config: AgentConfig) -> AgentRunnable` — 临时 factory；每次调用都生新代理。

运算子优先顺序：`* > | > & > >>`。

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

模块：`kohakuterrarium.testing.agent`。供决定性代理测试用的 fluent builder。

Builder 方法 (返回 `self`)：

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

模块：`kohakuterrarium.testing.llm`。

建构子：`ScriptedLLM(script: list[ScriptEntry] | list[str] | None = None)`。

 **`ScriptEntry`** ：`response: str`、`match: str | None = None`、`delay_per_chunk: float = 0`、`chunk_size: int = 10`。

方法：`async chat`、`async chat_complete`。

Assert 接口：`call_count: int`、`call_log: list[list[dict]]`。

### `OutputRecorder`

模块：`kohakuterrarium.testing.output`。

- `all_text: str`
- `chunks: list[str]`
- `writes: list[str]`
- `activities: list[tuple[str, str]]`

### `EventRecorder`

模块：`kohakuterrarium.testing.events`。

- `record(event) -> None`
- `get_all() -> list[TriggerEvent]`
- `get_by_type(event_type) -> list[TriggerEvent]`
- `clear() -> None`

---

## 套件

模块：`kohakuterrarium.packages`。

- `is_package_ref(path: str) -> bool`
- `resolve_package_path(ref: str) -> Path`
- `list_packages() -> list[str]`
- `install_package(source, name=None, editable=False) -> None`
- `uninstall_package(name) -> bool`

套件根目录：`~/.kohakuterrarium/packages/`。Editable 安装用 `<name>.link` 指标替换复制。

---

## 延伸阅读

- 概念：[组合 Agent 概念](../concepts/foundations/composing-an-agent.md)、[工具概念](../concepts/modules/tool.md)、[子代理概念](../concepts/modules/sub-agent.md)、[会话持久化概念](../concepts/impl-notes/session-persistence.md)。
- 指南：[编程方式使用指南](../guides/programmatic-usage.md)、[自定义模块指南](../guides/custom-modules.md)、[插件指南](../guides/plugins.md)。
- 参考：[CLI 参考](cli.md)、[HTTP API 参考](http.md)、[配置参考](configuration.md)、[内置模块参考](builtins.md)、[插件 Hook 参考](plugin-hooks.md)。
