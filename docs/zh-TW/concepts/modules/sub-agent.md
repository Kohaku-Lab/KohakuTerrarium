---
title: 子代理
summary: 由父生物為界定範圍的任務所派生的巢狀生物，擁有自己的上下文與一部分工具。
tags:
  - concepts
  - module
  - sub-agent
---

# 子代理

## 它是什麼

**子代理 (sub-agent)** 是由父生物為某個界定範圍的任務所派生出的巢狀生物。它有自己的 LLM 對話、自己的工具（通常是父生物工具的子集），以及自己的（較小）上下文。當它完成工作後，會回傳一份濃縮結果，然後消失。

投影片版的總結是：*其實它也是一種工具*。從父控制器的角度來看，呼叫子代理和呼叫其他工具看起來完全一樣。

## 為什麼它存在

上下文視窗是有限的。真實任務——例如「探索這個 repo，然後告訴我 auth 是怎麼運作的」——可能會牽涉上百次讀檔。如果把這些探索都放在父生物自己的對話裡，就會把主要工作的預算吃光。改由子代理去做，通常會消耗另一份預算，而回傳的只是一份摘要。

這份預算現在可以設定。子代理可以有自己的多軸執行期預算（turn、工具呼叫，以及可選的 walltime），也可以不設限制，或共享父級的舊式 iteration budget。內建子代理會帶一組保守的最小執行期預算：turn 軟/硬限制 `40/60`、工具呼叫軟/硬限制 `75/100`，並且沒有 walltime 限制。

第二個理由是：**專門化**。一個專門為審查決策而提示的 `critic` 子代理，通常會比讓一般 agent 順手兼做 review 來得更好。子代理讓你可以把專家接進通才型工作流，而不用重寫那個通才。

## 我們怎麼定義它

子代理 = 一份生物設定 + 一個父層 registry。當它被派生時：

- 它會繼承父生物的 LLM 與工具格式；
- 它會拿到一部分工具（定義於子代理設定中的 `tools` 清單）；
- 它會跑完整的 Agent 生命週期（start → event-loop → stop）；
- 它可以有自己的統一 `budget` 外掛選項（`turn_budget`、`tool_call_budget`，以及可選的 `walltime_budget`）；
- 它也可以繼承父級舊式 iteration budget、拿到自己的 `budget_allocation`，或完全不共享預算；
- 它的結果會以父層上的 `subagent_output` 事件送達，
  或在 `output_to: external` 時直接串流給使用者。

有三種重要型態：

- **One-shot**（預設）——派生後執行到完成，只回傳一次。
- **輸出型子代理**（`output_to: external`）——它的文字會和控制器的文字並行（或取而代之）串流到父生物的 `OutputRouter`。你可以把它想成：控制器在背後默默協調；真正讓使用者讀到的是子代理。
- **互動型**（`interactive: true`）——跨多輪持續存在，會接收上下文更新，也能被餵入新提示。適合那些能從對話連續性中受益的專家（持續運作的 reviewer、長駐 planner）。

## 我們怎麼實作它

`SubAgentManager`（`modules/subagent/manager.py`）會把 `SubAgent`（`modules/subagent/base.py`）派生成 `asyncio.Task`，依 job id 追蹤它們，並把完成結果作為 `TriggerEvent` 送出。

深度由 `max_subagent_depth`（設定層級）限制，以防止遞迴失控。取消採合作式機制——父生物可以呼叫 `stop_task` 中斷正在執行的子代理。

執行期預算由統一的 `budget` 外掛執行，並透過 `plugins[].options` 設定 `turn_budget`、`tool_call_budget` 以及可選的 `walltime_budget`。自動壓縮另外透過 `auto-compact` 外掛包啟用（它展開為 `compact.auto`）。舊式共享 iteration budget 在派生時解析：`budget_allocation` 優先，否則 `budget_inherit: true` 會在存在父級預算時複用同一個預算物件。

內建子代理（位於 `kt-biome` + framework）：`worker`、`plan`、`explore`、`critic`、`response`、`research`、`summarize`、`memory_read`、`memory_write`、`coordinator`。

## 因此你可以做什麼

- **規劃 / 實作 / 審查。** 一個父生物配三個子代理。父生物負責協調；每個子代理專注在單一階段。
- **靜默控制器。** 父生物對 `response` 子代理使用 `output_to: external`。控制器本身不輸出文字；只有子代理的回覆會到達使用者。這就是多數 kt-biome 聊天型生物的工作方式。
- **常駐專家。** 一個 `interactive: true` 的 reviewer，看見每一輪，只有在它有話要說時才開口。
- **巢狀生態瓶。** 子代理可以用 `terrarium_create` 啟動一個生態瓶。底層基礎設施不在乎。
- **縱向包在橫向裡。** 一個生態瓶中的生物本身還會使用子代理——混合兩種多代理軸向。

## 不要被它框住

子代理是可選的。對大多數短任務來說，只有工具的生物就已經夠用。而且既然「子代理」在概念上就是「其實作剛好是一整隻 agent 的工具」，兩者的界線本來就會模糊：某個工具完全可以在 Python 裡派生一隻 agent，而從 LLM 的角度看，這和呼叫子代理沒有差別。

## 另見

- [工具](tool.md) ——「它也是一種工具」這個視角。
- [多代理概覽](../multi-agent/README.md) —— 縱向（子代理）與橫向（生態瓶）的差異。
- [模式——靜默控制器](../patterns.md) —— 輸出型子代理這個慣用法。
- [子代理指南](../../guides/sub-agents.md) —— 設定內建/內聯子代理、預算與執行期外掛。
- [reference/builtins.md — Sub-agents](../../reference/builtins.md) —— 內建子代理工具包。
