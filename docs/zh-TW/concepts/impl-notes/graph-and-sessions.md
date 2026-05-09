---
title: 圖與 session
summary: terrarium 引擎如何計算連通分量、在 mutation 呼叫下變更拓樸，並在自動合併 / 自動分裂中保持 session store 的一致。
tags:
  - concepts
  - impl-notes
  - graph
  - persistence
---

# 圖與 session

## 這個問題要解決什麼

一個多生物 terrarium 是一張可在執行期變化的圖。三個特性必須同時成
立：

1. **反應式拓樸。** 執行期的心智模型 —— 誰共享 environment、誰共享
   session —— 必須自動跟隨連通性。如果使用者拔掉兩半之間最後一條頻
   道，那兩半應該在不需要操作員介入的前提下變成獨立的。
2. **歷史不丟。** 合併與分裂是資訊轉換；一隻生物之前的對話應該仍然
   能從原圖的任何後代裡復原。
3. **正在飛行的頻道訊息不會因為換 environment 而錯路由。** 當
   environment 因為合併或分裂而改變時，飛行中的頻道觸發器必須繼續
   在正確的活動 registry 上送達。訂閱者不會被通知重新訂閱；引擎得讓
   它們指向存活的物件。

樸素做法（每次讀取都重算圖，或在啟動時凍結拓樸）會破壞其中之一。

## 考慮過的選擇

- **每次 mutation 都即刻全表 reindex。** 每次變更重新算整張圖表。簡
  單。每次 mutation O(N)，可觀測性昂貴（每次變更隱含全 diff），還會
  迫使任何拿著 graph id 的人作廢它。
- **懶式：不追蹤分量；按需問「A 與 B 連通嗎？」** mutation 便宜，但
  共用 environment 的查詢昂貴；如果圖本身不存在為物件，要把 session
  store 掛到「一個圖」就難。
- **mutation 前預測。** 在套用變更前算出 mutation 後的分量，並拒絕
  會造成意外狀態的變更。太嚴格；拓樸變更是使用者動作，不是請求許
  可。
- **套用，再 normalise。** 變更拓樸。每次變更後只在受影響的圖內重算
  分量，並發出一個 `TopologyDelta` 描述發生了什麼。引擎對 delta 反
  應 —— 重新分配 environment、複製 session store、重指向生物、重新
  注入觸發器、發事件。我們的做法。

## 我們實際怎麼做

### 純資料圖（`terrarium/topology.py`）

拓樸活在一個樸素的 `TopologyState` 值裡：

- `state.graphs: dict[graph_id, GraphTopology]` —— 每個連通分量一筆。
  每個 `GraphTopology` 裝它的 creature id、頻道宣告、二部
  listen / send 邊映射。
- `state.creature_to_graph: dict[creature_id, graph_id]` —— 反向索
  引，回答「這隻生物在哪個圖？」。

Mutation 是純函數：`add_creature`、`remove_creature`、
`add_channel`、`remove_channel`、`connect`、`disconnect`、
`set_listen`、`set_send`。每個回傳一個 `TopologyDelta` 描述發生了什麼
（`kind` ∈ {`nothing`, `merge`, `split`}，加 `old_graph_ids`、
`new_graph_ids`、`affected_creatures`）。沒有活動 agent，沒有 asyncio
—— 整個模組可以純當資料測試。

### 連通分量（`find_components`、`_normalize_components`）

連通性透過 BFS 在二部圖（一邊是生物、另一邊是 channel）上算出。兩隻
生物同分量，若且唯若它們之間存在一條經由它們共享（監聽或送出）的頻
道的路徑。`find_components` 重建 per-creature ↔ per-channel 鄰接映
射，並從每一隻未訪問的生物跑 BFS。

`_normalize_components` 在可能切斷連通性的 mutation 之後跑。如果重算
後的分量數 `<= 1`，delta 是 `kind="nothing"`。如果大於 1，最大分量保
留原 graph id，其餘分量鑄造新 id；頻道按它們實際觸及的分量重新分配；
delta 報告 `kind="split"` 與受影響的生物。

### 合併記帳（`channels.connect_creatures`、`_merge_environment_into`、`session_coord.apply_merge`）

當 `Terrarium.connect(a, b, channel=...)` 呼進拓樸層、結果是
`kind="merge"` 時，引擎：

1. 決定存活 graph id 與被丟棄的 graph id。
2. `_merge_environment_into(engine, surviving, dropped)` 把每一個頻
   道物件從被丟棄的 environment 搬到存活 environment 的
   `ChannelRegistry`。每一個既存的監聽觸發器對著新 env 重新注入，讓
   它的 `on_send` callback 繼續指向正確的 registry 與正確的 session
   store。
3. 把每一隻受影響的生物的 `graph_id` 重指向存活圖（讓後續拓樸查詢落
   在正確的位置）。
4. `session_coord.apply_merge(engine, delta)` 整合 session store。它
   呼叫 `merge_session_stores(old_stores, new_path)`，在存活圖的路徑
   下建立新 store，把每一個事件透過 `copy_events_into` 從每一個舊
   store 複製到新 store（保留 `turn_index`、`spawned_in_turn`、
   `branch_id`，重新打 event id），並把
   `parent_session_ids: [old_a, old_b]` 加上
   `merged_at: <timestamp>` 寫入新 store 的 meta。
5. 發出 `TOPOLOGY_CHANGED(kind="merge", ...)`。

新合併 store 立即撐住存活 environment 中每一個頻道的持久化 callback
（因為第 2 步重新注入觸發器，而 callback 會閉包
`engine.session_store_for(graph_id)` —— 現在解析到合併 store）。

### 分裂記帳（`channel_lifecycle.apply_split_bookkeeping`、`session_coord.apply_split`）

當一次移除或斷開呼叫回傳帶 `kind="split"` 的 delta 時，引擎：

1. 為每一個新分量分配新的 `Environment`，並把該分量的拓樸頻道註冊到
   它的 environment。
2. 對每一隻受影響的生物：把 `creature.graph_id` 指向它的新分量，把
   它的 agent 與 executor 綁到新 env，把每一個頻道監聽觸發器對著新
   env 的頻道物件拆掉重注入。
3. `session_coord.apply_split(engine, delta)` 呼叫
   `split_session_store(old_store, new_paths)`，在每一個新分量路徑下
   建立一個新 store，並 `copy_events_into` 把完整的分裂前歷史複製進
   每一個子 store。每一個子 store 在 meta 記下
   `parent_session_ids: [old_graph_id]` 與 `split_at: <timestamp>`。
   session store 反向映射被更新，讓每一個新 graph id 解析到自己的
   store。
4. 發出 `TOPOLOGY_CHANGED(kind="split", ...)`。

分裂前的歷史在每一邊都保留。分支從共同的根分歧。

### 頻道持久化 callback（`channels._ensure_channel_persistence`）

當一個頻道在掛著 session store 的圖的 environment 裡被註冊時，頻道
上會裝上一個 `on_send` callback。Callback 透過
`store.save_channel_message()` 把每一次 send 寫入 store。Callback 是
冪等的（再次安裝會替換自己），且每次呼叫時都會讀取頻道物件上當下的
`_terrarium_graph_id` —— 因此當頻道物件因合併而搬家時，callback 會自
動指向存活 store，無需重裝。

這就是合併路徑（上面第 2 步）能搬動頻道物件而不丟訊息持久化的原因。

### 觸發器重注入（`channels.inject_channel_trigger`、`_teardown_existing_trigger`）

`ChannelTrigger` 是訂閱在某個特定頻道物件上的 async task。在合併或分
裂之後，撐住一隻生物監聽邊的頻道物件可能改變身份（不同的
`ChannelRegistry`、不同的 `Environment`）。`inject_channel_trigger` 是
冪等的：它按 id（`channel_{subscriber_id}_{channel_name}`）拆掉任何
既有觸發器，從舊頻道取消訂閱，再對當下活動頻道重新訂閱。
`apply_split_bookkeeping` 與 `_merge_environment_into` 路徑會對每一對
受影響的生物 × channel 呼叫它。

### Resume 時 recipe 是真理來源（`terrarium/resume.py`、`terrarium/recipe.py`）

`resume_into_engine` **不會**把活動拓樸寫到磁碟再播回。它會讀
`meta.config_path`（session 建立時記下的 recipe 路徑），並在新的引擎
上重新套用 recipe：宣告頻道、加入生物、接 listen / send 邊、註冊輸出
接線、若 recipe 宣告 root 則呼叫 `assign_root_to`。每一隻重建的生物
之上接著由 `session.resume.resume_agent` 注入保存的狀態（對話、
scratchpad、觸發器）。

一個推論：如果保存當下 session 處於*分裂狀態*，resume 會從 recipe 重
建一張圖。血脈 metadata（`parent_session_ids`、`merged_at`、
`split_at`）在 resume 後的 store 裡仍然存活，因此稽核軌跡仍可讀，但
活動拓樸會回到 recipe 的自然形狀。

## 維持的不變條件

- **連通性 ↔ graph id。** 兩隻生物在同一個圖中，若且唯若它們之間透
  過頻道連通。永遠如此。
- **一圖一 store。** 一個 graph id 至多對應一個 `SessionStore`。跨圖
  的生物不共享 store。
- **轉換時歷史保留。** 合併把歷史聯集到一個新 store 裡；分裂把來源
  store 複製到每一個新 store。沒有事件被丟、沒有事件變得不可讀。
- **血脈被記下。** 每一次轉換在新 store 的 meta 寫入
  `parent_session_ids` 與 `merged_at` / `split_at` 時間戳。
- **觸發器物件永遠指向活動頻道。** 任何合併或分裂之後，每一個頻道監
  聽觸發器都對著存活的 environment 拆掉重注入。
- **拓樸形狀由 recipe 主導。** Resume 時拓樸來自 recipe；session 提
  供 per-creature 狀態與血脈 metadata，不提供圖結構。

## 它住在程式碼哪裡

- `src/kohakuterrarium/terrarium/topology.py` —— `TopologyState`、
  `GraphTopology`、`TopologyDelta`、純 mutation 函數、
  `find_components`、`_normalize_components`、`_merge_graphs`。
- `src/kohakuterrarium/terrarium/engine.py` —— `Terrarium` 編排。
  `add_creature`、`remove_creature`、`connect`、`disconnect`、
  `add_channel`、`remove_channel`，加 environment / session store
  registry。
- `src/kohakuterrarium/terrarium/channels.py` —— `connect_creatures`、
  `_merge_environment_into`、`_ensure_channel_persistence`、
  `inject_channel_trigger`、`_teardown_existing_trigger`。
- `src/kohakuterrarium/terrarium/channel_lifecycle.py` ——
  `apply_split_bookkeeping`、頻道移除流程、environment 重新分配。
- `src/kohakuterrarium/terrarium/session_coord.py` —— `apply_merge`、
  `apply_split`、`merge_session_stores`、`split_session_store`、
  `copy_events_into`、meta 刷新輔助函數。
- `src/kohakuterrarium/terrarium/runtime_prompt.py` —— 事件驅動的
  per-creature 提示詞刷新，監聽 `TOPOLOGY_CHANGED`、
  `CREATURE_STARTED`、`CREATURE_STOPPED`、`OUTPUT_WIRE_ADDED`、
  `OUTPUT_WIRE_REMOVED`、`PARENT_LINK_CHANGED`。
- `src/kohakuterrarium/terrarium/resume.py` —— `resume_into_engine`、
  recipe 驅動的拓樸重建。
- `src/kohakuterrarium/terrarium/events.py` —— `EngineEvent` 分類、
  `EventFilter`。
- `src/kohakuterrarium/session/store.py` —— 協調器使用的
  `SessionStore` API。

## 另見

- [動態圖](../multi-agent/dynamic-graph.md) —— 這份實作支撐的使用者
  心智模型。
- [Session 持久化](session-persistence.md) —— 底層的 `.kohakutr` 檔
  案格式與 per-creature resume。
- [生態瓶](../multi-agent/terrarium.md) —— 引擎契約。
