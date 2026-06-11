---
title: Python API
summary: kohakuterrarium 的公开 Python 接口：errors、Agent、轮次结果、Terrarium 引擎、Creature、会话、packages、compose、validate 与 testing。
tags:
  - reference
  - python
  - api
---

# Python API

公开 Python 接口的权威参考。本页的每个签名都由
`tests/unit/test_docs_python_reference.py` 与源码核对：一旦文档中的符号
与代码不一致，CI 会失败。

日常需要的东西都可以从包根直接导入：

```python
import kohakuterrarium as kt

kt.Agent          # single-agent runtime
kt.Terrarium      # multi-agent engine
kt.Creature       # running-agent handle inside the engine
kt.Studio         # management facade (catalog / sessions / persistence)
kt.tool           # @kt.tool: plain function -> agent tool
kt.FunctionTool   # the class @kt.tool produces
kt.SessionReader  # read-only .kohakutr inspection
kt.SessionStore   # raw session persistence
kt.TurnResult, kt.TextChunk, kt.Activity, kt.TurnEnded   # turn surface
kt.EngineEvent, kt.EventKind, kt.EventFilter             # engine events
kt.ConnectionResult, kt.DisconnectionResult              # topology results
kt.errors         # typed exception hierarchy
kt.validate       # pre-flight validation helpers
kt.packages       # package install / resolve facade (subpackage)
```

`kt.compose` 和 `kt.testing` 以子包形式导入
（`from kohakuterrarium.compose import agent, factory, pure`、
`from kohakuterrarium.testing.llm import ScriptedLLM`）。

叙事性的讲解见：[编程式用法](../guides/programmatic-usage.md)、
[组合](../guides/composition.md)、[会话](../guides/sessions.md)、
[包](../guides/packages.md)。`Studio` 在
[guides/studio](../guides/studio.md) 和 [concepts/studio](../concepts/studio.md) 中介绍。

---

## 错误与严格模式

模块：`kohakuterrarium.errors`。框架在编程接口上抛出的每个错误都派生自
`KTError`，一个 `except` 就能全部接住。许多子类同时还派生自同类故障
历史上抛出的内置异常（`FileNotFoundError` / `ValueError` /
`TimeoutError`），所以已有的 `except` 代码依然有效。

- `KTError`：所有 KohakuTerrarium 错误的基类。
- 配置：
  - `ConfigError(KTError, ValueError)`：Agent / terrarium 配置内容非法。
  - `ConfigNotFoundError(ConfigError, FileNotFoundError)`：配置路径或 `@pkg` 引用不存在。
- 包：
  - `PackageError(KTError)`：包系统错误的基类。
  - `PackageRefError(PackageError, ValueError)`：`@` 引用格式错误。
  - `PackageNotInstalledError(PackageError, FileNotFoundError)`：`@<pkg>/...` 指向未安装的包。
  - `PackagePathNotFoundError(PackageError, FileNotFoundError)`：包存在，但子路径不存在。
- LLM：
  - `LLMError(KTError)`：提供商构建或调用失败。
  - `LLMNotConfiguredError(LLMError, ValueError)`：解析不出可用的 LLM（缺 key、未知 profile）。
- 会话：
  - `SessionError(KTError)`：持久化 / 恢复失败。
  - `SessionNotResumableError(SessionError, ValueError)`：文件存在但无法恢复。
  - `SessionNotFoundError(SessionError, NotFoundError, FileNotFoundError)`：指名的会话不存在。
- 轮次执行：
  - `TurnError(KTError)`：轮次失败（提供商错误、不可恢复的工具崩溃）。
  - `TurnTimeoutError(TurnError, TimeoutError)`：轮次超出 `timeout=` 预算并被取消。
  - `AgentNotRunningError(KTError, RuntimeError)`：操作需要一个已启动的 Agent。
- 请求形错误（studio 层使用；HTTP 适配器将其映射为状态码）：
  `NotFoundError(KTError, KeyError)`、
  `InvalidRequestError(KTError, ValueError)`、`ConflictError(KTError)`。

**默认严格。**编程式构造函数（`Agent.build`、
`Agent.from_path`、`Terrarium.add_creature`、`Terrarium.apply_recipe`）
接受 `strict: bool = True`：解析不出的 LLM、未知工具或损坏的插件会直接
抛错，而不是静默降级。交互式前端传 `strict=False`。`Agent.run` /
`Creature.run` 失败时抛 `TurnError` / `TurnTimeoutError`，除非传了
`raise_on_error=False`。

```python
import kohakuterrarium as kt

try:
    agent = await kt.Agent.build("@kt-biome/creatures/general")
except kt.errors.KTError as e:
    print(f"setup failed: {e}")
```

---

## Agent

模块：`kohakuterrarium.core.agent`（重导出为
`kohakuterrarium.Agent`）。单 Agent 运行时：LLM 控制器、工具、触发器、
子代理、I/O。

构造：

- `await Agent.build(config, *, llm=None, pwd=None, io="config", strict=True, tools=None, plugins=None, subagents=None, outputs=None, user_commands=None, input_module=None, output_module=None, session=None, environment=None) -> Agent`：
  规范的编程式构造函数。
  - `config`：配置文件夹路径、`@pkg/...` 引用，或 `AgentConfig` 实例。
  - `llm`：提供商实例（如 `ScriptedLLM`）、选择器字符串
    （profile / preset 名称或 `provider/model[@variations]`）、
    `LLMProfile`，或 `None`（从配置解析）。
  - `io`：`"config"`（按声明启动 I/O）、`"none"`（禁用输入）、
    `"headless"`（禁用输入且静音默认输出，批处理默认值）。显式的
    `input_module` / `output_module` 优先于 `io`。
  - `tools` / `plugins` / `subagents`：在系统提示词聚合之前注册的实例
    （`kt.tool` 适配器、`BasePlugin` 对象、`SubAgentConfig`）。
  - `outputs`：额外的具名输出 `{name: OutputModule}`；
    `user_commands` 是额外的斜杠命令 `{name: UserCommand}`。
  - 返回一个配置完毕但**尚未启动**的 Agent。
- `Agent.from_path(config_path, *, input_module=None, output_module=None, session=None, environment=None, llm=None, pwd=None, strict=True, tools=None, plugins=None) -> Agent`：
  同步的底层构造函数；新代码请用 `build`。

生命周期：

- `await agent.start()` / `await agent.stop()`：务必成对调用
  （`Agent` 上没有 `async with`）。
- `await agent.run_forever()`：传统的自治主循环
  （由输入模块 + 触发器驱动 Agent，直到输入退出）。
  `kt run` 用的就是它；一次性脚本请改用 `run`。
- `agent.interrupt()`：取消当前轮次（非阻塞）。

轮次驱动（见[轮次结果与事件](#轮次结果与事件)）：

- `await agent.run(content, *, timeout=None, source="programmatic", raise_on_error=True) -> TurnResult`
- `agent.run_stream(content, *, timeout=None, source="programmatic") -> AsyncIterator[TurnEvent]`

运行时扩展（每个都会刷新当前系统提示词）：

- `agent.add_tool(tool)`：注册表 + 执行器 + 提示词一步到位；按 `tool_name` 幂等。
- `await agent.add_plugin(plugin, *, enabled=True)`：即使在 `start()` 之后添加，也会触发插件的 `on_load`。
- `agent.add_subagent(config)`：注册一个 `SubAgentConfig`。
- `agent.refresh_system_prompt()`：手动重算聚合提示词。

其他运行时控制：

- `await agent.inject_input(content, source="programmatic") -> bool`：只推入输入、不消费输出。
- `agent.switch_model(profile_name) -> str` / `agent.llm_identifier() -> str`
- `agent.attach_session_store(store)`：接上一个 `SessionStore` 落盘端。
- `agent.set_output_handler(handler, replace_default=False)`
- 属性：`is_running`、`tools`、`subagents`、`conversation_history`。

```python
import kohakuterrarium as kt

agent = await kt.Agent.build("@kt-biome/creatures/general", io="headless")
await agent.start()
try:
    result = await agent.run("Summarize ./README.md", timeout=300)
    print(result.text)
finally:
    await agent.stop()
```

### `@kt.tool` / `FunctionTool`

模块：`kohakuterrarium.modules.tool.function`（重导出为
`kohakuterrarium.tool` / `kohakuterrarium.FunctionTool`）。把一个普通的
同步或异步函数变成 Agent 工具：名称取自函数名，描述取自 docstring 首行，
JSON-schema 参数取自类型注解。同步函数经 `asyncio.to_thread` 运行。
名为 `context` 的参数会收到 `ToolContext`。

- `tool(fn=None, *, name=None, description=None, execution_mode=ExecutionMode.DIRECT) -> FunctionTool | decorator`：
  可写作 `@tool`、`@tool(name=..., description=...)`，或直接调用
  `tool(existing_fn)`。
- `FunctionTool(fn, *, name=None, description=None, execution_mode=...)`：背后的类。

```python
import kohakuterrarium as kt

@kt.tool
def check_stock(item: str) -> str:
    """Look up how many units of an item are in stock."""
    return f"{item}: 3 in stock"

agent = await kt.Agent.build(cfg, tools=[check_stock])
```

---

## 轮次结果与事件

模块：`kohakuterrarium.core.turn`（四个类型都从包根重导出）。
`run`、`run_stream` 和 `attach` 返回 / 产出的带类型观察接口。

- `TurnResult`：一个完整轮次的结果：
  - `status: str`：`"ok"` | `"error"` | `"timeout"` | `"interrupted"`。
  - `ok: bool`：属性，即 `status == "ok"`。
  - `text: str`：拼接后的助手文本。
  - `error: str | None`：status 非 ok 时的失败详情。
  - `tool_calls: list[Activity]`：`tool_start` / `tool_done` / `tool_error` 活动。
  - `activities: list[Activity]`：该轮次的所有非文本事件。
  - `usage: dict | None`：提供商上报的 token 用量（如果有）。
  - `duration_s: float`
- `TextChunk`：`text: str`；流式输出的一段助手文本。
- `Activity`：`kind: str`、`detail: str`、`metadata: dict`；
  非文本事件（`tool_start`、`tool_done`、`tool_error`、
  `subagent_start`、`subagent_done`、`processing_start`、
  `processing_end`、`processing_error`、`session_info`、`ask_user` 等）。
- `TurnEnded`：`result: TurnResult`；`run_stream` 的终结事件。
- `TurnEvent = TextChunk | Activity | TurnEnded`：流产出的联合类型。
- `AgentEventStream`：`Creature.attach()` 背后的开放式观察者：
  既是异步上下文管理器，也是 `TurnEvent` 的异步迭代器；非破坏性、
  支持多消费者。

值得了解的语义：

- `run` 默认抛 `TurnError` / `TurnTimeoutError`；传
  `raise_on_error=False` 则总能拿到 `TurnResult`，由你自己根据
  `result.status` 分支。
- `timeout=` 会真正**中断**轮次（控制器循环被取消并收尾），
  不会丢下一个还在烧 token 的轮次不管。
- `run_stream` 在迭代过程中绝不抛错：错误以
  `Activity(kind="processing_error")` 的形式出现，并体现在最后的
  `TurnEnded(result)` 里。

```python
async for ev in agent.run_stream("Refactor utils.py"):
    match ev:
        case kt.TextChunk(text=t):
            print(t, end="", flush=True)
        case kt.Activity(kind="tool_start", detail=d):
            print(f"\n[tool] {d}")
        case kt.TurnEnded(result=r):
            print(f"\n[done: {r.status}]")
```

---

## Terrarium 引擎

模块：`kohakuterrarium.terrarium.engine`（重导出为
`kohakuterrarium.Terrarium`）。多 Agent 运行时引擎，托管进程内所有
运行中的生物 (creature)；单独运行的 Agent 就是一张单生物的图。

构造：

- `Terrarium(*, pwd=None, session_dir=None)`：裸引擎。
  `session_dir` 开启**自动会话 (autosession)**：每张新图自动获得一个
  `<session_dir>/<graph_id>.kohakutr` 存储（合并 / 拆分产生的子图也落在这里）。
- `await Terrarium.from_recipe(recipe, *, pwd=None) -> Terrarium`：
  应用了配方的引擎（`TerrariumConfig` 或 YAML 路径 / `@pkg` 引用）。
- `await Terrarium.resume(store, *, pwd=None, llm=None) -> Terrarium`：
  新引擎 + 接管一个已保存的会话（`SessionStore` 或路径；
  `llm` 是选择器字符串覆盖）。
- `await Terrarium.with_creature(config, *, pwd=None) -> tuple[Terrarium, Creature]`：
  一次调用得到引擎 + 一个生物。
- `async with Terrarium() as engine: ...`：`__aexit__` 调用 `shutdown()`。

生物 CRUD：

- `await engine.add_creature(config, *, graph=None, creature_id=None, llm=None, pwd=None, start=True, is_privileged=False, parent_creature_id=None, io="config", strict=True, session=None, name=None, tools=None, plugins=None) -> Creature`
  - `config`：路径 / `@pkg/...` 引用、`AgentConfig`、`CreatureConfig`，
    或一个预先构建好的 `Creature`（对预构建对象传构建期参数会抛错）。
  - `session`：持久化控制：传路径则在该文件创建存储；`True` 在默认
    会话目录创建；`False` 即使在 autosession 下也禁用持久化；
    `SessionStore` 则原样挂上；`None`（默认）跟随引擎
    （autosession / 图已有的存储 / 无）。
  - `is_privileged`：授予 `group_*` 图变更工具
    （只升不降；不会撤销预构建生物已有的特权）。
  - `llm` / `io` / `strict` / `tools` / `plugins`：与 `Agent.build`
    的约定相同。
  - `name`：生成时的显示名覆盖。
- `await engine.remove_creature(creature)`：停止并移除；可能触发图的自动拆分。
- `engine.get_creature(creature_id) -> Creature` / `engine.list_creatures() -> list[Creature]`
- Python 风格访问：`engine[id]`、`id in engine`、`for c in engine`、`len(engine)`。

频道与拓扑（所有图频道都是广播：每个监听者都会收到每条消息）：

- `await engine.add_channel(graph, name, description="") -> ChannelInfo`
- `await engine.remove_channel(graph, name) -> TopologyDelta`：可能自动拆分。
- `await engine.connect(sender, receiver, *, channel=None) -> ConnectionResult`：
  跨图连接会自动合并（环境取并集，会话存储合并）。
- `await engine.disconnect(sender, receiver, *, channel=None) -> DisconnectionResult`：
  可能自动拆分（每一侧各得一份存储副本）。
- `engine.environment(graph) -> Environment`：图的活动环境的公开句柄
  （未知图抛 `KeyError`）。
- `engine.channel(graph, name)`：活动广播频道句柄（或 `None`）：
  `await ch.send(ChannelMessage(...))` 可以给图“喂”消息，
  `ch.history` 可以观察流量。
- `engine.get_graph(graph_id) -> GraphTopology` / `engine.list_graphs() -> list[GraphTopology]`
- `await engine.assign_root(creature, *, report_channel="report_to_root") -> RootAssignment`：
  把一个生物提升为其所在图的特权节点，并接好汇报频道。

配方与恢复：

- `await engine.apply_recipe(recipe, *, graph=None, pwd=None, llm=None, strict=True, session=None, creature_builder=None) -> GraphTopology`：
  `session` 遵循 `add_creature` 的约定，但为整张图创建一个
  terrarium 类型的存储。
- `await engine.adopt_session(store, *, pwd=None, llm=None) -> str`：
  把已保存的会话恢复进当前运行中的引擎；返回新的 `graph_id`。
- `await engine.attach_session(graph, store)`：给图挂上一个
  `SessionStore`（或按路径创建一个）。

生命周期与输出连线：

- `await engine.start(creature)` / `await engine.stop(creature)` / `await engine.stop_graph(graph)`
- `await engine.shutdown()`：停止一切、关闭引擎创建的所有存储、
  终止订阅者；幂等。
- `await engine.wire_output(creature, target) -> str` / `await engine.unwire_output(creature, edge_id) -> bool`
- `engine.list_output_wiring(creature) -> list[dict]`
- `await engine.wire_output_sink(creature, sink) -> str` / `await engine.unwire_output_sink(creature, sink_id) -> bool`

可观测性：

- `engine.subscribe(filter=None) -> AsyncIterator[EngineEvent]`：
  订阅者在调用那一刻即注册（`subscribe()` 与第一次 `await` 之间的
  事件会被缓冲）；跳出循环即注销；`shutdown()` 会终止它。
- `engine.status()`（汇总）/ `engine.status(creature)`（单生物 dict）。

凡是接受 `CreatureRef` / `GraphRef` 的地方，传对象或其字符串 id 都可以。

### 引擎事件

模块：`kohakuterrarium.terrarium.events`（从包根重导出）。
引擎总线只承载**结构**事件；单个生物的内容（文本、工具活动）
走带类型的轮次接口。

- `EventKind`（`str` 枚举）：`CHANNEL_MESSAGE`、`TOPOLOGY_CHANGED`、
  `SESSION_KIND_CHANGED`、`CREATURE_ADDED`、`CREATURE_STARTED`、
  `CREATURE_STOPPED`、`OUTPUT_WIRE_ADDED`、`OUTPUT_WIRE_REMOVED`、
  `PARENT_LINK_CHANGED`。
- `EngineEvent`：`kind`、`creature_id`、`graph_id`、`channel`、
  `payload: dict`、`ts: float`。
- `EventFilter(kinds=None, creature_ids=None, graph_ids=None, channels=None)`：
  各字段按 AND 组合；`None` 表示“任意”；`matches(ev) -> bool`。
- `ConnectionResult`：`channel`、`trigger_id`、`delta_kind`
  （`"nothing"` | `"merge"`）、`graph_id`。
- `DisconnectionResult`：`channels: list[str]`、`delta_kind`
  （`"nothing"` | `"split"`）。

```python
import kohakuterrarium as kt

async with kt.Terrarium(session_dir="runs/") as engine:
    alice = await engine.add_creature("@kt-biome/creatures/general")
    bob = await engine.add_creature("@kt-biome/creatures/general")

    # Subscribe BEFORE mutating: events emitted between subscribe()
    # and the first await are buffered, so none are lost.
    events = engine.subscribe(kt.EventFilter(kinds={kt.EventKind.TOPOLOGY_CHANGED}))
    result = await engine.connect(alice, bob, channel="alice_to_bob")
    assert result.delta_kind == "merge"   # two graphs became one
    ev = await anext(events)
    print(ev.kind, ev.payload)
```

---

## Creature

模块：`kohakuterrarium.terrarium.creature_host`（重导出为
`kohakuterrarium.Creature`）。引擎对一个运行中 Agent 的句柄。
由 `add_creature` / `with_creature` 返回；不直接构造。

属性：`creature_id`、`name`、`agent: Agent`、`graph_id`、
`listen_channels`、`send_channels`、`is_privileged`、
`parent_creature_id`、`is_running`、`status`（取值为 `"not_started"`、
`"error"`、`"busy"`、`"idle"`、`"stopped"` 之一）。

轮次驱动（委托给底层 `Agent`）：

- `await creature.run(content, **kwargs) -> TurnResult`：
  `timeout=` / `raise_on_error=` 语义与 `Agent.run` 相同。
- `creature.run_stream(content, **kwargs) -> AsyncIterator[TurnEvent]`
- `creature.attach() -> AgentEventStream`：非破坏性、多消费者的
  观察者；带外轮次（触发器、频道消息）也能捕获：

  ```python
  async with creature.attach() as stream:
      async for ev in stream:
          ...
  ```

- `await creature.chat(message) -> AsyncIterator[str]`：纯文本语法糖
  （注入 + 排空）；新代码请用 `run` / `run_stream`。
- `await creature.inject_input(message, *, source="chat")`：只推入
  输入、不消费输出。

生命周期 / 自省：

- `await creature.start()` / `await creature.stop()`：幂等。
- `creature.get_status() -> dict`：model、provider、session_id、
  tools、subagents、pwd、channels、特权状态。
- `creature.get_log_entries(last_n=20)` / `creature.get_log_text(last_n=10)`。

---

## 会话

### `SessionReader`：只读检视

模块：`kohakuterrarium.session.reader`（重导出为
`kohakuterrarium.SessionReader`）。`.kohakutr` 文件的一站式只读接口。
经 `SessionStore.open_readonly` 打开，读取绝不会更新 `last_active`
或改写 `status`。支持上下文管理器。

- `SessionReader(path)`：文件不存在时抛 `FileNotFoundError`；
  `~` 会展开。
- 属性：`path: Path`、`meta: dict`（session_id、config_type /
  config_path、status 等）、`agents: list[str]`。
- `reader.events(agent=None) -> list[dict]`：追加式事件日志；
  `None` 则拼接所有 Agent 的事件。
- `reader.conversation(agent=None) -> list[dict]`：最终对话快照
  （OpenAI 消息 dict）。
- `reader.channel_messages(channel) -> list[dict]`：某个 terrarium
  频道的历史。
- `reader.turns(agent=None) -> list[TurnView]`：从事件日志重组出的
  活动分支轮次。`TurnView`：`index`、`user_text`、
  `assistant_text`、`tool_calls: list[dict]`、`source`、`ts`。
- `reader.search(query, *, mode="fts", k=10, agent=None) -> list[SearchResult]`：
  全文（或向量，如果建过索引）搜索；未建索引的会话返回空结果。
- `reader.index() -> int`：临时为 `search` 构建 FTS 索引。
- `reader.close()`：或者用 `with SessionReader(...) as r:`。

```python
import kohakuterrarium as kt

with kt.SessionReader("runs/student-42.kohakutr") as r:
    print(r.meta["status"], r.agents)
    for turn in r.turns():
        print(turn.user_text, "->", turn.assistant_text[:80])
```

### 引擎持有的持久化与恢复

持久化是引擎特性，不需要手动走 `SessionStore` +
`init_meta` + `attach_session` 那套仪式：

- `Terrarium(session_dir="runs/")`：每张图自动开启会话。
- `engine.add_creature(..., session="runs/x.kohakutr")`：单生物存储
  （路径 | `True` | `False` | `SessionStore` | `None`）。
- `engine.apply_recipe(..., session=...)`：配方图共用一个存储。
- `await Terrarium.resume(store_or_path, *, pwd=None, llm=None)`：
  从已保存的会话起一个新引擎。
- `await engine.adopt_session(store_or_path, *, pwd=None, llm=None) -> str`：
  把会话恢复进运行中的引擎。
- `await engine.shutdown()` 会关闭引擎创建的所有存储
  （文件不再卡在 `status: "running"`）。

`SessionStore`（模块 `kohakuterrarium.session.store`，从包根重导出）
仍是底层原始接口：

- `SessionStore(path)`：以读写方式打开。
- `SessionStore.open_readonly(path)`：`close()` 绝不修改元数据；
  所有列表 / 预览类消费者都应该用它。
- `store.close(update_status=True)`：幂等；`update_status=True`
  把会话标记为已暂停并更新 `last_active`（只读存储上忽略）。
- 事件 / 对话 / 状态 / 频道 / 任务访问器：见[会话指南](../guides/sessions.md)。

---

## 包

模块：`kohakuterrarium.packages`，一个惰性门面（PEP 562）：名称在首次
属性访问时才解析，导入它本身很便宜。

安装生命周期：

- `ensure(spec, *, deps="auto") -> str`：幂等安装；已安装则立即返回
  包名（不做版本检查，即使 spec 锁定了版本）。批处理脚本开头就该
  调它。
- `install_package_spec(spec, editable=False, name_override=None, *, deps="auto") -> str`：
  `@name` / `@name@version` / `@source/name` 经市场解析；
  git URL 和本地路径直接放行。
- `install_package(source, editable=False, name_override=None, ref=None, *, deps="auto") -> str`：
  git URL 或本地目录。
- `update_package(name, *, deps="auto") -> str`：原地
  `git pull --ff-only`；拒绝更新锁定版本的安装。
- `uninstall_package(name) -> bool`

`deps` 是 Python 依赖策略：`"auto"` 通过
`sys.executable -m pip` 安装清单的 `python_dependencies` +
`requirements.txt`；`"never"` 跳过。未知策略或安装失败抛
`PackageError`。

引用解析与布局：

- `is_package_ref(path) -> bool`：这是不是 `@pkg/...` 路径引用？
- `resolve_package_path(ref) -> Path` / `resolve_any_path(path) -> Path`
- `packages_dir() -> Path`：当前的包目录；遵循
  `KT_CONFIG_DIR`（默认 `~/.kohakuterrarium/packages`）。
- `get_package_root(name) -> Path | None` / `find_package_root_for_path(path) -> Path | None`
- `list_packages()` / `get_package_modules(...)`

清单槽位解析器：`resolve_package_tool`、`resolve_package_io`、
`resolve_package_trigger`、`resolve_package_command`、
`resolve_package_user_command`、`resolve_package_prompt`、
`resolve_package_skills`、`get_package_framework_hints`。

重导出的类型化错误：`PackageError`、`PackageRefError`、
`PackageNotInstalledError`、`PackagePathNotFoundError`。

```python
from kohakuterrarium import packages

packages.ensure("@kt-biome")                  # idempotent install
path = packages.resolve_package_path("@kt-biome/creatures/swe")
for pkg in packages.list_packages():
    print(pkg["name"], pkg["version"])
```

---

## Compose

模块：`kohakuterrarium.compose`。作用于 Agent 和普通可调用对象的
流水线代数。导出：`agent`、`factory`、`AgentRunnable`、
`AgentFactory`、`BaseRunnable`、`Runnable`、`Pure`、`pure`、`Sequence`、
`Product`、`Fallback`、`FailsWhen`、`Retry`、`Router`、
`PipelineIterator`。

Agent 包装器：

- `await agent(config, *, engine=None, pwd=None, llm=None) -> AgentRunnable`：
  持久 Agent（对话跨调用累积）；异步上下文管理器。`config` 是
  `AgentConfig`、路径或 `@pkg/...` 引用；`llm` 遵循标准选择器语法。
  `engine=None` 时会创建一个私有 `Terrarium` 并随 runnable 一起销毁；
  传入共享引擎可以摊薄启动成本（此时关闭只移除该生物）。
- `factory(config, *, engine=None, pwd=None, llm=None) -> AgentFactory`：
  临时型：每次调用新建一个 Agent，用完即毁。

运算符（都返回 `BaseRunnable`）：

- `a >> b`：顺序；输出接到下一个的输入。普通可调用对象自动包成
  `Pure`；右侧是 dict 则变成 `Router`。
- `a & b`：并行积；返回元组。首个失败发生时，存活的兄弟会先被
  **取消并 await 完毕**，然后异常才向外传播。
- `a | b`：回退；出异常时用原始输入运行 `b`。回退也失败时，
  主分支的异常作为 `__cause__` 链上。
- `a * N`：最多重试 `N` 次（立即重试）。
- `.retry(max_attempts, *, backoff=0.0, max_backoff=30.0)`：带指数
  退避的重试（每次睡眠翻倍，有上限）。
- `.iterate(initial_input)`：异步迭代器，把输出回喂为下一次输入
  （`it.feed(value)` 可覆盖下一次输入）。
- `.map(fn)` / `.contramap(fn)`：后置 / 前置变换。
- `.fails_when(predicate)`：输出命中谓词时抛 `ValueError`
  （把“坏的成功”变成回退触发条件）。

```python
from kohakuterrarium.compose import agent, factory, pure

async with await agent("@kt-biome/creatures/swe", llm="fast") as swe:
    pipeline = swe >> pure(str.strip) >> (lambda t: f"Review:\n{t}")
    result = await (pipeline.retry(3, backoff=1.0))("Implement the feature")
```

---

## Validate

模块：`kohakuterrarium.validate`（重导出为
`kohakuterrarium.validate`）。遇到第一个问题就抛类型化错误的
预检；`kt doctor` 是它的 CLI 包装。

- `validate.config(path) -> AgentConfig`：验证 Agent 配置文件夹 /
  `@pkg` 引用能以完全严格模式解析。
- `validate.terrarium_config(path) -> TerrariumConfig`：验证 terrarium
  配方能解析。
- `validate.llm(selector=None) -> str`：验证选择器能解析且提供商能构造
  （凭据检查，不走网络）；返回规范的
  `provider/name[@variations]` 标识。抛
  `LLMNotConfiguredError` / `ValueError`。
- `validate.creature(path, *, llm_binding=None) -> ValidationReport`：
  完整的干跑构建（`strict=True`、headless IO、从不启动）。
  `ValidationReport`：`name`、`config_path`、`model_identifier`、
  `tools`、`plugins`、`subagents`。
- `await validate.ping(selector_or_provider=None, *, timeout=30.0) -> str`：
  唯一会触网的验证器：一次最小的 LLM 往返；返回回复文本。

```python
import kohakuterrarium as kt

kt.validate.config("./scoring-agent")
kt.validate.llm("openai/gpt-5@reasoning=high")
report = kt.validate.creature("./scoring-agent")
await kt.validate.ping("openai/gpt-5")
```

---

## Testing

模块：`kohakuterrarium.testing`。

- `ScriptedLLM(script: list[ScriptEntry] | list[str] | None = None)`
  （模块 `kohakuterrarium.testing.llm`）：确定性的提供商。
  **优先直接注入**：每个构造入口都接受实例形式的 `llm=`：
  `Agent.build(cfg, llm=ScriptedLLM([...]))`、
  `engine.add_creature(path, llm=...)`、`compose.agent(cfg, llm=...)`。
  断言接口：`call_count`、`call_log`。
  `ScriptEntry(response, match=None, delay_per_chunk=0, chunk_size=10)`。
- `OutputRecorder`（`testing.output`）：捕获 `chunks`、`writes`、
  `activities`、`all_text`。
- `EventRecorder`（`testing.events`）：`record`、`get_all`、
  `get_by_type`、`clear`。
- `TestAgentBuilder`（`testing.agent`）：单元式 Agent 测试的流式
  脚手架（`with_llm_script`、`with_builtin_tools`、`build()`）。

`bootstrap.llm.create_llm_provider` +
`bootstrap.agent_init.create_llm_provider` 这个 monkeypatch 接缝只为
框架内部构造 Agent 的路径保留（配置文件、恢复、配方）。

```python
import kohakuterrarium as kt
from kohakuterrarium.testing.llm import ScriptedLLM

agent = await kt.Agent.build(cfg, llm=ScriptedLLM(["Hello!"]), io="headless")
await agent.start()
result = await agent.run("hi")
assert result.text == "Hello!"
await agent.stop()
```

---

## 另请参阅

- 指南：[编程式用法](../guides/programmatic-usage.md)、
  [组合](../guides/composition.md)、
  [会话](../guides/sessions.md)、[包](../guides/packages.md)、
  [studio](../guides/studio.md)、[自定义模块](../guides/custom-modules.md)、
  [插件](../guides/plugins.md)。
- 教程：[第一次 Python 嵌入](../tutorials/first-python-embedding.md)。
- 参考：[cli](cli.md)、[http](http.md)、
  [configuration](configuration.md)、[builtins](builtins.md)、
  [plugin-hooks](plugin-hooks.md)。
- 可运行脚本：[`examples/code/`](../../../examples/code/)：
  `batch_grading.py` 是批处理的范式示例。
