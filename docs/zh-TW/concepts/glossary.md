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

具名的廣播管道。每一個訂閱者都會收到任何送出的訊息 ——
[圖](#graph--圖)層級沒有 queue / consume 的語意。頻道活在生物的私有
session 或圖的共用 environment 裡。一個 `send_message` 工具加上
`ChannelTrigger` 就是跨生物通訊的方式。完整說明：[頻道](modules/channel.md)。

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

## Studio

[Terrarium](#terrarium--生態瓶) 引擎之上的管理框架。一個 Python class
（`kohakuterrarium.Studio`），透過六個命名空間 ——`catalog`、`identity`、
`sessions`、`persistence`、`editors`、`attach`—— 暴露每個 UI 與自動化腳
本本來都得自己重做的事：套件搜尋、LLM 設定檔與 API key、執行中 session
的生命週期、保存的 session 的 resume / fork / export、工作區生物 / 模組
CRUD、attach policy 公告。網頁 dashboard、桌面 app、`kt` CLI 與你自己的
Python 程式碼全都委派給 Studio，而不是各自重新實作。Studio **不是** UI；
dashboard 是它眾多 adapter 之一。完整說明：[Studio](studio.md)。

## Terrarium / 生態瓶

托管行程內所有執行中生物的執行期引擎。一隻獨立 agent 就是引擎裡的
1-creature [圖](#graph--圖)；多生物團隊則是用頻道連起來的連通圖。引擎
擁有 creature CRUD、channel CRUD、輸出接線、[熱插拔](#hot-plug--熱插拔)、
以及在圖變更時跟著走的拓樸 + session 記帳（[自動分裂 / 自動合併](#auto-split--auto-merge--自動分裂--自動合併)）。
它**不**執行 LLM、也沒有自己的推理迴圈 —— 那些都活在生物裡。它**真正
決定**的是結構：哪些生物共享一個連通分量、哪個 session store 撐住哪個
圖、每個回合結束的輸出該送往何處。生物不知道自己在生態瓶裡；同樣的設
定仍然可以獨立執行。完整說明：[生態瓶](multi-agent/terrarium.md)。

## Recipe / 配方

把一個全新的 [Terrarium](#terrarium--生態瓶) 引擎填入特定多生物設定的
YAML 設定檔。引擎本身永遠存在；配方只是「加入這些生物、宣告這些頻道、
接好這些邊、可選地把一隻提升為 [root](#root--root-關鍵字)」的指令序列。
配方在 resume 時是真理來源 —— 當一份保存的多生物 session 被開起來時，
引擎會從 session metadata 裡記的 recipe 路徑重建拓樸，而**不是**用一
份凍結的圖快照。

## Graph / 圖

[Terrarium](#terrarium--生態瓶) 引擎裡的一個連通分量：透過頻道連起來
的一組生物。兩隻無關的生物各自處於不同的圖；在它們之間畫一條頻道會把
兩個圖合併（並合併兩邊的 session 歷史）。把兩半之間最後一條頻道拔掉
則會把圖分裂（並把歷史複製到兩邊）。圖是 session 的單位 —— 同一個圖裡
的生物共用同一個 `.kohakutr` 檔案。完整說明：[生態瓶](multi-agent/terrarium.md)。

## Root / Root 關鍵字

terrarium 配方裡的 `root:` 關鍵字，用來指明哪一個節點是圖中代表使用者
的[特權節點](#privileged-node--特權節點)。配方載入器會把它標記為特
權、開啟一條 `report_to_root` 頻道（其他每隻生物都被接線為可送往該頻
道）、讓它監聽其他每一條頻道，並把它掛載為面向使用者的介面（TUI / CLI /
網頁）。「root」是設定上的慣例，不是另一種執行期型別 —— 在執行期它就
是一隻帶有標準面向使用者接線的特權節點。完整說明：[特權節點](multi-agent/privileged-node.md)。

## Privileged node / 特權節點

被授予[群組工具](#group-tools--群組工具)、可以變更所屬圖的生物：生成
或移除其他生物、繪製或刪除頻道、啟動或停止成員。被
[`root:`](#root--root-關鍵字)指定的節點預設就是特權；配方可以在其他成
員上 inline 標記為特權（`privileged: true`）；引擎也接受在
add_creature 時傳入 `is_privileged=True`。透過工具生成的工人生物（經
由 `group_add_node`）**不是**特權 —— 工人沒被顯式提權前不能再分叉同
儕。特權是執行期生物 handle 的屬性，與底層 agent 設定無關 —— 同一份
設定可以在某個 terrarium 裡以特權身份執行、在另一個裡以非特權身份執
行。完整說明：[特權節點](multi-agent/privileged-node.md)。

## Group tools / 群組工具

一組內建工具（`group_add_node`、`group_remove_node`、`group_start_node`、
`group_stop_node`、`group_channel`、`group_wire`、`group_status`、
`group_send`），用於從內部變更或檢查一個[圖](#graph--圖)。僅註冊在
[特權節點](#privileged-node--特權節點)上。它們合在一起就是執行期的
「圖編輯器」，讓 LLM 驅動的特權節點在執行中演化團隊 —— 每一次變更都
會發出 `EngineEvent`，讓 observer 與執行期提示詞保持同步。完整說明：
[builtins 參考](../reference/builtins.md)。

## Hot-plug / 熱插拔

在執行中的 [Terrarium](#terrarium--生態瓶) 裡加入或移除生物、頻道、
接線邊，**不需要重啟**。引擎處理記帳：新成員的觸發器注入與持久化綁定；
被移除的成員的觸發器拆除以及任何
[自動分裂](#auto-split--auto-merge--自動分裂--自動合併)。可以透過命令
式 API（`Terrarium.add_creature`、`connect`、`disconnect`）或由特權節
點呼叫的[群組工具](#group-tools--群組工具)使用。

## Auto-split / Auto-merge / 自動分裂 / 自動合併

引擎對會影響連通性的拓樸變更的反應。當一次 connect 跨越兩個圖時，引擎
會合併它們 —— 聯集 environment、把兩個 session store 複製到一個合併後
的 store（meta 裡的 `parent_session_ids` 記下血脈）。當一次 disconnect
或生物 / 頻道的移除切斷了兩半之間的唯一路徑時，引擎會分裂圖 —— 為每
一邊分配新的 environment、把頻道觸發器對著新的 env 重新注入、把
session store 複製到每一邊。所有記帳都自動完成；observer 在
`EngineEvent` 裡看到新的圖 id 出現。

## Package / 套件

一個可安裝的資料夾，裝著生物、生態瓶、自訂工具、外掛、LLM 預設、Python 相依，並以 `kohaku.yaml` manifest 描述。透過 `kt install` 安裝到 `~/.kohakuterrarium/packages/`。在設定和 CLI 裡用 `@<pkg>/<path>` 語法參照。完整說明：[套件使用指南](../guides/packages.md)。

## kt-biome

官方 out-of-the-box 套件，內含好用的生物、生態瓶、範例外掛。不是核心框架的一部分 — 是展示 + 起步點。請見 [github.com/Kohaku-Lab/kt-biome](https://github.com/Kohaku-Lab/kt-biome)。

## Compose 代數

一組小運算子 (`>>` sequence、`&` parallel、`|` fallback、`*N` retry、`.iterate` async loop)，用來在 Python 裡把 agent 串成 pipeline。這只是一層人體工學糖衣，核心事實是 agent 本來就是一等公民的 async Python 值。完整說明：[compose 代數](python-native/composition-algebra.md)。

## MCP

Model Context Protocol — 一個把工具暴露給 LLM 的外部協定。KohakuTerrarium 透過 stdio、streamable HTTP 或舊式 HTTP/SSE 連到 MCP 伺服器、探索它們的工具、再用 meta 工具 (`mcp_call`、`mcp_list`…) 把它們暴露給 LLM。完整說明：[MCP 使用指南](../guides/mcp.md)。

## Compaction / 壓縮

當上下文快滿時，把舊的對話回合摘要掉的背景流程。非阻塞：控制器在 summariser 工作時繼續執行，切換動作在回合之間原子地完成。完整說明：[非阻塞壓縮](impl-notes/non-blocking-compaction.md)。

## 延伸閱讀

- [核心概念首頁](README.md) — 完整章節地圖。
- [什麼是 agent](foundations/what-is-an-agent.md) — 把上面多數術語放在同一個脈絡裡介紹。
- [邊界](boundaries.md) — 上面任何一項何時可以忽略。
