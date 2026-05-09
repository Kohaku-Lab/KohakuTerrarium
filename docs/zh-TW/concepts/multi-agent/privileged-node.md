---
title: 特權節點
summary: 圖中已註冊群組工具的生物 —— `root:` 配方關鍵字會把一個節點提升為該狀態。
tags:
  - concepts
  - multi-agent
  - privileged
  - root
---

# 特權節點

## 它是什麼

**特權節點**（privileged node）是位於[圖](../glossary.md#graph)中的一隻
生物，被授予了變更所屬圖所需的[群組工具](../glossary.md#group-tools)：
生成或移除其他生物、繪製或刪除頻道、啟動或停止成員、查詢圖的狀態。從
結構上說，它就只是另一隻生物 —— 同樣的設定、同樣的模組、同樣的生命週
期。讓它「特權」的，是執行期的旗標（`creature.is_privileged = True`）
以及引擎在提升時一併執行的工具註冊。

`terrarium` 配方裡的 `root:` 是把一個節點標記為特權的其中一種方式。配
方也可以在成員上 inline 標記特權；引擎 API 接受在建立生物時傳入
`privileged=True`。透過工具生成的工人生物（經由 `group_add_node`）預設
**不是** 特權 —— 工人沒有被顯式提權前不能再分叉同儕。

## 為什麼它存在

兩個需求共享同一個答案：

1. **能夠編輯自己的圖。** 多代理工作經常在執行中才發現真正需要的團隊
   形狀。某個節點必須被允許呼叫 `group_add_node`、`group_channel` 等
   工具。這個旗標就是用來標識「哪一個」。
2. **面向使用者的介面。** 當人類在與圖互動時，需要一個單一的對話對
   口。那個節點通常也想要同樣的權力 —— 看到正在發生什麼、生成助手、
   重新接線頻道 —— 所以面向使用者的節點和特權節點常常是同一隻。

`root:` 配方關鍵字把第二種情況收為一行簡寫：宣告一個特權節點，並套上
標準的「面向使用者 root」接線（一條 `report_to_root` 頻道供所有人回
報、root 監聽圖中其他每一條頻道）。底層機制就是特權旗標加上接線；
「root」只是慣例。

## 我們怎麼定義它

讓節點變成特權的三種方式：

### 1. `root:` 配方關鍵字

```yaml
terrarium:
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md     # 團隊專用的委派提示詞
    controller:
      reasoning_effort: high
  creatures:
    - ...
```

配方載入器會建立該節點、把它標記為特權、開啟（或重用）一條
`report_to_root` 頻道、把圖中其他每一隻生物都接線為可送往該頻道、讓該
節點監聽圖中其他每一條頻道，並強制註冊群組工具。它同時被掛載為面向使
用者的介面（TUI / CLI / 網頁 tab）。

### 2. 在配方成員上 inline `privileged: true`

```yaml
terrarium:
  creatures:
    - name: planner
      base_config: "@kt-biome/creatures/general"
      privileged: true
      ...
```

適用於「我要一個不是面向使用者的特權成員」—— 例如，旁邊坐著幾位工人
的特權「主管」節點，獨立於另一個面向使用者的 root。

### 3. 程式化提權

```python
async with Terrarium() as engine:
    sup = await engine.add_creature(
        "@kt-biome/creatures/general",
        is_privileged=True,
    )
    # sup 立即擁有群組工具

# 或者，要套上完整的 root 風格接線（report_to_root + 全監聽）：
from kohakuterrarium.terrarium.root import assign_root_to
assign_root_to(engine, sup)
```

`engine.add_creature(..., is_privileged=True)` 是最小提權：旗標被設
上、`force_register_privileged_tools` 被執行。`assign_root_to(engine,
creature)` 是完整的 root 風格輔助 —— 特權 + `report_to_root` 頻道 + 全
監聽接線。

## 我們怎麼實作它

- **特權旗標：** `Creature.is_privileged` —— 這是生物 handle 的執行期
  屬性，與底層 agent 設定無關。
- **工具註冊：** `terrarium/tools_group.py` 暴露
  `force_register_basic_tools`（每隻生物都有）與
  `force_register_privileged_tools`（僅在特權節點上）。特權工具表面是
  `group_add_node`、`group_remove_node`、`group_start_node`、
  `group_stop_node`、`group_channel`、`group_wire`、`group_status`。
- **配方 `root:`：** 配方載入器在節點建立後呼叫 `assign_root_to`。
  `terrarium/root.py:assign_root_to` 會確保 `report_to_root` 頻道存
  在、把圖中其他每一隻生物接線為可送往該頻道、讓特權節點監聽每一條已
  存在的頻道、把它標記為特權，並註冊特權工具表面。
- **拓樸刷新：** 執行期提示詞訂閱器會監聽 `TOPOLOGY_CHANGED` 事件，並
  為每一隻受影響的生物重新產生「圖感知」區塊 —— 因此特權節點的提示詞
  永遠反映當前的生物、頻道與接線。

## 因此你可以做什麼

- **面向使用者的指揮者。** 使用者對特權節點說：「叫 SWE 修 auth
  bug，再叫 reviewer 批准。」節點透過頻道送訊息，並監看
  `report_to_root` 得知完成情況。
- **動態團隊建構。** 特權節點呼叫 `group_add_node` 生成專家、
  `group_channel` 宣告頻道、`group_wire` 加入輸出接線、
  `group_remove_node` / `group_stop_node` 收掉成員。
- **跨圖重接線。** `group_channel` 若指向呼叫者圖之外的目標，會經過
  `Terrarium.connect` 路由 —— 兩個圖（與它們的 session store）會合
  併，呼叫者就能接管原本獨立的生物。
- **每個圖可以有多個特權節點。** 沒有規定只能有一個。一個圖可以同時有
  面向使用者的 root 和特權主管，或多位主管分攤團隊。
- **可觀測性的樞紐。** root 風格的特權節點會自動監聽每一條頻道並接收
  `report_to_root` 的流量 —— 這正是執行摘要外掛、告警規則等工作的最
  佳位置。

## 不要被它框住

完全沒有特權節點的圖也合理 —— 像是無頭 pipeline、cron 驅動的協調、批
次作業。特權只是為了執行期編輯而設的便利；如果你的團隊形狀由配方固定
下來，可能根本用不到它。

## 另見

- [Terrarium](terrarium.md) —— 圖與其特權節點所棲身的引擎。
- [動態圖](dynamic-graph.md) —— 群組工具如何變更拓樸、引擎如何反應。
- [多代理概覽](README.md) —— 特權節點在整個模型中的位置。
- [reference/builtins.md — group_* 工具](../../reference/builtins.md)
  —— 特權工具表面。
