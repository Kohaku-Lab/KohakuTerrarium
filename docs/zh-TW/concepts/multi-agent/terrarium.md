---
title: 生態瓶
summary: 橫向接線層——頻道處理選用流量、輸出接線處理確定性邊，再疊上熱插拔與觀察。
tags:
  - concepts
  - multi-agent
  - terrarium
---

# 生態瓶

## 它是什麼

**生態瓶 (terrarium)** 是托管行程內所有執行中生物的執行期引擎。它自己
不執行 LLM、也沒有自己的推理迴圈 —— LLM 與推理都活在它所托管的生物
裡。它**真正擁有**的是*結構*：哪些生物共享一個連通分量、它們之間有哪
些頻道、每個回合結束的輸出送往何處、哪個 session store 撐住哪個圖，
以及拓樸變更時跟著走的記帳。每個行程一個引擎；同一個引擎裡可以共存
多個互不相連的圖。

獨立 agent 是引擎裡的 **1-creature 圖**。多生物團隊則是用頻道連起來的
**連通圖**。你以前稱為「生態瓶」的設定檔，現在是一份 **recipe** ——
「加這些生物、宣告這些頻道、接這些邊」的序列，由引擎執行。引擎本身始
終存在；recipe 只是把它填滿。

引擎做這些事：

1. **生物 CRUD** — 加、移除、列出、檢視。
2. **頻道 CRUD** — 宣告、連接生物、斷開。
3. **輸出接線** — 回合結束的事件送進指定目標。
4. **生命週期** — start、stop、shutdown。
5. **拓樸記帳** — 連通性變化時圖的自動分裂 / 自動合併。
6. **Session 合併 / 分裂**，跟著拓樸記帳走。
7. **可觀測性** — 一切可觀察的事都進 `EngineEvent` stream。

這就是它全部的契約。它一概不涉及 LLM；它做的全是引擎為內部生物代勞
的、確定性的結構性工作。

### 心智模型：單一團隊、單一 Root

入門時——多數使用者後續也維持這個畫面——你會從一支團隊加上一隻面向使用者的 Root 生物看起：

```
  +---------+       +---------------------------+
  |  User   |<----->|     特權節點              |
  +---------+       |   (group tools, TUI)      |
                    +---------------------------+
                          |               ^
            sends tasks   |               |  observes
                          v               |
                    +---------------------------+
                    |     Terrarium engine      |
                    |  (拓樸、頻道、session、    |
                    |   no LLM)                 |
                    +-------+----------+--------+
                    |  swe  | reviewer |  ....  |
                    +-------+----------+--------+
```

這是 **per-graph 視角**：一隻特權節點和它所管理的團隊並存於同一張圖，
引擎在底下持有頻道與拓樸。這是框架原生提供、也是大多數 recipe 編碼的
樣子。如果你只需要這些，看到這就夠了——本節剩下的是當你超出單一團隊
畫面後才需要的引擎細節。

### 引擎全貌：執行所有 graph 的 runtime

引擎是行程層級的 host。一個行程一個引擎。引擎裡可以同時存在任意多張 graph——你的團隊、你臨時拉起來快速對話的獨立生物、沒有同儕的監控生物——每張都是獨立的 connected component。拓樸不是凍結的；channel 可以在 runtime 跨 graph 拉起來（造成 graph 合併），也可以拆掉（可能讓 graph 分裂回去）。

```
              +-----------------------------------------+
              |              Terrarium engine           |
              |          (one per process, no LLM)      |
              +-----------------------------------------+
                |                  |                |
         graph A             graph B           graph C
   +-------------------+ +-------------+   +-------------+
   | root <- swe       | | scout       |   | watcher     |
   |    \-> reviewer   | | (solo)      |   | (no peers)  |
   |    \-> tester     | +-------------+   +-------------+
   +-------------------+

         |  ^
         |  |  connect(scout, swe, channel="leads")
         v  |  -> graph A 和 B 合併；environments 聯集，
            |     已 attach 的 session store 也合併。
            |
         |  |  disconnect(reviewer, tester, ...)
         v  |  -> 若移除連結讓 graph 斷開，A 會分裂；
            |     每一邊都拿到 parent session 的副本。
```

每一件可觀察的事——文字片段、工具活動、channel 訊息、拓樸變更——都從同一個事件匯流排（`EngineEvent` + `EventFilter`）流出。無論你有一張 graph 或十二張，都用一個 filter 訂閱。上面那張 per-graph 的心智模型只是這個引擎的一種 **投影**。

超出單一團隊場景之後，這個層次給你的：

- **一個行程多個 session** — 服務端可以並排 host 上百個使用者 session，不需要每張 graph 都拉一個獨立的執行期行程。
- **runtime 跨 graph 重接線** — 在兩個獨立 run 之間畫一條 channel 就能合併它們；session 歷史會自動合併。
- **統一可觀測性** — 一個訂閱 filter 涵蓋所有事件。
- **保留分層無知** — 生物仍然不知道自己在引擎裡。它只知道自己的 agent、工具，和它所在 graph 注入的 channel handle。

## 為什麼它存在

當生物變得可攜——一隻生物能單獨執行，同一份設定也能獨立運作——你就需要一種方法把它們組合起來，同時又不強迫它們彼此知道對方的存在。生態瓶就是這個方法。

它維持的核心不變條件是：生物永遠不知道自己在生態瓶裡。它只知道要監聽哪些頻道名稱、往哪些頻道名稱送訊息，就這樣而已。把它從生態瓶拿出來，它仍然可以作為獨立生物執行。

## 我們怎麼定義它

生態瓶設定：

```yaml
terrarium:
  name: my-team
  root:                         # 可選；位於團隊外、面向使用者的 agent
    base_config: "@pkg/creatures/general"
    system_prompt_file: prompts/root.md   # 團隊專用的委派提示詞
  creatures:
    - name: swe
      base_config: "@pkg/creatures/swe"
      output_wiring: [reviewer]           # 確定性邊 → reviewer
      channels:
        listen:    [tasks, feedback]
        can_send:  [status]
    - name: reviewer
      base_config: "@pkg/creatures/swe"   # reviewer 角色來自 prompt，而不是專用生物
      system_prompt_file: prompts/reviewer.md
      channels:
        listen:    [status]
        can_send:  [feedback, status]     # 條件式：approve vs. revise 仍走頻道
  channels:
    tasks:    "團隊拉取的工作項"
    feedback: "送回寫者的審閱備註"
    status:   "廣播的狀態 ping"
```

所有頻道都是廣播 —— 每個監聽者都收到每一次 send。執行期會自動為每隻
生物建立一條頻道（名稱就是它自己的名字，方便其他成員透過
`send_channel` 私訊它），而如果存在 root，還會建立一個
`report_to_root` 頻道（其他每隻生物都被接線為可送往該頻道）。

## 我們怎麼實作它

- `terrarium/engine.py` —— `Terrarium` 類別。每個行程一個。擁有拓樸狀態、live 生物、environment、掛著的 session store、訂閱者列表。是 async context manager (`async with Terrarium() as t:`)，並附 classmethod factory (`from_recipe`、`with_creature`、`resume` — 最後一個尚未實作)。
- `terrarium/topology.py` —— 純資料 graph 模型 (`TopologyState`、
  `GraphTopology`、`ChannelInfo`、`TopologyDelta`)。沒有 live agent 參
  考；不需 asyncio 就能測。連通分量透過 BFS 在「creature ↔ channel」
  二部圖上算出；mutation 會回傳一個 delta 描述 `merge` /
  `split` / `nothing`。引擎在它上面疊 live state。
- `terrarium/creature_host.py` —— `Creature`，引擎對每隻生物的 wrapper。把以前獨立 agent 與頻道感知的兩個面合成同一個型別。
- `terrarium/recipe.py` —— 走完一份 `TerrariumConfig` 套到引擎上：宣告頻道、為每隻生物加一條 direct channel、若有 root 加 `report_to_root`、接 listen / send 邊、注入頻道觸發器、啟動一切。
- `terrarium/channels.py` —— 頻道注入 (當一隻生物加入了一個有它要 listen 的頻道的 graph，引擎會往它的 agent 加一個 `ChannelTrigger`)，以及 `connect_creatures` / `disconnect_creatures` 的本體。
- `terrarium/root.py` —— `assign_root` 輔助函數。給一隻已經在 graph 裡的生物，把它指定為該 graph 的 Root：宣告（或重用）一個 `report_to_root` 頻道、把 graph 內每隻其它生物接成在該頻道上送訊息、讓 Root 在每個既有頻道上 listen，並把 `creature.is_root = True` 翻起來。純粹是 channel + wiring；工具註冊和使用者 IO 掛接留給上層處理。當你以 imperative 方式建 graph 又想要傳統「一團隊一 Root」拓樸而不走 recipe 檔案時使用。
- `terrarium/session_coord.py` —— Session 合併 / 分裂策略。Graph 合併時把兩邊舊 store 合成一份新的；Graph 分裂時把 parent store 複製到兩邊。
- `terrarium/events.py` —— `EngineEvent` 分類，加 `EventFilter`、`ConnectionResult`、`DisconnectionResult`。

新程式碼請直接用 `Terrarium`。頂層 re-export 是穩定的：`from kohakuterrarium import Terrarium, Creature, EngineEvent, EventFilter`。

## 因此你可以做什麼

- **明確分工的專家團隊。** 兩隻 `swe` 生物透過 `tasks` / `review` / `feedback` 頻道拓樸協作，而 reviewer 角色則由 prompt 驅動。
- **面向使用者的特權節點。** 見 [privileged-node](privileged-node.md)。它讓使用者只和一隻 agent 對話，再由那隻 agent 去編排整個團隊。
- **透過輸出接線建立確定性的 pipeline 邊。** 在生物設定裡宣告它的回合結束輸出要自動流向下一階段——不需要依賴 LLM 記得呼叫 `send_message`。
- **熱插拔專家。** 不需重啟，就能在工作階段中途加入新生物；現有頻道會直接接上。可以透過命令式 API（`Terrarium.add_creature`、`connect`、`disconnect`）使用，也可以由圖中的特權節點透過[群組工具](../glossary.md#group-tools--群組工具)（`group_add_node`、`group_channel`、`group_wire`、…）呼叫。
- **非破壞式監看。** 用 `EventFilter` 訂閱引擎事件流 —— 頻道訊息會和拓樸、生命週期、工具事件一起流過來，而不會與任何 consumer 競爭。

## 與頻道並存的輸出接線

頻道是原本的答案，而且現在仍然是正確答案，適合處理**條件性與選用流量**：會批准*或*要求修改的 critic、任何人都可讀的狀態廣播、群聊式側通道。這些都依賴生物自己呼叫 `send_message`。

輸出接線則是另一條框架層級的路徑：生物在設定裡宣告 `output_wiring`，執行期就會在回合結束時，把 `creature_output` TriggerEvent 直接送進目標的事件佇列。沒有頻道、沒有工具呼叫——這個事件走的是和其他 trigger 相同的路徑。

把接線用在**確定性的 pipeline 邊**（「下一步一定要交給 runner」）。把頻道留給接線無法表達的條件式 / 廣播 / 觀察情境。兩者可以在同一個生態瓶裡自然組合——kt-biome 的 `auto_research` 與 `deep_research` 生態瓶正是這樣做的。

接線的設定形狀與混合模式，請見 [生態瓶指南](../../guides/terrariums.md#output-wiring)。

## 說實話，我們的定位

我們把生態瓶視為橫向多代理的**一種提案架構**，而不是已經完全定案的唯一答案。各個部件今天已經可以一起工作（接線 + 頻道 + 熱插拔 + 觀察 + 對 root 的生命週期回報），而且 kt-biome 的生態瓶也把這整套從頭到尾跑通了。我們仍在學習的是慣用法：什麼時候該優先用接線、什麼時候該用頻道；要怎麼在不手刻頻道 plumbing 的前提下表達條件分支；要怎麼讓 UI 對接線活動的呈現能和頻道流量並列。

當工作流本質上就是多生物協作，而且你希望生物保持可攜時，就用它。當任務比較自然地在一隻生物內部拆解時，就用子代理（縱向）——對多數「我需要上下文隔離」的直覺來說，縱向通常更簡單。兩種都合理；框架不替你做決定。

至於我們正在探索的完整改進方向（UI 中接線事件的呈現、條件式接線、內容模式、接線熱插拔），請參見 [ROADMAP](../../../ROADMAP.md)。

## 不要被它框住

沒有 root 的生態瓶是合理的（無頭協作工作）。沒有生物的 root，則是一隻附帶特殊工具的獨立 agent。一隻生物在不同執行中，可以屬於零個、一個或多個生態瓶——生態瓶不會污染生物本身。

## 另見

- [多代理概覽](README.md) —— 縱向與橫向。
- [特權節點](privileged-node.md) —— 圖中代表使用者的特權生物。
- [動態圖](dynamic-graph.md) —— 自動合併 / 自動分裂與圖內的群組工具表面。
- [impl-notes / graph and sessions](../impl-notes/graph-and-sessions.md)
  —— 合併 / 分裂記帳的實作細節。
- [頻道](../modules/channel.md) —— 生態瓶所由之構成的原語。
- [ROADMAP](../../../ROADMAP.md) —— 生態瓶接下來的方向。
