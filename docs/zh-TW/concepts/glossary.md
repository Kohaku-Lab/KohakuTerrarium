---
title: 詞彙表
summary: 文件裡用到的術語的白話解釋。
tags:
  - concepts
  - glossary
  - reference
---

# 詞彙表

這一頁是給你在文件中間看到某個詞卡住時的查找表。每一條都指向完整的概念文件。

## Creature / 生物

一個獨立的 agent。KohakuTerrarium 的第一等抽象。一隻生物有控制器、工具、觸發器、(通常有的) 子代理、輸入、輸出、工作階段、以及選用的外掛。它可以單獨執行，也可以放進生態瓶裡。完整說明：[什麼是 agent](foundations/what-is-an-agent.md)。

## Controller / 控制器

生物內部的推理迴圈。從事件佇列取事件、請 LLM 回應、派發回傳的工具與子代理呼叫、把它們的結果當成新事件餵回去、決定是否繼續。它不是「大腦」 — LLM 才是大腦；控制器是讓 LLM 在時間軸上運作的那層迴圈。完整說明：[控制器](modules/controller.md)。

## Input / 輸入

外界把使用者訊息交給生物的方式。實際上就是一種特殊的觸發器 — 標記為 `user_input` 的那種。內建的有 CLI、TUI、以及 `none` (純觸發器驅動的生物)；音訊/ASR 由 opt-in 的自訂模組提供。完整說明：[輸入](modules/input.md)。

## Trigger / 觸發器

任何不需要使用者輸入就可以把控制器叫醒的東西。計時器、idle 偵測、webhook、頻道 listener、監控條件都是觸發器。每個觸發器會把 `TriggerEvent` 推到生物的事件佇列。完整說明：[觸發器](modules/trigger.md)。

## Output / 輸出

生物向外界說話的方式。一個路由器接收控制器產生的一切 (文字 chunk、工具活動、token 用量)，然後分發到一個或多個 sink — stdout、TTS、Discord、檔案。完整說明：[輸出](modules/output.md)。

## Tool / 工具

LLM 可以帶參數呼叫的具名能力。shell 指令、檔案編輯、網頁搜尋。工具也可以是訊息匯流排、狀態 handle、或一個巢狀 agent — 框架不管呼叫之後背後做什麼。完整說明：[工具](modules/tool.md)。

## Sub-agent / 子代理

由父生物為某個有界任務派生出來的巢狀生物。有自己的上下文、(通常) 是父代理工具的子集。概念上也是一種工具 — 從 LLM 的角度看，呼叫子代理和呼叫任何工具沒有兩樣。完整說明：[子代理](modules/sub-agent.md)。

## TriggerEvent

所有外部訊號抵達生物時共用的那一個信封。使用者輸入、計時器觸發、工具完成、頻道訊息、子代理輸出 — 全部都變成 `TriggerEvent(type=..., content=..., ...)`。一個信封、一條程式碼路徑。完整說明：[組合一個 agent](foundations/composing-an-agent.md)。

## Channel / 頻道

具名的訊息管道。兩種類型：**queue** (FIFO，每則訊息只有一個消費者收到) 與 **broadcast** (每個訂閱者都收到每則訊息)。頻道活在生物的私有 session 或 terrarium 的共用 environment 裡。一個 `send_message` 工具加上 `ChannelTrigger` 就是跨生物通訊的方式。完整說明：[頻道](modules/channel.md)。

## Output wiring / 輸出接線

框架層級的設定，把生物回合結束的輸出自動送到指定的目標。在生物設定裡用 `output_wiring:` 宣告；每一個回合結束時，框架把一個 `creature_output` TriggerEvent 直接推進指定的目標生物的事件佇列。不需要呼叫 `send_message`、也不經過頻道 — 它走的是和其他觸發器一樣的事件路徑。**確定性的 pipeline 邊**用輸出接線；條件性、廣播、觀察類的流量留給頻道。完整說明：[生態瓶使用指南 — 輸出接線](../guides/terrariums.md#output-wiring)。

## creature_output (事件型別)

框架在每個 `output_wiring` entry 的回合結束時發出的 TriggerEvent 型別。context 帶著 `source`、`target`、`with_content`、`source_event_type`、以及每個來源生物獨立累加的 `turn_index`。目標生物上註冊的外掛會透過正常的 `on_event` hook 收到它。

## Session / 工作階段

每隻生物的**私有**狀態：scratchpad、私有頻道、TUI 參照、正在跑的 job 的 store。序列化到 `.kohakutr` 檔案。一個生物實例對應一個工作階段。完整說明：[工作階段與環境](modules/session-and-environment.md)。

## Environment / 環境

整個生態瓶**共享**的狀態：共用頻道 registry 加上選用的共用 context dict。生物預設私有、共享需明確 opt-in — 它們只看得到自己明確 listen 的共用頻道。完整說明：[工作階段與環境](modules/session-and-environment.md)。

## Scratchpad / 草稿區

生物 session 裡的 key-value store。跨回合存活；用 `scratchpad` 工具讀寫。適合當作工作記憶，或合作中的工具之間的會合點。

## Plugin / 外掛

修改模組之間**連接方式**的程式碼 — 不是 fork 某個模組。兩種：**prompt 外掛** (為 system prompt 貢獻內容) 與 **lifecycle 外掛** (掛在 `pre_llm_call`、`post_tool_execute` 這類 hook)。`pre_*` hook 可以拋 `PluginBlockError` 來中止操作。完整說明：[外掛](modules/plugin.md)。

## Skill mode / Skill 模式

設定旋鈕 (`skill_mode: dynamic | static`)，決定 system prompt 要不要一開始就放上完整的工具說明 (`static`，比較大) 或只放名字加一行描述、等 agent 需要時用 `info` 框架指令擴展 (`dynamic`，比較小)。純粹的取捨；其他行為沒變。完整說明：[提示詞組合](impl-notes/prompt-aggregation.md)。

## Framework commands / 框架指令

LLM 在一個回合中可以發出的行內指示，用來和框架溝通而不發動一次完整的工具 round-trip。它們和工具呼叫**用同一套語法家族** — 生物設定的 `tool_format` (bracket / XML / native) 是哪一種，它們就長什麼樣。「指令」這個詞指的是**意圖** (和框架對話，而不是執行工具)，不是說它有另一套語法。

預設 bracket 格式裡：

- `[/info]工具或子代理名[info/]` — 按需載入某個工具或子代理的完整文件。
- `[/read_job]job_id[read_job/]` — 讀取執行中或已完成的背景 job 輸出 (body 支援 `--lines N` 與 `--offset M` 旗標)。
- `[/jobs][jobs/]` — 列出目前正在執行的背景 job (附 id)。
- `[/wait]job_id[wait/]` — 阻塞目前回合直到某個背景 job 完成。

指令名和工具名共用命名空間；「讀取 job 輸出」之所以叫 `read_job` 而不是 `read`，是為了避免和 `read` 檔案讀取工具撞名。

## Terrarium / 生態瓶

同時執行多隻生物的純接線層。沒有 LLM、不做決策 — 只有執行期、一組共用頻道、和輸出接線的管線。生物不知道自己在生態瓶裡；它們仍然可以獨立執行。我們把它當作橫向多代理的一種提案架構 — 隨著模式浮現還在演化。ROADMAP 裡有已釋出與尚在探索的部分。完整說明：[生態瓶](multi-agent/terrarium.md)。

## Root agent / Root 代理

站在生態瓶**外面**、在生態瓶裡代表使用者的生物。結構上就是一般的生物；它之所以叫「root」是因為它會自動拿到生態瓶管理工具組，而且它是使用者的對口。完整說明：[Root 代理](multi-agent/root-agent.md)。

## Package / 套件

一個可安裝的資料夾，裝著生物、生態瓶、自訂工具、外掛、LLM 預設、Python 相依，並以 `kohaku.yaml` manifest 描述。透過 `kt install` 安裝到 `~/.kohakuterrarium/packages/`。在設定和 CLI 裡用 `@<pkg>/<path>` 語法參照。完整說明：[套件使用指南](../guides/packages.md)。

## kt-biome

官方 out-of-the-box 套件，內含好用的生物、生態瓶、範例外掛。不是核心框架的一部分 — 是展示 + 起步點。請見 [github.com/Kohaku-Lab/kt-biome](https://github.com/Kohaku-Lab/kt-biome)。

## Compose 代數

一組小運算子 (`>>` sequence、`&` parallel、`|` fallback、`*N` retry、`.iterate` async loop)，用來在 Python 裡把 agent 串成 pipeline。這只是一層人體工學糖衣，核心事實是 agent 本來就是一等公民的 async Python 值。完整說明：[compose 代數](python-native/composition-algebra.md)。

## MCP

Model Context Protocol — 一個把工具暴露給 LLM 的外部協定。KohakuTerrarium 透過 stdio 或 HTTP/SSE 連到 MCP 伺服器、探索它們的工具、再用 meta 工具 (`mcp_call`、`mcp_list`…) 把它們暴露給 LLM。完整說明：[MCP 使用指南](../guides/mcp.md)。

## Compaction / 壓縮

當上下文快滿時，把舊的對話回合摘要掉的背景流程。非阻塞：控制器在 summariser 工作時繼續執行，切換動作在回合之間原子地完成。完整說明：[非阻塞壓縮](impl-notes/non-blocking-compaction.md)。

## 延伸閱讀

- [核心概念首頁](README.md) — 完整章節地圖。
- [什麼是 agent](foundations/what-is-an-agent.md) — 把上面多數術語放在同一個脈絡裡介紹。
- [邊界](boundaries.md) — 上面任何一項何時可以忽略。
