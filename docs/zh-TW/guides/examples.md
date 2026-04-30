---
title: 範例
summary: 快速瀏覽隨附的範例生物、生態瓶與程式碼，了解應該先看哪些內容，以及原因。
tags:
  - guides
  - examples
---

# 範例

適合想找可執行程式碼與設定來學習的讀者。

`examples/` 目錄依類型整理可執行內容：獨立代理設定、生態瓶設定、外掛實作，以及將框架嵌入其中的 Python 腳本。每個資料夾都示範了一種你可以直接複製或繼承的模式。

概念導讀：[boundaries](../concepts/boundaries.md) —— 範例刻意涵蓋系統邊界情況。

## `examples/agent-apps/` —— 獨立生物

單一生物設定。執行方式：

```bash
kt run examples/agent-apps/<name>
```

| Agent | 模式 | 示範內容 |
|---|---|---|
| `swe_agent` | 程式開發代理 | 偏重工具使用的生物，接近 `kt-biome/creatures/swe` |
| `discord_bot` | 群組聊天機器人 | 自訂 Discord I/O、短暫型、原生工具呼叫 |
| `planner_agent` | 規劃－執行－反思 | 草稿區狀態機 + 評審子代理 |
| `monitor_agent` | 觸發器驅動 | `input: none` + 計時器觸發器，沒有使用者介入 |
| `conversational` | 串流 ASR/TTS | Whisper 輸入、TTS 輸出、互動式子代理 |
| `rp_agent` | 角色扮演 | 以記憶為優先的設計、啟動觸發器、角色提示詞 |
| `compact_test` | 壓縮壓力測試 | 小型上下文 + 自動壓縮，用來驗證壓縮流程 |

相關指南：[Creatures](creatures.md)、[Configuration](configuration.md)。

## `examples/terrariums/` —— 多代理生態瓶設定

```bash
kt terrarium run examples/terrariums/<name>
```

| Terrarium | 拓樸 | 生物 |
|---|---|---|
| `novel_terrarium` | 帶回饋的管線 | brainstorm → planner → writer |
| `code_review_team` | 帶關卡的迴圈 | developer、reviewer、tester |
| `research_assistant` | 星狀加協調者 | coordinator + searcher + analyst |

相關指南：[Terrariums](terrariums.md)。

## `examples/plugins/` —— 外掛 hooks

每個 hook 類別各有一個範例。撰寫自己的外掛時，可把它們當成參考。

| Plugin | Hooks | 等級 |
|---|---|---|
| `hello_plugin` | `on_load`、`on_agent_start/stop` | 初學 |
| `tool_timer` | `pre/post_tool_execute`、state | 初學 |
| `tool_guard` | `pre_tool_execute`、`PluginBlockError` | 進階入門 |
| `prompt_injector` | `pre_llm_call`（訊息變更） | 進階入門 |
| `response_logger` | `post_llm_call`、`on_event`、`on_interrupt` | 進階入門 |
| `budget_enforcer` | `pre/post_llm_call` 搭配阻擋與 state | 進階 |
| `subagent_tracker` | `pre/post_subagent_run`、`on_task_promoted` | 進階 |
| `webhook_notifier` | Fire-and-forget 回呼、`inject_event`、`switch_model` | 進階 |

相關指南：[Plugins](plugins.md)。完整逐欄位說明請見 `examples/plugins/README.md`。

## `examples/code/` —— Python 嵌入

這些腳本示範如何把框架嵌入你的程式中，並由你的程式碼擔任協調者。每個範例都使用 compose algebra 的不同片段，或 `Terrarium` / `Creature` / `Studio` / 底層 `Agent` API。

| Script | 模式 | 使用的功能 |
|---|---|---|
| `programmatic_chat.py` | 將 Creature 當作函式庫使用 | `Creature.chat()` |
| `run_terrarium.py` | 以程式碼建立 Terrarium | `Terrarium`、頻道注入 |
| `discord_adventure_bot.py` | 由 Bot 擁有互動流程 | `agent()`、動態建立、遊戲狀態 |
| `debate_arena.py` | 多代理輪流互動 | `agent()`、`>>`、`async for`、持久代理 |
| `task_orchestrator.py` | 動態代理拓樸 | `factory()`、`>>`、`asyncio.gather` |
| `ensemble_voting.py` | 以多樣性實現冗餘 | `&`、`>>` 自動包裝、`\|`、`*` |
| `review_loop.py` | 撰寫 → 審查 → 修訂 | `.iterate()`、持久 `agent()` |
| `smart_router.py` | 分類並派送 | `>> {dict}` 路由、`factory()`、`\|` 後備 |
| `pipeline_transforms.py` | 資料擷取管線 | `>>` 自動包裝（`json.loads`、lambda）、代理 + 函式 |

相關指南：[Programmatic Usage](programmatic-usage.md)、[Composition](composition.md)。

## 新讀者建議閱讀順序

1. **先跑一個。** `kt run examples/agent-apps/swe_agent` —— 先感受生物如何運作。
2. **再從它繼承。** 複製資料夾、調整 `config.yaml`，然後重新執行。
3. **加入外掛。** 把 `examples/plugins/tool_timer.py` 加到你的生物 `plugins:` 清單中。
4. **進入 Python。** 打開 `examples/code/programmatic_chat.py` 並執行它。
5. **試試組合。** 用 `examples/code/review_loop.py` 看 compose algebra 如何運作。
6. **改跑多代理。** 執行 `examples/terrariums/code_review_team`，觀察頻道流量。

## 另請參閱

- [Getting Started](getting-started.md) —— 環境設定。
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome) —— 展示用套件；許多範例與它共用相同模式。
- [Tutorials](../tutorials/README.md) —— 與這些範例搭配的引導式教學。