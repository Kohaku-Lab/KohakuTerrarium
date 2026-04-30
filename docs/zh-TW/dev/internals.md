---
title: 內部結構
summary: 執行期怎麼組裝起來 — 事件佇列、控制器迴圈、executor、子代理管理、外掛包裝。
tags:
  - dev
  - internals
---

# 框架內部結構

執行期的實作地圖，分成三層。這份文件假設你會把 `src/kohakuterrarium/` 開著一起看。`../concepts/` 下的概念文件講的是「為什麼」；這裡講的是「在哪裡」。公開的 Python API 參考 (`plans/inventory-python-api.md`) 有簽名。

下面記錄 16 條流程，分組如下：

1. **代理執行期** — 生命週期、控制器迴圈、工具管線、子代理、觸發器、提示詞組裝、外掛。
2. **持久化與記憶** — 工作階段持久化、壓縮。
3. **多代理與 serving** — 生態瓶執行期、頻道、environment 與 session、serving 層、compose 代數、套件系統、MCP。

最後一節 [跨層不變條件](#跨層不變條件) 把適用於整個系統的規則整理在一起。

---

## 1. 代理執行期

### 1.1 代理生命週期 (單隻生物)

CLI 入口在 `cli/run.py:run_agent_cli()`。它會驗證設定檔路徑、挑一種 I/O 模式 (`cli` / `plain` / TUI)、選擇性地建一個 `SessionStore`、呼叫 `Agent.from_path(config_path, …)`，然後交給 `_run_agent_rich_cli()` 或 `agent.run()`。

`Agent.__init__` (`src/kohakuterrarium/core/agent.py:146`) 會依固定順序跑一連串 bootstrap：`_init_llm`、`_init_registry`、`_init_executor`、`_init_subagents`、`_init_output`、`_init_controller`、`_init_input`、`_init_user_commands`、`_init_triggers`。Mixin 的堆疊是 `AgentInitMixin` (`bootstrap/agent_init.py`) + `AgentHandlersMixin` (`core/agent_handlers.py`) + `AgentToolsMixin` (`core/agent_tools.py`)。

`await agent.start()` (`core/agent.py:186`) 會啟動輸入與輸出模組、在需要時接上 TUI callback、啟動 trigger manager、接完成事件的 callback、初始化 MCP (連線到 server 並把工具描述塞進 prompt)、初始化 `CompactManager`、載入外掛、發布 session info，最後啟動終止條件檢查器。

`await agent.run()` (`core/agent.py:684`) — 如果是 resume，就先重播 session 事件、還原觸發器、發出 startup trigger，然後進入主迴圈：`event = await input.get_input()` → `_process_event(event)`。`stop()` 會反向拆掉每個東西。代理擁有這些欄位：`llm`、`registry`、`executor`、`session`、`environment`、`subagent_manager`、`output_router`、`controller`、`input`、`trigger_manager`、`compact_manager`、`plugins`。

觀念層面的說明請看 [concepts/foundations/composing-an-agent.md](../concepts/foundations/composing-an-agent.md)。API 簽名：`plans/inventory-python-api.md` §Core Agent Lifecycle。

### 1.2 控制器迴圈與事件模型

一切都走 `TriggerEvent` (`core/events.py`)。欄位：`type, content, context, timestamp, job_id?, prompt_override?, stackable`。型別包含 `user_input`、`idle`、`timer`、`context_update`、`tool_complete`、`subagent_output`、`channel_message`、`monitor`、`error`、`startup`、`shutdown`。

事件佇列在 `core/controller.py:push_event` / `_collect_events` (第 252-299 行)。同一個 tick 收集到的可堆疊事件會合併成同一回合的 user message；不可堆疊事件會把當前批次切斷；超出這一批的部分存到 `_pending_events`，留到下一回合。

每回合的流程在 `agent_handlers.py:_run_controller_loop`：

1. 收集事件、建出這一回合的 context。
2. 組 messages、從 LLM 串流。
3. 串流進來時就邊剖析 tool / sub-agent / command 區塊。
4. 每個偵測到的就透過 `asyncio.create_task` 派發 — 工具是**在串流過程中**啟動的，不是等 LLM 講完才跑。
5. 串流結束後，`asyncio.gather` 等所有 direct 模式的完成。
6. 把合併後的回饋事件推回去、決定要不要再跑一回合。

細節參考 [concepts/modules/controller.md](../concepts/modules/controller.md) 與 [串流 parser 實作筆記](../concepts/impl-notes/stream-parser.md)。

### 1.3 工具執行管線

串流 parser (`parsing/`) 在偵測到設定 `tool_format` 裡的工具區塊時就會發出事件 — bracket (預設：`[/bash]@@command=ls\n[bash/]`)、XML (`<bash command="ls"></bash>`)、或 native (LLM provider 自己的 function-calling 包裝)。每個偵測到的工具都會透過 `executor.submit_from_event()` 變成一個 executor task。

Executor (`core/executor.py`) 把 `{job_id: asyncio.Task}` 存起來，並為每次呼叫建一個 `ToolContext`，裡面有 `working_dir`、`session`、`environment`、file guards、檔案讀取狀態表、job store、代理名稱。

三種模式：

- **Direct** — 在同一回合 await 完成。結果會批次塞進下一個控制器回饋事件。
- **Background** — 工具結果中設 `run_in_background=true`。Task 繼續跑，完成時發出未來的 `tool_complete` 事件。
- **Stateful** — 子代理這類長期存在的 handle。結果會存在 `jobs` 裡，用 `wait` 框架指令取回。

不變條件 (在 `agent_handlers.py` 與 `executor.py` 裡強制)：

- 工具在它的區塊被 parse 出來的那一刻就啟動 — 不是排隊等 LLM 講完。
- 同一回合的多個工具會平行跑 (`asyncio.gather`)。
- LLM 串流從不會因為工具執行而被卡住。

細節參考 [concepts/modules/tool.md](../concepts/modules/tool.md) 與 [impl-notes/stream-parser.md](../concepts/impl-notes/stream-parser.md)。API：`plans/inventory-python-api.md` §Tool Execution。

### 1.4 子代理派發

子代理由 `modules/subagent/manager.py:spawn` 產生。深度受 `config.max_subagent_depth` 限制。新的 `SubAgent` (`modules/subagent/base.py`) 共用父代理的 registry、LLM、工具格式，但維護自己的對話。

執行完成後會推一個 `subagent_output` 事件回父控制器。如果子代理設了 `output_to: external`，它的輸出會直接串到指定的輸出模組，而不是回到父代理。

互動式子代理 (`modules/subagent/interactive.py` + `interactive_mgr.py`) 會跨回合持續活著、吸收 context update，並可透過 `_feed_interactive()` 餵新的提示詞。它們會像頂層對話一樣存在 session store 裡。

細節參考 [concepts/modules/sub-agent.md](../concepts/modules/sub-agent.md)。

### 1.5 觸發器系統

`modules/trigger/base.py` 定義 `BaseTrigger`：一個產生 `TriggerEvent` 的 async generator。`to_resume_dict()` / `from_resume_dict()` 負責持久化。

內建的有 `TimerTrigger`、`IdleTrigger`、`ChannelTrigger`、`HTTPTrigger`、各種 monitor 觸發器。`TriggerManager` (`core/trigger_manager.py`) 拿一個 dict 存每個觸發器與它的背景 task。啟動時，它為每個觸發器開一個 task，不斷呼叫 `fire()` 並把事件推進代理的佇列。`CallableTriggerTool` (`modules/trigger/callable.py`) 把每個通用觸發器類別包起來，這樣代理就可以在執行期熱插拔觸發器。

Resume 時，觸發器的狀態會從 session store 的 `events[agent]:*` 列重新建回來。

細節參考 [concepts/modules/trigger.md](../concepts/modules/trigger.md)。

### 1.6 提示詞組裝

`prompt/aggregator.py:aggregate_system_prompt` 依下列順序組最終的 system prompt：

1. 基底 prompt (來自 `system.md` 的代理人格)，用 `render_template_safe` 走 Jinja2 渲染；未定義變數會 degrade 成空字串。
2. 工具說明。`skill_mode: dynamic` 時只放名稱 + 一行描述；`static` 時放完整說明。
3. 頻道拓樸提示，由 `terrarium/config.py:build_channel_topology_prompt` 在 creature 建起來時產生。
4. 依工具格式產生的框架提示 (bracket / xml / native)。
5. Named-output 模型 (怎麼寫到 `discord`、`tts` 等)。

各段之間以雙換行串接。`system.md` **絕不**能放工具清單、工具呼叫語法、或完整工具說明 — 那些要嘛是自動組進來，要嘛用 `info` 框架指令按需載入。

細節參考 [impl-notes/prompt-aggregation.md](../concepts/impl-notes/prompt-aggregation.md)。

### 1.7 外掛系統

兩套獨立的系統：

**Prompt 外掛** (`prompt/plugins.py`) 在 aggregate 時貢獻內容到 system prompt。按 priority 排序。內建包含 `ToolList`、`FrameworkHints`、`EnvInfo`、`ProjectInstructions`。

**Lifecycle 外掛** (`bootstrap/plugins.py` + `modules/plugin/` 底下的 manager) 掛進代理的事件流。`PluginManager.notify(hook, **kwargs)` 會 await 每個啟用的外掛對應方法。`pre_*` hook 丟出 `PluginBlockError` 會中止該操作。所有 hook 列在內建清單裡。

套件在 `kohaku.yaml` 宣告外掛；列在 `config.plugins[]` 裡的外掛會在代理啟動時載入。

細節參考 [concepts/modules/plugin.md](../concepts/modules/plugin.md)。

---

## 2. 持久化與記憶

### 2.1 工作階段持久化

一個工作階段是一個 `.kohakutr` 檔案，底下是 KohakuVault (SQLite)。`session/store.py` 裡的資料表：`meta`、`state`、`events` (append-only)、`channels` (訊息歷史)、`subagents` (銷毀前的快照)、`jobs`、`conversation` (每個代理最新的快照)、`fts` (全文索引)。

寫入發生的時機：

- 每次工具呼叫、文字 chunk、觸發器觸發、token 用量發出 (事件日誌)，
- 每回合結束時 (conversation 快照)，
- 草稿區寫入，
- 頻道 send。

Resume (`session/resume.py`)：載入 `meta`、每個代理的 conversation 快照、還原 scratchpad/state、還原觸發器、把事件重播給輸出模組 (拿來當 scrollback)、把子代理的對話再接回來。不可 resume 的狀態 (開著的檔案、LLM 連線、TUI、asyncio task) 從 config 重建。

`session/memory.py` + `session/embedding.py` 在事件日誌上提供 FTS5 與向量搜尋。Embedding 提供者：`model2vec`、`sentence-transformer`、`api`。向量跟事件區塊並排存，以便做混合搜尋。

細節參考 [impl-notes/session-persistence.md](../concepts/impl-notes/session-persistence.md)。

### 2.2 上下文壓縮

`core/compact.py:CompactManager` 在每回合結束後執行。`should_compact(prompt_tokens)` 判斷 prompt tokens 有沒有超過 `max_context` 的 80% (可由 `compact.threshold` 與 `compact.max_tokens` 設定)。觸發時它會發一個 `compact_start` activity 事件、開一個背景 task 跑 summariser LLM (主 LLM，或設了 `compact_model` 時用那個)，然後在回合**之間**原子地把摘要塞進對話。活動區 — 最後 `keep_recent_turns` 個回合 — 永遠不會被摘要掉。

原子替換的設計讓控制器永遠不會在回合中間看到訊息突然消失。完整推理請看 [impl-notes/non-blocking-compaction.md](../concepts/impl-notes/non-blocking-compaction.md)。

---

## 3. 多代理與 serving

### 3.1 生態瓶引擎

`terrarium/engine.py:Terrarium` 是執行期引擎 — 每行程一個，托管所有生物。引擎擁有：

- `_topology: TopologyState` — 純資料 graph 模型 (`terrarium/topology.py`)，記錄哪些生物共用哪個 graph、哪些頻道存在、誰 listen / send。
- `_creatures: dict[str, Creature]` — 運行中的 wrapper (`terrarium/creature_host.py`)。
- `_environments: dict[str, Environment]` — 每個 graph 一份；持有 `shared_channels`。
- `_session_stores: dict[str, SessionStore]` — 每個掛著 store 的 graph 一份。
- `_subscribers: list[_Subscriber]` — `EngineEvent` 發布訂閱。

獨立 agent 是 1-creature graph；recipe 是用頻道連起來的 connected graph。`Terrarium.with_creature(config)` 是獨立 agent 的捷徑；`Terrarium.from_recipe(recipe)` 透過 `terrarium/recipe.py:apply_recipe` 走完一份 `TerrariumConfig` (宣告頻道、為每隻生物加一條 direct channel、若有 root 加 `report_to_root`、接 listen / send 邊、啟動一切)。生物除了透過頻道和 (選擇性的) 嵌進 system prompt 的拓樸提示外，不會知道自己處於生態瓶中。

**頻道注入**。當一隻生物加入了一個有它要 listen 的頻道的 graph，`terrarium/channels.py:inject_channel_trigger` 會往它的 `trigger_manager` 加一個 `ChannelTrigger`。這是 layer 模型裡唯一被允許的向下注入：在 graph 裡的生物 *會* 知道自己有同伴 (它得知道)，但只透過引擎給它的 handle。獨立生物不會有任何注入。

**熱插拔**。拓樸可以在執行期變更。`Terrarium.connect(a, b, channel=...)` 可能合併兩個 graph (environment 取聯集，頻道彙集，掛著的 session store 透過 `terrarium/session_coord.py:apply_merge` 合成一份)。`Terrarium.disconnect` 可能拆 graph (parent session 透過 `apply_split` 複製到兩邊)。`terrarium/topology.py` 裡的純資料拓樸 mutator 回傳 `TopologyDelta`，其 `kind in {"nothing", "merge", "split"}` 驅動這些 live 更新。

**Session 合併 / 分裂**。Session 的單位是 graph 的連通分量。不影響 graph 成員的拓樸變更會沿用既有的 store。`terrarium/session_coord.py` 實作兩條分支並發出 `SESSION_FORKED` / `TOPOLOGY_CHANGED` 事件。

**事件 bus**。`terrarium/events.py:EngineEvent` 是統一的可觀測面。kind 涵蓋 text chunk、頻道訊息、拓樸變更、session fork、creature 生命週期、processing start / end、error。`Terrarium.subscribe(filter)` 回傳與 `EventFilter` 匹配的事件 async iterator。每個訂閱者各有一個 queue；取消 iterator 會自動撤銷訂閱。

舊版 `terrarium/runtime.py:TerrariumRuntime` 與 `serving/manager.py:KohakuManager` 在過渡期間還留在硬碟上 — 較舊的 HTTP route 與 CLI 還會用它們。`api/deps.py` 現在同時暴露 `get_engine()` (新) 與 `get_manager()` (舊) 這兩個 singleton；route 會一條一條切過去。

細節參考 [concepts/multi-agent/terrarium.md](../concepts/multi-agent/terrarium.md) 與 [concepts/multi-agent/root-agent.md](../concepts/multi-agent/root-agent.md)。

### 3.2 頻道

`core/channel.py` 定義兩個原語：

- `SubAgentChannel` — queue 結構，每則訊息一個消費者，FIFO。支援 `send` / `receive` / `try_receive`。
- `AgentChannel` — broadcast。每個訂閱者透過 `ChannelSubscription` 持有自己的 queue。晚來的訂閱者拿不到舊訊息。

頻道存在 `ChannelRegistry` 裡，位置是 `environment.shared_channels` (生態瓶全域) 或 `session.channels` (生物私有)。自動建出來的頻道：每隻生物的 queue、以及 `report_to_root`。`ChannelTrigger` 把一條頻道接到代理的事件流，把進來的訊息轉成 `channel_message` 事件。

細節參考 [concepts/modules/channel.md](../concepts/modules/channel.md)。

### 3.3 Environment 與 Session

- `Environment` (`core/environment.py`) 持有生態瓶全域的狀態：`shared_channels`、選用的共享 context dict、session 記帳。
- `Session` (`core/session.py`) 持有每隻生物自己的狀態：私有頻道 registry (或 alias 到 environment 的)、`scratchpad`、`tui` 參照、`extra` dict。

每隻代理一個 session。在生態瓶裡，environment 是全部生物共享的；session 是私有的。生物永遠不碰別人的 session — 共享狀態一律走 `environment.shared_channels`。

細節參考 [concepts/modules/session-and-environment.md](../concepts/modules/session-and-environment.md)。

### 3.4 Serving 層

`serving/manager.py:KohakuManager` 替 transport 層的程式碼建 `AgentSession` 或 `TerrariumSession` wrapper。`AgentSession.send_input` 把 user-input 事件推進代理，並 yield 出輸出 router 的事件，以 JSON dict 的形式：`text`、`tool_start`、`tool_complete`、`activity`、`token_usage`、`compact_*`、`job_update` 等等。

`api/` 下的 HTTP/WS API 跟任何 Python 嵌入都走這層，不會直接碰 `Agent` 內部。

API 簽名：`plans/inventory-python-api.md` §Serving。

### 3.5 Compose 代數內部

`compose/core.py` 定義 `BaseRunnable.run(input)` 與 `__call__(input)`。運算子 overload 包成 composition：

- `__rshift__` (`>>`) → `Sequence`；`>>` 右邊是 dict 時會變成 `Router`。
- `__and__` (`&`) → `Product` (平行跑)。
- `__or__` (`|`) → `Fallback`。
- `__mul__` (`*`) → `Retry`。

純 callable 會自動包成 `Pure`。`agent()` 建一個持久的 `AgentRunnable` (跨呼叫共用對話)；`factory()` 建一個 `AgentFactory`，每次呼叫都產生新的代理。`iterate(async_iter)` 會走訪一個 async 來源，每個元素都 await 整條管線。`effects.Effects()` 記錄綁在管線上的副作用 (`pipeline.effects.get_all()`)。

細節參考 [concepts/python-native/composition-algebra.md](../concepts/python-native/composition-algebra.md)。

### 3.6 套件 / extension 系統

安裝：`packages.py:install_package(source, editable=False)`。三種模式 — git clone、本地複製、或用 `.link` 指標做 editable。落點：`~/.kohakuterrarium/packages/<name>/`。

解析：`resolve_package_path("@<pkg>/<sub>")` 會跟 `.link` 指標走、或沿資料夾走。設定載入器 (例如 `base_config: "@pkg/creatures/…"`) 與 CLI 指令都用這個。

`kohaku.yaml` manifest 宣告一個套件的 `creatures`、`terrariums`、`tools`、`plugins`、`llm_presets`、`python_dependencies`。

術語：

- **Extension** — 套件提供的一個 Python 模組 (tool / plugin / LLM preset)。
- **Plugin** — 一個 lifecycle-hook 實作。
- **Package** — 可安裝的單位，裡面可以裝上面任何東西加上 config。

### 3.7 MCP 整合

`mcp/client.py:MCPClientManager.connect(cfg)` 開一個 stdio 或 HTTP MCP session、呼叫 `session.initialize()`、透過 `list_tools` 探索工具、結果快取到 `self._servers[name]`。`disconnect(name)` 清理。

代理啟動時，MCP 連線完成之後，代理會呼叫 `_inject_mcp_tools_into_prompt()`，建一段「Available MCP Tools」的 markdown，列出每個 server、工具、參數。代理透過內建的 `mcp_call(server, tool, args)` meta 工具呼叫 MCP 工具，加上 `mcp_list` / `mcp_connect` / `mcp_disconnect`。

Transport：`stdio` (子行程走 stdin/stdout) 與 `streamable_http plus legacy http/sse`。

---

## 跨層不變條件

下面這些規則跨越上面的流程都適用。違反任何一條都會弄壞某個東西。

- **一隻代理一個 `_processing_lock`。** 同時只會跑一個 LLM 回合。在 `agent_handlers.py` 強制。
- **工具平行派發。** 同一回合偵測到的所有工具會一起開始跑。一個一個派發是 bug。
- **非阻塞壓縮。** 對話的替換是原子的，而且只發生在回合之間。控制器絕對不會在 LLM 呼叫中間看到訊息消失。
- **事件可堆疊性。** 一連串同類的可堆疊事件會合併成一則 user message；不可堆疊事件永遠會切斷批次。
- **Backpressure。** 佇列滿時，`controller.push_event` 會 await。失控的觸發器會被節流，而不是丟事件。
- **生態瓶的 session 隔離。** 生物絕不碰別人的 session。共享狀態一律走 `environment.shared_channels`，沒有例外。

如果你改了上面任何一條流程，請回頭檢查這些不變條件。清單 (`plans/inventory-runtime.md`) 是唯一可信來源，改 code 時請一起更新。

TODO：清單目前還沒完整涵蓋 `compose/` 套件 (除了上面有的)、`commands/` 框架指令執行期、以及輸出路由器的狀態機。下次 inventory pass 時補齊。
