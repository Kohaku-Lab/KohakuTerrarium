---
title: Python API
summary: kohakuterrarium 的公開介面：errors、Agent、輪次結果、Terrarium 引擎、Creature、工作階段、packages、compose、validate 與 testing。
tags:
  - reference
  - python
  - api
---

# Python API

公開 Python 介面的正式參考。本頁的每一個簽名都由
`tests/unit/test_docs_python_reference.py` 對照原始碼驗證：
只要這裡的符號跟程式碼不一致，CI 就會失敗。

平常需要的東西都可以從套件根目錄 import：

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

`kt.compose` 與 `kt.testing` 以子套件方式 import
(`from kohakuterrarium.compose import agent, factory, pure`、
`from kohakuterrarium.testing.llm import ScriptedLLM`)。

走讀式的說明在：[程式化使用](../guides/programmatic-usage.md)、
[組合](../guides/composition.md)、[工作階段](../guides/sessions.md)、
[套件](../guides/packages.md)。`Studio` 的說明在
[guides/studio](../guides/studio.md) 與 [concepts/studio](../concepts/studio.md)。

---

## 錯誤與嚴格模式

模組：`kohakuterrarium.errors`。框架在程式化介面上拋出的每個錯誤都繼承自
`KTError`，所以一個 `except` 就能全部接住。許多子類別同時也繼承同一種失敗
過去拋出的內建例外 (`FileNotFoundError` / `ValueError` / `TimeoutError`)，
既有的 `except` 寫法不用改。

- `KTError`：所有 KohakuTerrarium 錯誤的基底類別。
- 設定：
  - `ConfigError(KTError, ValueError)`：agent / terrarium 設定內容無效。
  - `ConfigNotFoundError(ConfigError, FileNotFoundError)`：設定路徑或 `@pkg` 參照找不到。
- 套件：
  - `PackageError(KTError)`：套件系統錯誤的基底。
  - `PackageRefError(PackageError, ValueError)`：`@` 參照格式錯誤。
  - `PackageNotInstalledError(PackageError, FileNotFoundError)`：`@<pkg>/...` 指到未安裝的套件。
  - `PackagePathNotFoundError(PackageError, FileNotFoundError)`：套件存在，但子路徑不存在。
- LLM：
  - `LLMError(KTError)`：provider 建構或呼叫失敗。
  - `LLMNotConfiguredError(LLMError, ValueError)`：解析不出可用的 LLM (缺金鑰、profile 不存在)。
- 工作階段：
  - `SessionError(KTError)`：持久化 / 恢復失敗。
  - `SessionNotResumableError(SessionError, ValueError)`：檔案存在但無法恢復。
  - `SessionNotFoundError(SessionError, NotFoundError, FileNotFoundError)`：指名的工作階段不存在。
- 輪次執行：
  - `TurnError(KTError)`：輪次失敗 (provider 錯誤、工具無法復原的崩潰)。
  - `TurnTimeoutError(TurnError, TimeoutError)`：輪次超出 `timeout=` 預算並被取消。
  - `AgentNotRunningError(KTError, RuntimeError)`：操作需要已啟動的 agent。
- 請求形 (給 studio 層用；HTTP adapter 會對應到狀態碼)：
  `NotFoundError(KTError, KeyError)`、
  `InvalidRequestError(KTError, ValueError)`、`ConflictError(KTError)`。

**預設嚴格。** 程式化建構子 (`Agent.build`、`Agent.from_path`、
`Terrarium.add_creature`、`Terrarium.apply_recipe`)
都帶 `strict: bool = True`：解析不出的 LLM、不認識的工具、壞掉的外掛
會直接拋錯，而不是默默降級。互動式前端會傳 `strict=False`。
`Agent.run` / `Creature.run` 失敗時預設拋出
`TurnError` / `TurnTimeoutError`，除非你傳 `raise_on_error=False`。

```python
import kohakuterrarium as kt

try:
    agent = await kt.Agent.build("@kt-biome/creatures/general")
except kt.errors.KTError as e:
    print(f"setup failed: {e}")
```

---

## Agent

模組：`kohakuterrarium.core.agent` (re-export 為
`kohakuterrarium.Agent`)。單 agent 執行期：LLM 控制器、
工具、觸發器、子代理、I/O。

建構：

- `await Agent.build(config, *, llm=None, pwd=None, io="config", strict=True, tools=None, plugins=None, subagents=None, outputs=None, user_commands=None, input_module=None, output_module=None, session=None, environment=None) -> Agent`：
  標準的程式化建構子。
  - `config`：設定資料夾路徑、`@pkg/...` 參照，或一個
    `AgentConfig` 實例。
  - `llm`：provider 實例 (例如 `ScriptedLLM`)、選擇器字串
    (profile / preset 名稱或 `provider/model[@variations]`)、
    `LLMProfile`，或 `None` (從設定解析)。
  - `io`：`"config"` (依宣告啟動 I/O)、`"none"` (停用輸入)、
    `"headless"` (停用輸入且靜音預設輸出；批次任務的預設)。
    明確指定的 `input_module` / `output_module` 優先於 `io`。
  - `tools` / `plugins` / `subagents`：在系統提示詞聚合之前註冊的
    實例 (`kt.tool` adapter、`BasePlugin` 物件、`SubAgentConfig`)。
  - `outputs`：額外的具名輸出 `{name: OutputModule}`；
    `user_commands`：額外的 slash 指令 `{name: UserCommand}`。
  - 回傳已設定好、但**尚未啟動**的 agent。
- `Agent.from_path(config_path, *, input_module=None, output_module=None, session=None, environment=None, llm=None, pwd=None, strict=True, tools=None, plugins=None) -> Agent`：
  同步的低階建構子；新程式碼請優先用 `build`。

生命週期：

- `await agent.start()` / `await agent.stop()`：一定要成對呼叫
  (`Agent` 沒有 `async with`)。
- `await agent.run_forever()`：舊式的自主主迴圈
  (由輸入模組 + 觸發器驅動 agent，直到輸入結束)。
  `kt run` 走的就是這條路；一次性腳本請改用 `run`。
- `agent.interrupt()`：取消進行中的輪次 (非阻塞)。

輪次驅動 (見[輪次結果與事件](#輪次結果與事件))：

- `await agent.run(content, *, timeout=None, source="programmatic", raise_on_error=True) -> TurnResult`
- `agent.run_stream(content, *, timeout=None, source="programmatic") -> AsyncIterator[TurnEvent]`

執行期擴充 (每一項都會即時更新系統提示詞)：

- `agent.add_tool(tool)`：registry + executor + prompt 一次搞定；對 `tool_name` 冪等。
- `await agent.add_plugin(plugin, *, enabled=True)`：即使在 `start()` 之後加入，也會觸發外掛的 `on_load`。
- `agent.add_subagent(config)`：註冊一個 `SubAgentConfig`。
- `agent.refresh_system_prompt()`：手動重算聚合後的提示詞。

其他執行期控制：

- `await agent.inject_input(content, source="programmatic") -> bool`：推入輸入但不消費輸出。
- `agent.switch_model(profile_name) -> str` / `agent.llm_identifier() -> str`
- `agent.attach_session_store(store)`：接上一個 `SessionStore` sink。
- `agent.set_output_handler(handler, replace_default=False)`
- 屬性：`is_running`、`tools`、`subagents`、`conversation_history`。

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

模組：`kohakuterrarium.modules.tool.function` (re-export 為
`kohakuterrarium.tool` / `kohakuterrarium.FunctionTool`)。把普通的
同步或非同步函式變成 agent 工具：名稱取自函式名，
描述取自 docstring 第一行，JSON-schema 參數從型別註記產生。
同步函式透過 `asyncio.to_thread` 執行。`context`
參數會收到 `ToolContext`。

- `tool(fn=None, *, name=None, description=None, execution_mode=ExecutionMode.DIRECT) -> FunctionTool | decorator`：
  可寫成 `@tool`、`@tool(name=..., description=...)`，或直接呼叫
  `tool(existing_fn)`。
- `FunctionTool(fn, *, name=None, description=None, execution_mode=...)`：背後的類別。

```python
import kohakuterrarium as kt

@kt.tool
def check_stock(item: str) -> str:
    """Look up how many units of an item are in stock."""
    return f"{item}: 3 in stock"

agent = await kt.Agent.build(cfg, tools=[check_stock])
```

---

## 輪次結果與事件

模組：`kohakuterrarium.core.turn` (四個型別都從套件根目錄
re-export)。`run`、`run_stream`、`attach` 回傳 / 產出的
有型別觀察介面。

- `TurnResult`：一個完整輪次的結果：
  - `status: str`：`"ok"` | `"error"` | `"timeout"` | `"interrupted"`。
  - `ok: bool`：property，等於 `status == "ok"`。
  - `text: str`：串接後的 assistant 文字。
  - `error: str | None`：status 非 ok 時的失敗細節。
  - `tool_calls: list[Activity]`：`tool_start` / `tool_done` / `tool_error` 活動。
  - `activities: list[Activity]`：該輪次的所有非文字事件。
  - `usage: dict | None`：provider 有回報時的 token 用量。
  - `duration_s: float`
- `TextChunk`：`text: str`；一段串流出來的 assistant 文字。
- `Activity`：`kind: str`、`detail: str`、`metadata: dict`；
  非文字事件 (`tool_start`、`tool_done`、`tool_error`、
  `subagent_start`、`subagent_done`、`processing_start`、
  `processing_end`、`processing_error`、`session_info`、`ask_user`…)。
- `TurnEnded`：`result: TurnResult`；`run_stream` 的終結事件。
- `TurnEvent = TextChunk | Activity | TurnEnded`：串流產出的 union。
- `AgentEventStream`：`Creature.attach()` 背後的開放式觀察者：
  async context manager + `TurnEvent` 的 async iterator；
  非破壞性、可多消費者。

值得知道的語意：

- `run` 預設拋出 `TurnError` / `TurnTimeoutError`；傳
  `raise_on_error=False` 可以永遠拿到 `TurnResult`，自己依
  `result.status` 分支。
- `timeout=` 真的會**中斷**輪次 (控制器迴圈被取消並收尾)，
  不是放任一個還在燒的輪次不管。
- `run_stream` 迭代過程中永遠不拋錯：錯誤會以
  `Activity(kind="processing_error")` 浮現，並出現在最後的
  `TurnEnded(result)`。

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

模組：`kohakuterrarium.terrarium.engine` (re-export 為
`kohakuterrarium.Terrarium`)。多代理執行期引擎：托管行程內
所有運行中的生物 (creature)；獨立 agent 就是一張單生物圖。

建構：

- `Terrarium(*, pwd=None, session_dir=None)`：裸引擎。
  指定 `session_dir` 會開啟 **autosession**：每張新圖自動拿到一個
  `<session_dir>/<graph_id>.kohakutr` store (合併 / 分割產生的
  子圖也存在那裡)。
- `await Terrarium.from_recipe(recipe, *, pwd=None) -> Terrarium`：
  套用配方的引擎 (`TerrariumConfig` 或 YAML 路徑 / `@pkg` 參照)。
- `await Terrarium.resume(store, *, pwd=None, llm=None) -> Terrarium`：
  新引擎 + 認領一個已儲存的工作階段 (`SessionStore` 或路徑；
  `llm` 是選擇器字串覆寫)。
- `await Terrarium.with_creature(config, *, pwd=None) -> tuple[Terrarium, Creature]`：
  一次建好引擎 + 一隻生物。
- `async with Terrarium() as engine: ...`：`__aexit__` 會呼叫 `shutdown()`。

生物的 CRUD：

- `await engine.add_creature(config, *, graph=None, creature_id=None, llm=None, pwd=None, start=True, is_privileged=False, parent_creature_id=None, io="config", strict=True, session=None, name=None, tools=None, plugins=None) -> Creature`
  - `config`：路徑 / `@pkg/...` 參照、`AgentConfig`、`CreatureConfig`，
    或一個已建好的 `Creature` (對已建好的生物傳建構期 kwargs 會拋錯)。
  - `session`：持久化控制：傳路徑就在那個檔案建立 store；
    `True` 在預設 session 目錄建立；`False` 即使在 autosession 下
    也停用持久化；傳 `SessionStore` 直接掛上；`None` (預設)
    跟隨引擎 (autosession / 圖既有的 store / 不持久化)。
  - `is_privileged`：授予 `group_*` 圖變更工具
    (只升不降；不會把已建好的生物降權)。
  - `llm` / `io` / `strict` / `tools` / `plugins`：契約同
    `Agent.build`。
  - `name`：生成時的顯示名稱覆寫。
- `await engine.remove_creature(creature)`：停止 + 移除；可能觸發圖的自動分割。
- `engine.get_creature(creature_id) -> Creature` / `engine.list_creatures() -> list[Creature]`
- Pythonic 存取：`engine[id]`、`id in engine`、`for c in engine`、`len(engine)`。

頻道與拓樸 (圖層的所有頻道都是廣播，每個監聽者都收到每一則訊息)：

- `await engine.add_channel(graph, name, description="") -> ChannelInfo`
- `await engine.remove_channel(graph, name) -> TopologyDelta`：可能觸發自動分割。
- `await engine.connect(sender, receiver, *, channel=None) -> ConnectionResult`：
  跨圖連接會自動合併 (環境取聯集、session store 合併)。
- `await engine.disconnect(sender, receiver, *, channel=None) -> DisconnectionResult`：
  可能觸發自動分割 (兩側各拿到一份 store 副本)。
- `engine.environment(graph) -> Environment`：圖的即時環境公開
  handle (不認識的圖會拋 `KeyError`)。
- `engine.channel(graph, name)`：即時的廣播頻道 handle (或
  `None`)：`await ch.send(ChannelMessage(...))` 可以替一張圖播種，
  `ch.history` 可以觀察流量。
- `engine.get_graph(graph_id) -> GraphTopology` / `engine.list_graphs() -> list[GraphTopology]`
- `await engine.assign_root(creature, *, report_channel="report_to_root") -> RootAssignment`：
  把一隻生物升為其所屬圖的特權節點，並接好回報頻道。

配方與恢復進引擎：

- `await engine.apply_recipe(recipe, *, graph=None, pwd=None, llm=None, strict=True, session=None, creature_builder=None) -> GraphTopology`：
  `session` 遵循 `add_creature` 的契約，但會替整張圖建立一個
  terrarium 型的 store。
- `await engine.adopt_session(store, *, pwd=None, llm=None) -> str`：
  把已儲存的工作階段恢復進這個運行中的引擎；回傳新的
  `graph_id`。
- `await engine.attach_session(graph, store)`：把一個
  `SessionStore` 掛到圖上 (傳路徑則就地建立)。

生命週期與輸出接線：

- `await engine.start(creature)` / `await engine.stop(creature)` / `await engine.stop_graph(graph)`
- `await engine.shutdown()`：停止一切、關閉引擎建立的每個 store、
  終結所有訂閱者；冪等。
- `await engine.wire_output(creature, target) -> str` / `await engine.unwire_output(creature, edge_id) -> bool`
- `engine.list_output_wiring(creature) -> list[dict]`
- `await engine.wire_output_sink(creature, sink) -> str` / `await engine.unwire_output_sink(creature, sink_id) -> bool`

可觀測性：

- `engine.subscribe(filter=None) -> AsyncIterator[EngineEvent]`：
  訂閱者在呼叫當下就註冊 (`subscribe()` 到第一次 `await` 之間的
  事件會被緩衝)；跳出迴圈即解除註冊；`shutdown()` 會終結它。
- `engine.status()` (整體) / `engine.status(creature)` (單生物 dict)。

凡是接受 `CreatureRef` / `GraphRef` 的地方，傳物件本身或其字串 id 都行。

### 引擎事件

模組：`kohakuterrarium.terrarium.events` (從根目錄 re-export)。
引擎匯流排只承載**結構**事件；各生物的內容
(文字、工具活動) 走有型別的輪次介面。

- `EventKind` (`str` enum)：`CHANNEL_MESSAGE`、`TOPOLOGY_CHANGED`、
  `SESSION_KIND_CHANGED`、`CREATURE_ADDED`、`CREATURE_STARTED`、
  `CREATURE_STOPPED`、`OUTPUT_WIRE_ADDED`、`OUTPUT_WIRE_REMOVED`、
  `PARENT_LINK_CHANGED`。
- `EngineEvent`：`kind`、`creature_id`、`graph_id`、`channel`、
  `payload: dict`、`ts: float`。
- `EventFilter(kinds=None, creature_ids=None, graph_ids=None, channels=None)`：
  欄位以 AND 結合；`None` 表示「任意」；`matches(ev) -> bool`。
- `ConnectionResult`：`channel`、`trigger_id`、`delta_kind`
  (`"nothing"` | `"merge"`)、`graph_id`。
- `DisconnectionResult`：`channels: list[str]`、`delta_kind`
  (`"nothing"` | `"split"`)。

```python
import kohakuterrarium as kt

async with kt.Terrarium(session_dir="runs/") as engine:
    alice = await engine.add_creature("@kt-biome/creatures/general")
    bob = await engine.add_creature("@kt-biome/creatures/general")

    # 先訂閱、再變更：subscribe() 到第一次 await 之間發出的
    # 事件會被緩衝，一個都不會漏。
    events = engine.subscribe(kt.EventFilter(kinds={kt.EventKind.TOPOLOGY_CHANGED}))
    result = await engine.connect(alice, bob, channel="alice_to_bob")
    assert result.delta_kind == "merge"   # 兩張圖合而為一
    ev = await anext(events)
    print(ev.kind, ev.payload)
```

---

## Creature

模組：`kohakuterrarium.terrarium.creature_host` (re-export 為
`kohakuterrarium.Creature`)。引擎對一隻運行中 agent 的 handle。
由 `add_creature` / `with_creature` 回傳；不直接建構。

屬性：`creature_id`、`name`、`agent: Agent`、`graph_id`、
`listen_channels`、`send_channels`、`is_privileged`、
`parent_creature_id`、`is_running`、`status` (`"not_started"`、
`"error"`、`"busy"`、`"idle"`、`"stopped"` 之一)。

輪次驅動 (委派給底層的 `Agent`)：

- `await creature.run(content, **kwargs) -> TurnResult`：
  `timeout=` / `raise_on_error=` 語意同 `Agent.run`。
- `creature.run_stream(content, **kwargs) -> AsyncIterator[TurnEvent]`
- `creature.attach() -> AgentEventStream`：非破壞性、可多消費者的
  觀察者；連帶外 (out-of-band) 的輪次 (觸發器、頻道訊息)
  也會捕捉到：

  ```python
  async with creature.attach() as stream:
      async for ev in stream:
          ...
  ```

- `await creature.chat(message) -> AsyncIterator[str]`：純文字的
  語法糖 (注入 + 排空)；新程式碼請優先用 `run` / `run_stream`。
- `await creature.inject_input(message, *, source="chat")`：推入
  輸入但不消費輸出。

生命週期 / 內省：

- `await creature.start()` / `await creature.stop()`：冪等。
- `creature.get_status() -> dict`：model、provider、session_id、
  tools、subagents、pwd、channels、privilege。
- `creature.get_log_entries(last_n=20)` / `creature.get_log_text(last_n=10)`。

---

## 工作階段

### `SessionReader`：唯讀檢視

模組：`kohakuterrarium.session.reader` (re-export 為
`kohakuterrarium.SessionReader`)。針對 `.kohakutr` 檔案的一站式
唯讀介面。透過 `SessionStore.open_readonly` 開啟，讀取永遠不會
更新 `last_active` 或改動 `status`。支援 context manager。

- `SessionReader(path)`：檔案不存在拋 `FileNotFoundError`；
  `~` 會展開。
- 屬性：`path: Path`、`meta: dict` (session_id、config_type /
  config_path、status…)、`agents: list[str]`。
- `reader.events(agent=None) -> list[dict]`：append-only 事件
  日誌；`None` 會串接所有 agent 的事件。
- `reader.conversation(agent=None) -> list[dict]`：最終的對話
  快照 (OpenAI message dict)。
- `reader.channel_messages(channel) -> list[dict]`：一條 terrarium
  頻道的歷史。
- `reader.turns(agent=None) -> list[TurnView]`：從事件日誌
  重組出來的 live-branch 輪次。`TurnView`：`index`、`user_text`、
  `assistant_text`、`tool_calls: list[dict]`、`source`、`ts`。
- `reader.search(query, *, mode="fts", k=10, agent=None) -> list[SearchResult]`：
  全文 (或已建索引時的向量) 搜尋；未建索引的工作階段
  不會有結果。
- `reader.index() -> int`：臨時替 `search` 建 FTS 索引。
- `reader.close()`：或用 `with SessionReader(...) as r:`。

```python
import kohakuterrarium as kt

with kt.SessionReader("runs/student-42.kohakutr") as r:
    print(r.meta["status"], r.agents)
    for turn in r.turns():
        print(turn.user_text, "->", turn.assistant_text[:80])
```

### 引擎持有的持久化與恢復

持久化是引擎的功能，不需要手動的 `SessionStore` +
`init_meta` + `attach_session` 儀式：

- `Terrarium(session_dir="runs/")`：每張圖自動 autosession。
- `engine.add_creature(..., session="runs/x.kohakutr")`：單生物
  store (路徑 | `True` | `False` | `SessionStore` | `None`)。
- `engine.apply_recipe(..., session=...)`：整張配方圖一個 store。
- `await Terrarium.resume(store_or_path, *, pwd=None, llm=None)`：
  從已儲存的工作階段建一個新引擎。
- `await engine.adopt_session(store_or_path, *, pwd=None, llm=None) -> str`：
  恢復進運行中的引擎。
- `await engine.shutdown()` 會關閉引擎建立的每個 store
  (檔案不會再卡在 `status: "running"`)。

`SessionStore` (模組 `kohakuterrarium.session.store`，從根目錄
re-export) 仍是底層原語：

- `SessionStore(path)`：以讀寫模式開啟。
- `SessionStore.open_readonly(path)`：`close()` 永不改動 meta；
  所有列表 / 預覽消費者都該用這個。
- `store.close(update_status=True)`：冪等；`update_status=True`
  會把工作階段標為 paused 並更新 `last_active` (唯讀 store 上忽略)。
- 事件 / 對話 / 狀態 / 頻道 / job 的存取器，見
  [工作階段使用指南](../guides/sessions.md)。

---

## Packages

模組：`kohakuterrarium.packages`，惰性門面 (PEP 562)：名稱在第一次
屬性存取時才解析，import 本身很便宜。

安裝生命週期：

- `ensure(spec, *, deps="auto") -> str`：冪等安裝；已安裝就直接
  回傳套件名稱 (不做版本檢查，連鎖定版本的 spec 也一樣)。
  批次腳本開頭就該呼叫這個。
- `install_package_spec(spec, editable=False, name_override=None, *, deps="auto") -> str`：
  `@name` / `@name@version` / `@source/name` 透過市集解析；
  git URL 與本地路徑直接放行。
- `install_package(source, editable=False, name_override=None, ref=None, *, deps="auto") -> str`：
  git URL 或本地目錄。
- `update_package(name, *, deps="auto") -> str`：原地
  `git pull --ff-only`；鎖定版本的安裝會拒絕更新。
- `uninstall_package(name) -> bool`

`deps` 是 Python 依賴政策：`"auto"` 用 `sys.executable -m pip`
安裝 manifest 的 `python_dependencies` + `requirements.txt`；
`"never"` 跳過。不認識的政策或安裝失敗會拋 `PackageError`。

參照解析與佈局：

- `is_package_ref(path) -> bool`：是不是 `@pkg/...` 路徑參照？
- `resolve_package_path(ref) -> Path` / `resolve_any_path(path) -> Path`
- `packages_dir() -> Path`：作用中的套件目錄；尊重
  `KT_CONFIG_DIR` (預設 `~/.kohakuterrarium/packages`)。
- `get_package_root(name) -> Path | None` / `find_package_root_for_path(path) -> Path | None`
- `list_packages()` / `get_package_modules(...)`

Manifest 槽位解析器：`resolve_package_tool`、`resolve_package_io`、
`resolve_package_trigger`、`resolve_package_command`、
`resolve_package_user_command`、`resolve_package_prompt`、
`resolve_package_skills`、`get_package_framework_hints`。

re-export 的型別化錯誤：`PackageError`、`PackageRefError`、
`PackageNotInstalledError`、`PackagePathNotFoundError`。

```python
from kohakuterrarium import packages

packages.ensure("@kt-biome")                  # 冪等安裝
path = packages.resolve_package_path("@kt-biome/creatures/swe")
for pkg in packages.list_packages():
    print(pkg["name"], pkg["version"])
```

---

## Compose

模組：`kohakuterrarium.compose`。建在 agent 與普通 callable 之上的
pipeline 代數。匯出：`agent`、`factory`、`AgentRunnable`、
`AgentFactory`、`BaseRunnable`、`Runnable`、`Pure`、`pure`、`Sequence`、
`Product`、`Fallback`、`FailsWhen`、`Retry`、`Router`、
`PipelineIterator`。

Agent 包裝器：

- `await agent(config, *, engine=None, pwd=None, llm=None) -> AgentRunnable`：
  持久 agent (對話跨呼叫累積)；是 async context manager。
  `config` 可以是 `AgentConfig`、路徑或 `@pkg/...` 參照；`llm`
  遵循標準的選擇器文法。`engine=None` 時會建立一個私有的
  `Terrarium`，並隨 runnable 一起收掉；傳入共用引擎可以攤平
  啟動成本 (此時關閉只會移除該生物)。
- `factory(config, *, engine=None, pwd=None, llm=None) -> AgentFactory`：
  短命版：每次呼叫一個全新的 agent，用完即毀。

運算子 (都回傳 `BaseRunnable`)：

- `a >> b`：sequence；把輸出餵給下一個輸入。普通 callable
  自動包成 `Pure`；右邊放 dict 會變成 `Router`。
- `a & b`：平行 product；回傳 tuple。第一個失敗發生時，
  其餘還活著的兄弟會被**取消並等待完成**，例外才往上傳。
- `a | b`：fallback；發生例外時用原始輸入跑 `b`。
  連 fallback 也失敗時，主要例外會以 `__cause__` 鏈上去。
- `a * N`：最多重試 `N` 次 (立即重試)。
- `.retry(max_attempts, *, backoff=0.0, max_backoff=30.0)`：指數
  退避的重試 (每次睡眠加倍，有上限)。
- `.iterate(initial_input)`：async iterator，把輸出回灌成下一次的
  輸入 (`it.feed(value)` 可覆寫下一次輸入)。
- `.map(fn)` / `.contramap(fn)`：後置 / 前置轉換。
- `.fails_when(predicate)`：輸出符合 predicate 時拋
  `ValueError` (把「錯誤的成功」變成 fallback 的觸發點)。

```python
from kohakuterrarium.compose import agent, factory, pure

async with await agent("@kt-biome/creatures/swe", llm="fast") as swe:
    pipeline = swe >> pure(str.strip) >> (lambda t: f"Review:\n{t}")
    result = await (pipeline.retry(3, backoff=1.0))("Implement the feature")
```

---

## Validate

模組：`kohakuterrarium.validate` (re-export 為
`kohakuterrarium.validate`)。事前檢查，遇到第一個問題就拋出
型別化錯誤；`kt doctor` 是它的 CLI 包裝。

- `validate.config(path) -> AgentConfig`：agent 設定資料夾 /
  `@pkg` 參照能以完全嚴格模式解析。
- `validate.terrarium_config(path) -> TerrariumConfig`：terrarium
  配方能解析。
- `validate.llm(selector=None) -> str`：選擇器能解析且
  provider 能建構 (檢查憑證，不碰網路)；回傳標準的
  `provider/name[@variations]` 識別字。會拋
  `LLMNotConfiguredError` / `ValueError`。
- `validate.creature(path, *, llm_binding=None) -> ValidationReport`：
  完整的 dry-run 建構 (`strict=True`、headless IO、不啟動)。
  `ValidationReport`：`name`、`config_path`、`model_identifier`、
  `tools`、`plugins`、`subagents`。
- `await validate.ping(selector_or_provider=None, *, timeout=30.0) -> str`：
  唯一會碰網路的驗證器：跑一次最小的 LLM
  來回；回傳回覆文字。

```python
import kohakuterrarium as kt

kt.validate.config("./scoring-agent")
kt.validate.llm("openai/gpt-5@reasoning=high")
report = kt.validate.creature("./scoring-agent")
await kt.validate.ping("openai/gpt-5")
```

---

## Testing

模組：`kohakuterrarium.testing`。

- `ScriptedLLM(script: list[ScriptEntry] | list[str] | None = None)`
  (模組 `kohakuterrarium.testing.llm`)，確定性的 provider。
  **優先直接注入**：每個建構入口都接受實例的 `llm=`：
  `Agent.build(cfg, llm=ScriptedLLM([...]))`、
  `engine.add_creature(path, llm=...)`、`compose.agent(cfg, llm=...)`。
  斷言介面：`call_count`、`call_log`。
  `ScriptEntry(response, match=None, delay_per_chunk=0, chunk_size=10)`。
- `OutputRecorder` (`testing.output`)：捕捉 `chunks`、`writes`、
  `activities`、`all_text`。
- `EventRecorder` (`testing.events`)：`record`、`get_all`、
  `get_by_type`、`clear`。
- `TestAgentBuilder` (`testing.agent`)：流暢式的單元測試 harness
  (`with_llm_script`、`with_builtin_tools`、`build()`)。

`bootstrap.llm.create_llm_provider` +
`bootstrap.agent_init.create_llm_provider` 這個 monkeypatch 縫
只剩框架內部建構 agent 的路徑會用到 (設定檔、恢復、配方)。

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

## 另見

- 使用指南：[程式化使用](../guides/programmatic-usage.md)、
  [組合](../guides/composition.md)、
  [工作階段](../guides/sessions.md)、[套件](../guides/packages.md)、
  [studio](../guides/studio.md)、[自訂模組](../guides/custom-modules.md)、
  [外掛](../guides/plugins.md)。
- 教學：[第一次 Python 嵌入](../tutorials/first-python-embedding.md)。
- 參考：[cli](cli.md)、[http](http.md)、
  [設定檔](configuration.md)、[內建模組](builtins.md)、
  [外掛 hook](plugin-hooks.md)。
- 可執行腳本：[`examples/code/`](../../../examples/code/)，
  `batch_grading.py` 是批次模式的標準範例。
