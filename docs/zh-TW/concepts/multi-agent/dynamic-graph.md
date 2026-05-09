---
title: 動態圖
summary: terrarium 的圖為何會在執行期變形 —— 連通分量、自動合併 / 自動分裂、圖內的「圖編輯器」、以及 session 血脈的代價與好處。
tags:
  - concepts
  - multi-agent
  - graph
  - hot-plug
---

# 動態圖

## 它是什麼

[Terrarium](terrarium.md) 不是固定形狀。執行中的生物集合、它們之間
的頻道、誰與誰共享一個 session —— 這一切都可以在執行期變化，**不需
要重啟**、不會重新建立未受影響的生物、也不會丟歷史。

引擎把活動系統建模為一張由生物與頻道組成的**圖**。這張圖的每個連通
分量是一個獨立的「graph id」，擁有自己的共用 environment 與自己的
session store。當拓樸變化時，引擎做出反應：

- 加入一隻新生物 → 預設落在一個新的單點分量；若呼叫者明確指定，則加
  入指定分量。
- 一條新接線跨越兩個分量 → 它們**自動合併**為一個分量（與一個合併後
  的 session store）。
- 生物或頻道被移除 → 若連通性斷開，分量**自動分裂**為新的連通片段
  （session store 複製到每一邊）。

這一切都是引擎在收到 mutation 呼叫時確定性地執行的結構性工作。圖內
的生物不做這些決定 —— 引擎做。

## 為什麼它存在

靜態拓樸的多代理執行期無法表達人們真正想在執行期做的事：

- 一個特權節點在任務進行中決定它需要一種沒預料到的專家，並想要立刻
  生成一隻。
- 兩個並排獨立執行的 session 在其中一方需要協助時想合併成一段單一對
  話。
- 一隻生物完成它的工作，應該在不影響他人的前提下被收掉；如果它原本
  是連接兩半的橋，那兩半也應該獨立地繼續執行。
- 團隊應該可以從外部觀察，但團隊內的人不知道自己被觀察；而且觀察必
  須在生物來去之間持續追蹤它們的身份。

讓圖變成動態的 —— 並把記帳責任交給引擎 —— 讓這四件事都不需要在每個
recipe 裡特殊處理。

## 心智模型

每一個連通分量是一個**圖**。兩隻生物在同一個圖裡，若且唯若它們之間
存在一條經由它們共享（監聽或送出）的頻道的路徑。圖是以下這些事情的
單位：

- **共用 environment。** 在一個圖中宣告的頻道活在該圖的
  `Environment` 裡；只有圖中的生物看得見。
- **Session。** 一個 `.kohakutr` 檔案撐住一個圖。同一個圖裡的生物共
  享歷史；不同圖的不會。
- **群組工具。** 特權操作（生成、移除、頻道 CRUD、輸出接線 CRUD）作
  用於呼叫者的圖。

分量不需宣告。它們是從任意當下的頻道相鄰關係**推導**出來的。一次連
通性變化就會重新推導。

## 執行期可以變更的東西

| 操作 | 拓樸影響 | Session store 影響 |
|------|----------|---------------------|
| `Terrarium.add_creature` | 新單點分量（預設），或加入指定圖 | 當圖已掛 store 時綁定 |
| `Terrarium.remove_creature` | 若該生物是橋，可能分裂 | 分裂側的記帳（複製） |
| `Terrarium.add_channel` | 不變更連通性 | 直接無影響 |
| `Terrarium.remove_channel` | 若該頻道是唯一路徑，可能分裂 | 分裂側的記帳 |
| `Terrarium.connect(a, b, ...)` | 若 `a`、`b` 在不同圖 → 合併 | 合併 store；記下 `parent_session_ids` |
| `Terrarium.disconnect(a, b, ...)` | 可能分裂 | 分裂側的記帳 |

同樣的 mutation 也透過[群組工具](../glossary.md#group-tools--群組工具)
（`group_add_node`、`group_remove_node`、`group_start_node`、
`group_stop_node`、`group_channel`、`group_wire`）暴露給圖中的
[特權節點](privileged-node.md)。它們合在一起就是圖內的**圖編輯器**
—— LLM 驅動的特權節點可以靠呼叫工具在執行中演化團隊，每一次 mutation
都會發出 `EngineEvent`，讓 observer 與執行期提示詞保持同步。

## 自動合併

合併發生在跨圖 connect 時。引擎會：

1. 在拓樸層聯集兩張圖（creature id、頻道宣告、listen / send 邊）。
2. 聯集兩個 `Environment` —— 每一個頻道物件從被丟棄的圖搬到存活的
   environment，已存在的頻道觸發器對著存活的 env 重新注入。
3. 把兩個 session store 合併為存活圖路徑下一個新的 store。每一個事
   件從兩個舊 store 複製到新 store；新 store 的 meta 記下
   `parent_session_ids` 與 `merged_at` 時間戳。
4. 把每一隻受影響的生物的 `graph_id` 重指向存活圖。
5. 發出 `TOPOLOGY_CHANGED` 事件，附 `kind="merge"`、`old_graph_ids`、
   `new_graph_ids`、`affected_creatures`。

合併之後，被丟棄的圖上原有頻道的流量都會經過合併後的 environment 路
由，而 session 寫入會指向合併後的 store。

## 自動分裂

分裂發生在一次移除切斷了圖中兩半之間的唯一連通路徑時。引擎會：

1. 在 mutation 後的拓樸上計算連通分量。
2. 最大分量保留原 graph id。其他分量鑄造新 id。
3. 為每一個新分量分配新的 `Environment`，並把該分量的拓樸頻道註冊到
   新 env 裡。
4. 對每一隻受影響的生物：把 `creature.graph_id` 指向它的新分量、把
   它的 agent 與 executor 綁到新 env、把它的每一個頻道監聽觸發器對
   著新 env 的頻道物件拆掉重注入（讓訊息在正確的活動 registry 上流
   動）。
5. 把分裂前的 session store 複製到每一個新分量的路徑下。每一個子
   store 都繼承完整的分裂前歷史，meta 記下 `parent_session_ids` 與
   `split_at` 時間戳。
6. 發出 `TOPOLOGY_CHANGED` 事件，附 `kind="split"` 與新的 graph id。

分裂時歷史不會遺失 —— 只會被複製。分支 session 從同一起點分歧。

## Resume：recipe 是真理來源

當一份保存的多生物 session 被 resume 時，引擎會**從 recipe 重建拓
樸**，**不是**用圖的凍結快照。session metadata 記著 `config_path`、
`agents`，以及血脈（`parent_session_ids`、`merged_at`、`split_at`）；
引擎播放磁碟上的 recipe 來重建生物、頻道與接線，再把保存的對話注入
回去。

意思是：

- 在兩次執行之間編輯 recipe 是被支援的。新頻道會出現、被移除的生物
  不見了、輸出接線被更新。
- *分裂狀態*的快照**不會**被保留。Resume 重建 recipe 自然形狀的圖；
  若保存當下 session 處於分裂狀態，resume 會重建原本合併後的圖。
- 血脈 metadata 仍然存活。即使 resume 後的圖回到 recipe 的拓樸，你
  仍可以透過 `parent_session_ids` 追蹤過去合併 / 分裂到當前 store 的
  歷史。

## 特權門檻

不是每一隻生物都該能變更圖。引擎區分：

- **特權生物** —— recipe 的 `root:` 節點、recipe 內 inline 標記
  `privileged: true` 的成員、以及以 `is_privileged=True` 建立的生物。
  它們持有[群組工具](../glossary.md#group-tools--群組工具)。
- **工人** —— 由特權呼叫者透過 `group_add_node` 生成的生物。它們落
  在呼叫者的圖裡，但**不**擁有群組工具。工人沒被引擎顯式提權前不能
  再分叉同儕或圖邊。

特權是執行期生物 handle 的屬性，與底層 agent 設定無關。同一份設定可
以在某個 terrarium 裡以特權身份執行、在另一個裡以非特權身份執行。

## 可觀測性

每一次拓樸 mutation 都會發出 `EngineEvent`：

- `CREATURE_STARTED` / `CREATURE_STOPPED`
- `OUTPUT_WIRE_ADDED` / `OUTPUT_WIRE_REMOVED`
- `PARENT_LINK_CHANGED`
- `TOPOLOGY_CHANGED`（merge / split / nothing，附帶新舊 graph id 與
  受影響的生物）
- `SESSION_FORKED` / `CREATURE_SESSION_ATTACHED`

訂閱者用 `EventFilter` 在 kind、creature id、graph id、channel 上過
濾。網頁 dashboard 用這條流來驅動 live 面板；執行期提示詞訂閱器用它
來在圖變化時刷新受影響生物的 system prompt（這樣特權節點的「圖感知」
區塊永遠當前）。

## 不要被它框住

完全沒有執行期變化的靜態 recipe 是最簡單的模式，也是好的預設。當工
作本身是動態的 —— 團隊形狀在執行中才被發現的開放性研究、一個
session 把另一個拉進來的臨時救援、分支與合併並存的並行探索 —— 才會
需要熱插拔與群組工具創作。

## 另見

- [生態瓶](terrarium.md) —— 圖所棲身的執行期引擎。
- [特權節點](privileged-node.md) —— 使用群組工具的特權生物。
- [impl-notes / graph and sessions](../impl-notes/graph-and-sessions.md)
  —— 合併 / 分裂記帳的實際實作方式。
- [reference / builtins — group_* 工具](../../reference/builtins.md)
  —— 群組工具表面。
