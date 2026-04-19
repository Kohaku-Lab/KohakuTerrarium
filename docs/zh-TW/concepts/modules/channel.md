---
title: 頻道
summary: 具名的訊息管道 — queue 與 broadcast — 是多代理與跨模組通訊的底層基礎。
tags:
  - concepts
  - module
  - channel
  - multi-agent
---

# 頻道

## 它是什麼

**頻道 (channel)** 是一種帶型別的訊息管道。一端可以送出；另一端
或多端可以接收。頻道存在於生物的私有工作階段裡，或存在於多隻
生物都看得到的共享 environment 裡。

它嚴格來說不是生物的「正典」模組之一 — 在 chat-bot → agent 的
推導路徑裡，它從來沒有出現過。它是讓工具與觸發器能在多個代理之
間真正變得有用的通訊底層。

## 為什麼它存在

當你已經有工具與觸發器之後，很自然就會想讓兩個 agent 彼此說話。
摩擦最低的做法是：agent A 的工具寫入一則訊息；agent B 有一個觸發
器，當某個名字的訊息到達時就觸發。

這正是頻道。它不是什麼新點子 — 它只是*命名慣例*加上一點點佇列
機制，讓「這邊寫入、那邊監聽」能成立，而雙方都不需要知道彼此是
誰。

## 我們怎麼定義它

頻道有兩種類型：

- **`SubAgentChannel` (queue)** — 訊息依 FIFO 排列，每則訊息由
  *一個*接收者消費。適合 request/response 或任務派發。
- **`AgentChannel` (broadcast)** — 每個訂閱者都有自己的佇列，
  每則訊息都會送到每個訂閱者。適合公告。

頻道存在於 `ChannelRegistry` 裡。生物的私有工作階段有一份 registry；
terrarium 的 environment 有一份共享 registry。生物可以監聽其中任一種。

`ChannelTrigger` 會把頻道名稱綁到生物的事件流上 — 每當有訊息到達，
就會推入一個 `channel_message` 事件。

## 我們怎麼實作它

`core/channel.py` 實作了兩種頻道類別與 registry。
`modules/trigger/channel.py` 實作了把頻道橋接進生物事件佇列的觸發器。

自動建立的頻道：

- 在 terrarium 裡，每隻生物各有一個 **queue**，名稱就是生物名稱
  本身（所以其他生物可以直接 DM 它）。
- 當存在 root agent 時，會建立 `report_to_root`。

`ChannelObserver`（`terrarium/observer.py`）可以在頻道上掛一個不破壞
性的 callback：observer 能看見每一則送出的訊息，但不會消費它們。
這就是 dashboard 能觀察那些已經有真實 consumer 在讀取的 queue 頻道
的方式。

## 因此你可以做什麼

- **生態瓶接線。** terrarium 設定裡每一條 listen/send 設定，最終
  都會解析成頻道操作。
- **群聊模式。** `send_message` 工具（任一生物皆可用）+
  其他生物上的 `ChannelTrigger` = N 方群聊。不需要新的 primitive。
- **死信 / 失敗頻道。** 把錯誤導到專用的 broadcast 頻道；一隻
  `logger` 生物訂閱後寫入磁碟。
- **非破壞式除錯。** 用 `ChannelObserver` 去偷看一個已有真實 consumer
  持續排空的 queue。
- **跨生物 rendezvous。** 兩隻同時監聽同一個共享頻道的生物，可以
  輪流處理其中的項目。

## 頻道 vs. 輸出接線

頻道不是生物彼此溝通的唯一方法。另一個平行機制 — **輸出接線
(output wiring)** — 會在每個回合結束時，直接把一個
`creature_output` `TriggerEvent` 發送到目標生物的事件佇列裡，雙方都
不需要呼叫 `send_message`。該用哪一種：

- **頻道** — 條件式路由（approve 或 revise）、群聊、狀態廣播、
  延後 / 非必然流量、觀察。由生物自己決定要不要送、送去哪裡。
- **輸出接線** — 確定性的 pipeline 邊（「runner 的輸出永遠送給
  analyzer」）。以宣告式設定，並在回合結束時自動觸發。

同一個 terrarium 可以自由混用兩者。見
[terrarium](../multi-agent/terrarium.md) 與
[guides/terrariums](../../guides/terrariums.md#output-wiring)。

## 不要被它框住

獨立運作的生物其實不需要頻道 — 它的工具不會 `send_message`，
它的觸發器也不會監聽。頻道不是推導裡的一等模組；它是一種慣例，
只是因為太多 multi-agent 使用情境最後都能化約成它，所以框架乾脆
把它提供成 primitive。

這是「框架會自己彎折自己的抽象」最清楚的例子。頻道活在六模組分
類之外，而把「agent A 告訴 agent B 某件事」實作成「工具寫入、
觸發器觸發」本來就是刻意混用不同層。見 [boundaries](../boundaries.md)。

## 另見

- [工具](tool.md) — 傳送端那一半。
- [觸發器](trigger.md) — 接收端那一半。
- [多代理 / terrarium](../multi-agent/terrarium.md) — 頻道在那裡真正亮起來成為接線。
- [模式](../patterns.md) — 群聊、死信、observer。
