---
title: 串流剖析器
summary: 以狀態機將 LLM 輸出解析為文字、工具呼叫、子代理派送與 framework commands。
tags:
  - concepts
  - impl-notes
  - parser
---

# 串流剖析器

## 這要解決的問題

當 LLM 在串流中途輸出一個工具呼叫時，框架應該在什麼時候開始執行它？

有兩種選擇：

1. **等到輪次結束。** 收集所有工具呼叫；一次批次派送；取得結果；
   然後可能再進行一次 LLM 呼叫。
2. **區塊一關閉就立刻派送。** 每個工具都會與 LLM 其餘輸出並行執行；
   到 LLM 講完時，有些工具可能已經完成。

方案 2 的回應速度顯著更好——尤其是在長串流輪次且包含多個工具呼叫時——
而這正是框架採用的做法。

## 曾考慮的方案

- **輪次後派送。** 較簡單，但浪費了串流視窗；工具只能排在 LLM 後面。
- **推測式派送。** 在 LLM 串流時就開始跑工具；如果後來發現區塊不完整
  再取消。錯誤風險太高。
- **在區塊關閉時，以確定性的狀態機派送。** 這就是我們實際的做法。
  僅在文字區塊完成解析時啟動工具；絕不對部分輸入執行。

## 我們實際怎麼做

LLM 輸出的串流會逐塊送入 parser 狀態機。Parser 會依照目前設定的
`tool_format`，追蹤三種巢狀區塊：

- **工具呼叫** — 例如在 bracket（預設）格式中是
  `[/bash]@@command=ls\n[bash/]`；在 XML 中是 `<bash command="ls"></bash>`；
  在 native 中則是 LLM provider 自己的 function-calling envelope。
- **子代理派送** — 使用相同的格式家族，只是改用 agent tag。
- **Framework commands** — `info`、`jobs`、`wait`
  （以及在 parser 的 DEFAULT_COMMANDS 集合中的 `read_job`）。
  這些和工具呼叫共用相同的 bracket/XML 框架。關於格式如何設定，
  請參閱 [modules/tool — formats](../modules/tool.md) 與
  [modules/plugin](../modules/plugin.md)。

當一個區塊關閉時，parser 會在其輸出 generator 上發出事件。
控制器接著做出反應：

- `TextEvent` → 串流到輸出。
- `ToolCallEvent` → `Executor.submit_from_event(event, is_direct=True)`
  → `asyncio.create_task(tool.execute(...))`。立即返回。
- `SubAgentCallEvent` → 類似處理，但走 `SubAgentManager.spawn`。
- `CommandEvent` → 直接就地執行（讀取 job 輸出、載入文件等）；
  這些操作很快且具確定性。

在串流結束時，控制器會等待所有在串流期間啟動的 `direct` jobs，
將其結果收集為 `tool_complete` 事件，並在下一輪回饋給 LLM。

## 維持不變的條件

- **每個已關閉區塊只派送一次。** 部分區塊絕不執行。
- **同一輪中的多個工具會並行執行。** 對它們的 tasks 做 `gather`，
  而不是依序執行。
- **LLM 串流不會被工具執行阻塞。** LLM 持續輸出；工具在旁並行執行。
- **背景工具不會讓輪次維持開啟。** 被標記為 background 的工具，
  會先以 job id 作為占位結果返回；控制器繼續前進；真正結果會在之後
  以事件形式送達。

## 程式碼中的位置

- `src/kohakuterrarium/parsing/` — parser 狀態機；每種 tool-format
  變體（bracket、XML、native）各有一個模組。
- `src/kohakuterrarium/core/controller.py` — 消費 parser 事件。
- `src/kohakuterrarium/core/executor.py` — 把工具執行包成 tasks。
- `src/kohakuterrarium/core/agent_tools.py` — submit-from-event 路徑，
  將 parser 輸出接到 executor。

## 另請參閱

- [Composing an agent](../foundations/composing-an-agent.md) — 從輪次層級
  理解本頁所放大的流程。
- [Tool](../modules/tool.md) — 執行模式（direct / background /
  stateful）。
