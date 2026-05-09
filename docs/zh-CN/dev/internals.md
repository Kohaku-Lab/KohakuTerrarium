---
title: 框架内部机制
summary: 说明运行时的组装方式：事件队列、控制器循环、执行器、子代理管理与插件封装。
tags:
  - dev
  - internals
---
# 框架内部机制

这篇文档就是运行时地图。建议你一边阅读，一边对照 `src/kohakuterrarium/` 中的代码。`../concepts/` 下的文档解释的是为什么这样设计，这篇只说明内容放在哪里、流程如何运转。公开的 Python API 签名见 `plans/inventory-python-api.md`。

这里一共整理了 16 条流程，分为三组：

1. **Agent runtime** — 生命周期、controller 循环、tool pipeline、sub-agent、trigger、prompt 聚合、plugin。
2. **Persistence & memory** — session 持久化、压缩。
3. **Multi-agent & serving** — terrarium runtime、channel、environment 和 session 的区别、serving 层、compose 代数、package 系统、MCP。

最后还有一节 [跨流程不变量](#跨流程不变量)，专门列出整个系统都必须遵守的硬规则。

---

## 1. Agent runtime

### 1.1 Agent 生命周期（独立 creature）

CLI 入口位于 `cli/run.py:run_agent_cli()`。它会先检查配置路径，再选择 I/O 模式（`cli` / `plain` / TUI），按需创建 `SessionStore`，然后调用 `Agent.from_path(config_path, …)`，最后进入 `_run_agent_rich_cli()` 或 `agent.run()`。

`Agent.__init__`（`src/kohakuterrarium/core/agent.py:146`）会按固定顺序初始化：`_init_llm`、`_init_registry`、`_init_executor`、`_init_subagents`、`_init_output`、`_init_controller`、`_init_input`、`_init_user_commands`、`_init_triggers`。mixin 布局为 `AgentInitMixin`（`bootstrap/agent_init.py`）+ `AgentHandlersMixin`（`core/agent_handlers.py`）+ `AgentToolsMixin`（`core/agent_tools.py`）。

`await agent.start()`（`core/agent.py:186`）会启动 input 和 output 模块；如果存在 TUI，就挂上回调；接着启动 trigger manager、注册 completion callback、初始化 MCP、将 tool 描述注入 prompt、初始化 `CompactManager`、加载 plugin、发布 session 信息，最后启动 termination checker。

`await agent.run()`（`core/agent.py:684`）在恢复 session 时，会先重放 session event、恢复 triggers、触发 startup trigger，然后进入主循环：
`event = await input.get_input()` → `_process_event(event)`。`stop()` 会按相反顺序拆除这些组件。agent 会一直持有这些对象：`llm`、`registry`、`executor`、`session`、`environment`、`subagent_manager`、`output_router`、`controller`、`input`、`trigger_manager`、`compact_manager`、`plugins`。

概念层说明见 [concepts/foundations/composing-an-agent.md](../concepts/foundations/composing-an-agent.md)。API 签名见 `plans/inventory-python-api.md` 中的 §Core Agent Lifecycle。

### 1.2 Controller 循环和事件模型

所有内容最终都会转成 `TriggerEvent`（`core/events.py`）。字段包括：
`type, content, context, timestamp, job_id?, prompt_override?, stackable`。
类型包括 `user_input`、`idle`、`timer`、`context_update`、`tool_complete`、`subagent_output`、`channel_message`、`monitor`、`error`、`startup`、`shutdown`。

事件队列位于 `core/controller.py:push_event` / `_collect_events`（252-299 行）。同一个 tick 中收集到的 stackable event，会合并为这一轮中的同一条 user message；非 stackable event 会直接打断当前 batch；超出当前 batch 的事件会先放入 `_pending_events`，下一轮再处理。

每一轮的流程位于 `agent_handlers.py:_run_controller_loop`：

1. 收集事件，拼出这一轮的上下文。
2. 构造 messages，然后开始从 LLM 流式读取。
3. 一边读取，一边解析 tool / sub-agent / command event。
4. 每解析到一个事件，就立刻用 `asyncio.create_task` 分发出去。也就是说，tool 会在流式输出过程中启动，而不是等 LLM 全部输出完再统一执行。
5. 流结束后，对 direct 模式的完成项执行 `asyncio.gather`。
6. 推入合并后的反馈事件，再决定是否继续下一轮。

相关文档见 [concepts/modules/controller.md](../concepts/modules/controller.md) 和 [stream-parser 实现说明](../concepts/impl-notes/stream-parser.md)。

### 1.3 Tool 执行流水线

stream parser（`parsing/`）在识别到配置好的 `tool_format` 中的 tool block 时，会发出事件。支持 bracket（默认：`[/bash]@@command=ls\n[bash/]`）、XML（`<bash command="ls"></bash>`）和 native（LLM provider 自带的 function-calling 封装）。每个识别出的 tool，都会通过 `executor.submit_from_event()` 转成一个 executor task。

executor（`core/executor.py`）会维护 `{job_id: asyncio.Task}`，每次调用都会创建一个 `ToolContext`。其中包含 `working_dir`、`session`、`environment`、文件保护设置、文件读取状态 map、job store，以及 agent 名称。

共有三种模式：

- **Direct** — 当前轮就等待它执行完，结果会并入下一次 controller feedback event。
- **Background** — 如果 tool 结果中包含 `run_in_background=true`，任务就会继续在后台运行；完成后再发出后续的 `tool_complete` event。
- **Stateful** — 例如 sub-agent 这种长生命周期 handle。结果会存入 `jobs`，之后通过框架命令 `wait` 取回。

这里有几条硬规则，`agent_handlers.py` 和 `executor.py` 都在保证：

- tool block 一旦识别出来，就必须立即启动，不能等 LLM 停下后再统一排队。
- 同一轮中的多个 tool 必须并行执行，依靠 `asyncio.gather`。
- tool 执行不能阻塞 LLM 的流式输出。

更多内容见 [concepts/modules/tool.md](../concepts/modules/tool.md) 和 [impl-notes/stream-parser.md](../concepts/impl-notes/stream-parser.md)。API 参考见 `plans/inventory-python-api.md` 中的 §Tool Execution。

### 1.4 Sub-agent 分发

Sub-agent 由 `modules/subagent/manager.py:spawn` 启动。深度受 `config.max_subagent_depth` 限制。新的 `SubAgent`（`modules/subagent/base.py`）会复用父级的 registry、LLM 和 tool format，但拥有独立对话。

执行完成后，它会将一个 `subagent_output` event 推回父 controller。如果该 sub-agent 配置了 `output_to: external`，输出就会直接流向某个具名 output module，而不再返回父级。

交互式 sub-agent（`modules/subagent/interactive.py` + `interactive_mgr.py`）会跨多轮持续存在，能够接收 `context_update`，也能通过 `_feed_interactive()` 接收新的 prompt。它们和顶层对话一样，也会持久化到 session store。

相关文档见 [concepts/modules/sub-agent.md](../concepts/modules/sub-agent.md)。

### 1.5 Trigger 系统

`modules/trigger/base.py` 定义了 `BaseTrigger`：一个会 yield `TriggerEvent` 的 async generator。`to_resume_dict()` / `from_resume_dict()` 负责持久化。

内建 trigger 包括 `TimerTrigger`、`IdleTrigger`、`ChannelTrigger`、`HTTPTrigger`，以及 monitor 相关 trigger。`TriggerManager`（`core/trigger_manager.py`）会维护 trigger 字典及其对应的后台 task。启动时，它会为每个 trigger 启动一个 task，不断迭代 `fire()`，并将事件推进 agent 队列。`CallableTriggerTool`（`modules/trigger/callable.py`）会包装通用 trigger 类，使 agent 能在运行时热插 trigger。

恢复 session 时，trigger 状态会根据 session store 中的 `events[agent]:*` 记录重建。

相关文档见 [concepts/modules/trigger.md](../concepts/modules/trigger.md)。

### 1.6 Prompt 聚合

`prompt/aggregator.py:aggregate_system_prompt` 会按以下顺序组装最终的 system prompt：

1. 基础 prompt，也就是 `system.md` 中的 agent personality。它会经过 Jinja2 和 `render_template_safe` 渲染，因此未定义的变量会变成空字符串。
2. Tool 文档。`skill_mode: dynamic` 时这里只放名称和一行说明；`static` 时会插入完整文档。
3. Channel topology 提示，由 `terrarium/config.py:build_channel_topology_prompt` 在 creature 构建时生成。
4. 各种 tool format 对应的框架提示（bracket / xml / native）。
5. Named-output 说明，也就是如何向 `discord`、`tts` 等输出写内容。

这些部分会用双换行拼接。`system.md` 中 **不要自己写** tool 列表、tool call 语法或完整 tool 文档。这些内容要么由框架自动拼接，要么通过框架命令 `info` 按需获取。

相关文档见 [impl-notes/prompt-aggregation.md](../concepts/impl-notes/prompt-aggregation.md)。

### 1.7 Plugin 系统

这里实际上是两套彼此独立的系统。**Prompt plugin**（`prompt/plugins.py`）会在聚合 system prompt 时向其中追加内容，按 priority 排序。内建的有 `ToolList`、`FrameworkHints`、`EnvInfo`、`ProjectInstructions`。**Lifecycle plugin**（`bootstrap/plugins.py`，管理器位于 `modules/plugin/`）会挂到 agent event 上。`PluginManager.notify(hook, **kwargs)` 会等待所有启用 plugin 的匹配方法执行完毕。如果某个 `pre_*` hook 抛出 `PluginBlockError`，当前操作就会被拦截。可用 hook 列表见 builtin inventory。

Package 可以在 `kohaku.yaml` 中声明 plugin；`config.plugins[]` 中列出的 plugin 会在 agent 启动时加载。

相关文档见 [concepts/modules/plugin.md](../concepts/modules/plugin.md)。

---

## 2. Persistence & memory

### 2.1 Session 持久化

Session 都保存在单个 `.kohakutr` 文件中，底层是 KohakuVault（SQLite）。`session/store.py` 中定义的表包括：`meta`、`state`、`events`（append-only）、`channels`（消息历史）、`subagents`（销毁前快照）、`jobs`、`conversation`（每个 agent 的最新快照）、`fts`（全文索引）。

写入会发生在以下时机：

- 每次 tool 调用、文本 chunk、trigger 触发、token usage 发出时（event log）
- 每轮结束时（conversation snapshot）
- scratchpad 写入时
- channel 发送时

恢复逻辑位于 `session/resume.py`：先加载 `meta`，再加载每个 agent 的 conversation snapshot，恢复 scratchpad/state，恢复 trigger，将 event 重放给 output module（用于 scrollback），再把 sub-agent 对话挂回去。无法恢复的状态，例如打开的文件、LLM 连接、TUI、asyncio task，都会按配置重新创建。

`session/memory.py` 和 `session/embedding.py` 提供基于 event log 的 FTS5 和向量搜索。embedding provider 支持 `model2vec`、`sentence-transformer`、`api`。向量会和 event block 一起存储，用于混合搜索。

相关文档见 [impl-notes/session-persistence.md](../concepts/impl-notes/session-persistence.md)。

### 2.2 上下文压缩

`core/compact.py:CompactManager` 每轮结束后都会运行。`should_compact(prompt_tokens)` 会检查 prompt token 是否超过 `max_context` 的 80%（可通过 `compact.threshold` 和 `compact.max_tokens` 调整）。触发后，它会先发出一个 `compact_start` activity event，再启动后台 task 运行 summarizer LLM。默认使用主 LLM，也可以单独配置 `compact_model`。summary 会在 **轮与轮之间** 以原子方式插入对话。live zone，也就是最近 `keep_recent_turns` 轮，永远不会被总结。

这样可以保证 controller 不会在一轮执行到一半时，突然发现前面的消息消失。完整原因见 [impl-notes/non-blocking-compaction.md](../concepts/impl-notes/non-blocking-compaction.md)。

---

## 3. Multi-agent & serving

### 3.1 Terrarium 引擎

`terrarium/engine.py:Terrarium` 是运行时引擎 — 每进程一个，托管所有 Creature。引擎拥有：

- `_topology: TopologyState` — 纯数据 graph 模型 (`terrarium/topology.py`)，记录哪些 Creature 共用哪个 graph、哪些频道存在、谁 listen / send。
- `_creatures: dict[str, Creature]` — 运行中的 wrapper (`terrarium/creature_host.py`)。
- `_environments: dict[str, Environment]` — 每个 graph 一份；持有 `shared_channels`。
- `_session_stores: dict[str, SessionStore]` — 每个挂著 store 的 graph 一份。
- `_subscribers: list[_Subscriber]` — `EngineEvent` 发布订阅。

独立 Agent 是 1-creature graph；recipe 是用频道连起来的 connected graph。`Terrarium.with_creature(config)` 是独立 Agent 的捷径；`Terrarium.from_recipe(recipe)` 通过 `terrarium/recipe.py:apply_recipe` 走完一份 `TerrariumConfig` (宣告频道、为每只 Creature 加一条 direct channel、若有 root 加 `report_to_root`、接 listen / send 边、启动一切)。Creature 除了通过频道和 (可选的) 嵌进 system prompt 的 topology hint 外，不会知道自己处于 Terrarium 中。

**频道注入**。当一只 Creature 加入了一个有它要 listen 的频道的 graph，`terrarium/channels.py:inject_channel_trigger` 会往它的 `trigger_manager` 加一个 `ChannelTrigger`。这是 layer 模型里唯一被允许的向下注入：在 graph 里的 Creature *会* 知道自己有同伴 (它得知道)，但只透过引擎给它的 handle。独立 Creature 不会有任何注入。

**热插拔**。拓扑可以在运行时变更。`Terrarium.connect(a, b, channel=...)` 可能合并两个 graph (environment 取联集，频道汇集，挂著的 session store 透过 `terrarium/session_coord.py:apply_merge` 合成一份)。`Terrarium.disconnect` 可能拆 graph (parent session 透过 `apply_split` 复制到两边)。`terrarium/topology.py` 里的纯数据拓扑 mutator 返回 `TopologyDelta`，其 `kind in {"nothing", "merge", "split"}` 驱动这些 live 更新。

**Session 合并 / 分裂**。Session 的单位是 graph 的连通分量。不影响 graph 成员的拓扑变更会沿用既有的 store。`terrarium/session_coord.py` 实作两条分支并发出 `SESSION_FORKED` / `TOPOLOGY_CHANGED` 事件。

**事件 bus**。`terrarium/events.py:EngineEvent` 是统一的可观测面。kind 涵盖 text chunk、频道消息、拓扑变更、session fork、creature 生命周期、processing start / end、error。`Terrarium.subscribe(filter)` 返回与 `EventFilter` 匹配的事件 async iterator。每个订阅者各有一个 queue；取消 iterator 会自动撤销订阅。

旧版 `terrarium/runtime.py:TerrariumRuntime` 与 `serving/manager.py:KohakuManager` 在过渡期间还留在硬盘上 — 较旧的 HTTP route 与 CLI 还会用它们。`api/deps.py` 现在同时暴露 `get_engine()` (新) 与 `get_manager()` (旧) 这两个 singleton；route 会一条一条切过去。

相关文档见 [concepts/multi-agent/terrarium.md](../concepts/multi-agent/terrarium.md) 和 [concepts/multi-agent/privileged-node.md](../concepts/multi-agent/privileged-node.md)。

### 3.2 Channels

`core/channel.py` 定义了两个基础类型：

- `SubAgentChannel` — 队列型，一个消息只会发送给一个 consumer，FIFO。支持 `send` / `receive` / `try_receive`。
- `AgentChannel` — 广播型。每个订阅者都会通过 `ChannelSubscription` 拿到自己的队列。后订阅的消费者收不到历史消息。

Channel 都保存在 `ChannelRegistry` 中，要么挂在 `environment.shared_channels` 下（整个 terrarium 共用），要么挂在 `session.channels` 下（单个 creature 私有）。自动创建的 channel 包括每个 creature 自己的队列，以及 `report_to_root`。`ChannelTrigger` 会把某个 channel 绑定到 agent 的事件流上，将收到的消息转换为 `channel_message` event。

相关文档见 [concepts/modules/channel.md](../concepts/modules/channel.md)。

### 3.3 Environment 和 Session 的区别

- `Environment`（`core/environment.py`）保存整个 terrarium 级别的状态：`shared_channels`、可选的共享 context dict、session bookkeeping。
- `Session`（`core/session.py`）保存单个 creature 的状态：私有 channel registry（也可能直接别名到 environment 的）、`scratchpad`、`tui` 引用、`extra` dict。

每个 agent 实例都有一个 session。到了 terrarium 中，environment 是所有 creature 共享的，session 则各自独立。creature 之间不能直接访问彼此的 session。共享状态只能通过 `environment.shared_channels` 传递，不要绕过它。

相关文档见 [concepts/modules/session-and-environment.md](../concepts/modules/session-and-environment.md)。

### 3.4 Serving 层

`serving/manager.py:KohakuManager` 会为传输层代码创建 `AgentSession` 或 `TerrariumSession` 这样的包装层。
`AgentSession.send_input` 会将 user-input event 推入 agent，然后把 output-router event 转成 JSON dict 向外输出：`text`、`tool_start`、`tool_complete`、`activity`、`token_usage`、`compact_*`、`job_update` 等。

`api/` 中的 HTTP/WS API，以及所有 Python embedding，走的都是这一层，不会直接访问 `Agent` 内部。

API 签名见 `plans/inventory-python-api.md` 中的 §Serving。

### 3.5 Compose 代数内部实现

`compose/core.py` 定义了 `BaseRunnable.run(input)` 和 `__call__(input)`。运算符重载会把组合关系包装起来：

- `__rshift__`（`>>`）→ `Sequence`；如果右边是 dict，会变成 `Router`。
- `__and__`（`&`）→ `Product`（并行运行）。
- `__or__`（`|`）→ `Fallback`。
- `__mul__`（`*`）→ `Retry`。

普通 callable 会自动包装成 `Pure`。`agent()` 会创建持久化的 `AgentRunnable`（多次调用共享对话）；`factory()` 会创建 `AgentFactory`，每次调用都会新建一个 agent。`iterate(async_iter)` 会遍历异步数据源，并对每个元素等待整条 pipeline 执行完毕。`effects.Effects()` 用来记录挂在 pipeline 上的副作用，可通过 `pipeline.effects.get_all()` 读取。

相关文档见 [concepts/python-native/composition-algebra.md](../concepts/python-native/composition-algebra.md)。

### 3.6 Package / extension 系统

安装入口是 `packages.py:install_package(source, editable=False)`。支持三种模式：git clone、本地复制，或在 editable 模式下使用 `.link` 指针。落地目录为 `~/.kohakuterrarium/packages/<name>/`。

路径解析依赖 `resolve_package_path("@<pkg>/<sub>")`。它会沿着 `.link` 查找，或直接遍历目录。配置加载器，例如 `base_config: "@pkg/creatures/..."`，以及 CLI 命令，都依靠它解析路径。

`kohaku.yaml` manifest 中会声明 package 内的 `creatures`、`terrariums`、`tools`、`plugins`、`llm_presets`、`python_dependencies`。

下面几个词不要混淆：

- **Extension** — package 提供的 Python 模块，例如 tool、plugin、LLM preset。
- **Plugin** — 实现生命周期 hook 的那类组件。
- **Package** — 可安装单元，可以包含前两者，也可以只包含配置。

### 3.7 MCP 集成

`mcp/client.py:MCPClientManager.connect(cfg)` 会打开一个 stdio 或 HTTP MCP session，调用 `session.initialize()`，再通过 `list_tools` 查找可用工具，并将结果缓存到 `self._servers[name]`。`disconnect(name)` 负责清理。

agent 启动时，MCP 连接完成后会调用 `_inject_mcp_tools_into_prompt()`，生成一个 “Available MCP Tools” 的 markdown 块，将每台 server、每个 tool 和参数集合列出来。agent 调用 MCP tool 时，不会直接连接 server，而是通过内建元工具 `mcp_call(server, tool, args)`。另外还有 `mcp_list`、`mcp_connect`、`mcp_disconnect`。

支持的传输方式包括 `stdio`（子进程 stdin/stdout）和 `streamable_http plus legacy http/sse`。

---

## 跨流程不变量

上面这些流程都必须遵守以下规则。违反其中任何一条，系统大概率都会出问题。

- **每个 agent 只有一个 `_processing_lock`。** 同一时间只能运行一个 LLM turn。这个约束由 `agent_handlers.py` 保证。
- **Tool 必须并行分发。** 一轮中识别出的所有 tool 都要一起启动。顺序执行就是 bug。
- **压缩不能阻塞。** 对话替换必须是原子的，而且只能发生在轮与轮之间。controller 不应在一次 LLM 调用中途看到消息消失。
- **事件堆叠规则不能乱。** 一串相同的 stackable event 会合并成一条 user message；非 stackable event 一定会打断 batch。
- **必须有背压。** `controller.push_event` 在队列满时会等待。trigger 暴走时，系统应限速，而不是直接丢弃事件。
- **Terrarium 的 session 必须隔离。** creature 不能访问彼此的 session。共享状态只能通过 `environment.shared_channels` 传递，没有例外。

只要你修改了这些流程中的任意部分，都应该回来重新核对这些规则。真正的准绳是 inventory（`plans/inventory-runtime.md`），代码改了，它也要同步更新。

TODO：inventory 目前对 `compose/` 包的细节还没有完全覆盖，这里写得比那边更细；`commands/` 的 framework-command runtime 也还没有完全补进去，output router 的状态机也未补全。等下一轮 inventory 更新时，再把这些内容补齐。
