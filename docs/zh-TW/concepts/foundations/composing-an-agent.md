---
title: 組合一個 agent
summary: 六個生物模組如何在執行期透過單一 TriggerEvent 信封互動。
tags:
  - concepts
  - foundations
  - runtime
---

# 組合一個 agent

[什麼是 agent](what-is-an-agent.md) 介紹了六個模組。本頁要說明它們實際上如何拼成一隻正在運作的生物。

## 單一信封：`TriggerEvent`

所有來自控制器外部的東西，都會以 `TriggerEvent` 的形式進來：

- 使用者輸入文字 → `TriggerEvent(type="user_input", content=...)`
- 計時器觸發 → `TriggerEvent(type="timer", ...)`
- 工具執行完成 → `TriggerEvent(type="tool_complete", job_id=..., content=...)`
- 子代理回傳 → `TriggerEvent(type="subagent_output", ...)`
- 頻道訊息 → `TriggerEvent(type="channel_message", ...)`
- 上下文注入 → `TriggerEvent(type="context_update", ...)`
- 錯誤 → `TriggerEvent(type="error", stackable=False, ...)`

所有事情共用一個信封。控制器不需要為每一種來源各寫一條不同的程式路徑；它只要問：「我這一輪拿到了哪些事件？」這就是整個架構上的簡化。

## 事件佇列

```
        +-----------+  +---------+  +-----------+  +----------+
        | input.get |  | trigger |  | tool done |  | sub done |
        +-----+-----+  +----+----+  +-----+-----+  +-----+----+
              \             \             /             /
               \             \           /             /
                +------------ event queue ------------+
                              |
                              v
                        +------------+
                        | Controller |
                        +------------+
```

每一個喚醒來源都會把事件推進同一個佇列。多個「同時」到達的事件可以是 **stackable** 的——控制器會把它們合併成同一輪的使用者訊息，因此一波活動高峰不會直接變成一波 LLM 呼叫高峰。

不可堆疊的事件（錯誤、優先訊號）會打斷這個批次。它們會在自己的輪次裡單獨處理。

## 一輪的流程，逐步拆開

```
  +---- collect events from queue (batch stackable)
  |
  |   +- build turn context (job status + event content, multimodal-aware)
  |
  |   +- call LLM in streaming mode
  |
  |       during stream:
  |         - text chunks -> output
  |         - tool blocks detected -> asyncio.create_task(run tool)
  |         - sub-agent blocks detected -> asyncio.create_task(run sub)
  |         - framework commands (info, jobs, wait) -> inline
  |
  |   +- await direct-mode tools + sub-agents
  |
  |   +- feed their results back as new events
  |
  |   +- decide: loop or break
  +---- back to event queue
```

有幾個值得注意的不變條件：

1. **工具會立刻開始。** 工具區塊一解析完成——遠在 LLM 還沒說完之前——我們就會把它派發成一個新 task。同一輪裡的多個工具會平行執行。詳見 [impl-notes/stream-parser](../impl-notes/stream-parser.md)。
2. **同一時間只會有一輪 LLM。** 每隻生物各自有一把 lock，保證控制器不會被重入。觸發器可以自由觸發，但它們只會進佇列。
3. **direct / background / stateful** 是派發模式，不是三套分離系統。參見 [modules/tool](../modules/tool.md)。

## 其他模組放在哪裡

- **輸入 (Input)** 會把事件推進佇列；除此之外它本身沒有變。
- **觸發器 (Trigger)** 各自擁有一個背景 task，當條件成立時就把事件推進佇列。
- **工具與子代理** 透過 executor / sub-agent manager 執行。它們完成後會變成新的事件——迴圈就這樣閉合。
- **輸出 (Output)** 消費控制器產生的文字與工具活動串流，並把它送往一個或多個 sink（stdout、TTS、Discord，或任何你設定的目的地）。

## 在這個層級，概念文件有涵蓋與沒涵蓋什麼

本頁是架構總覽。每個模組更深入的故事，都在各自的模組文件裡：

- [Controller](../modules/controller.md) — 迴圈本身
- [Input](../modules/input.md) — 第一個觸發器
- [Trigger](../modules/trigger.md) — 從世界到 agent 的喚醒
- [Output](../modules/output.md) — 從 agent 到世界
- [Tool](../modules/tool.md) — agent 的手
- [Sub-agent](../modules/sub-agent.md) — 受上下文範圍限制的委派者

另外有兩個橫切性的部分，適合放在獨立章節，而不是壓在某一個模組上：

- [Channel](../modules/channel.md) — 工具、觸發器與 terrarium 共同分享的通訊基底。
- [Session and environment](../modules/session-and-environment.md) — 私有狀態與共享狀態的切分。

## 延伸閱讀

- [Agent as a Python object](../python-native/agent-as-python-object.md) — 這張圖在嵌入式使用時，如何映射回一般 Python。
- [impl-notes/stream-parser](../impl-notes/stream-parser.md) — 為什麼工具會在 LLM 停止之前就開始執行。
- [impl-notes/prompt-aggregation](../impl-notes/prompt-aggregation.md) — 驅動這個迴圈的 system prompt 是怎麼建出來的。
