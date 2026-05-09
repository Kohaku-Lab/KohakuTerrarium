---
title: 工具
summary: LLM 可呼叫的具名能力——shell 命令、檔案編輯、網頁搜尋等等。
tags:
  - concepts
  - module
  - tool
---

# 工具

## 它是什麼

**工具 (tool)** 是 agent *做事* 的方式。它是一種向控制器註冊的可執行能力，LLM 可以用名稱加上參數來呼叫它。

在多數人的心智模型裡，工具就是「LLM 可以呼叫的函式」：`bash`、`read`、`write`、`grep`、`web_search`。這樣說沒錯，但還不完整。工具也可以是通往另一個 agent 的訊息匯流排、狀態機控制柄、巢狀生物、權限閘門，或同時兼具這些身分。

## 為什麼它存在

聊天機器人只有嘴。工具讓 agent 長出手。沒有工具時，LLM 只能說話；有了工具，它就能在世界裡做各種工作。

這個框架的工作，是讓工具執行變成**容易使用，也容易撰寫**：感知串流的派發、平行執行、上下文傳播、背景作業，以及型別化中繼資料。每個既有 agent 產品幾乎都會重做其中某個子集；把它一次做好放進底層，就不用一直重複造輪子。

## 我們怎麼定義它

一個工具會實作：

- 一個**名稱**與簡短描述（自動插入 system prompt）
- 一份 **args schema**（`parameters`），相容於 JSON Schema
- 一個非同步 **`execute(args, context)` → `ToolResult`**
- 一種**執行模式**：`direct`（預設）、`background` 或 `stateful`
- 可選的**完整文件**（`get_full_documentation()`），透過 `info` 框架命令按需載入

執行模式：

- **Direct** —— 在同一輪中等待工具完成；結果會作為 `tool_complete` 事件回饋。
- **Background** —— 提交後立即返回；結果會在之後的事件中送達。
- **Stateful** —— 跨多輪互動；像 generator 一樣的工具，可產出中間結果供 agent 回應。

## 我們怎麼實作它

工具會註冊到 `Registry`（`core/registry.py`）。控制器的 stream parser 會在工具區塊結束時偵測到它，並立刻呼叫 `Executor.submit_from_event(...)`。executor 會建立 `asyncio.Task`；多個工具可平行執行。

每次工具執行都會收到一個 `ToolContext`，其中帶有：

- 生物的工作目錄；
- 工作階段（草稿區、私有頻道）；
- 環境（共用頻道，如果有的話）；
- 檔案防護（先讀後寫、路徑安全）；
- 檔案讀取狀態（用於去重）；
- agent 名稱；
- job store（讓 `wait` / `read_job` 框架命令能找到這個工具的作業）。

內建工具包含 shell（`bash`）、Python（`python`）、檔案操作（`read`、`write`、`edit`、`multi_edit`）、搜尋（`glob`、`grep`、`tree`）、JSON（`json_read`、`json_write`）、Web（`web_fetch`、`web_search`）、通訊（`send_message`）、記憶（`scratchpad`、`search_memory`）、內省（`info`、`stop_task`），以及圖管理（`group_add_node`、`group_channel`、`group_wire`、…；只註冊在特權節點上）。

## 因此你可以做什麼

- **把工具當成訊息匯流排。** `send_message` 會寫入某個頻道；另一隻生物上的 `ChannelTrigger` 會讀取它。兩個工具加上一個 trigger，就能重現群聊模式，而不需要新增任何原語。
- **把工具當成狀態控制柄。** `scratchpad` 工具就是典型的 KV API；任何協作中的工具都可以透過它會合。
- **會安裝 trigger 的工具。** 任何通用 trigger 類別（預設為 `TimerTrigger`、`ChannelTrigger`、`SchedulerTrigger`）都能以工具形式暴露——在 `tools:` 下列出 `type: trigger`，就會讓 `add_timer` / `watch_channel` / `add_schedule` 出現在工具清單中，而呼叫它就會把該 trigger 安裝到活躍的 `TriggerManager` 上。`group_add_node` 則會把一隻新的生物生成到呼叫者的圖裡。
- **包裝子代理的工具。** 任何子代理呼叫本身就是工具形狀，因為 LLM 仍然是用名稱加參數去呼叫它。
- **會執行 agent 的工具。** 因為工具就是普通 Python，某個工具可以內含一隻 agent——例如先用一個小型判斷 agent 檢查參數，再派發真正動作的 guard 工具。參見 [patterns](../patterns.md)。

## 不要被它框住

工具不必是「純函式」。它們可以改變狀態、啟動長時間工作、和其他生物協調，或編排整個生態瓶。它們也不必很直觀：一個唯一效果只是把工作階段標記成「準備好壓縮了」的工具，也完全合理。抽象的重點只是「LLM 可以呼叫的某個東西」；至於呼叫背後發生什麼，框架不會替你設限。

## 另見

- [impl-notes/stream-parser](../impl-notes/stream-parser.md) —— 為什麼工具會在 LLM 停止前就開始執行。
- [子代理](sub-agent.md) —— 那個「它也是一種工具」的兄弟概念。
- [頻道](channel.md) —— 把工具當訊息匯流排的另一半。
- [模式](../patterns.md) —— 工具的各種非常規用法。
- [reference/builtins.md — Tools](../../reference/builtins.md) —— 完整目錄。
