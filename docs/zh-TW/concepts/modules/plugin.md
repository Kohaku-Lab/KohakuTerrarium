---
title: Plugin
summary: 在不 fork 模組的前提下修改模組之間的連接方式——prompt plugins 與 lifecycle plugins。
tags:
  - concepts
  - module
  - plugin
---

# Plugin

## 它是什麼

**plugin** 改的是 *模組之間的連接*，不是模組本身。模組是積木；plugin 則是跑在接縫上的東西。

它有兩種 flavour，各自解決不同問題：

- **Prompt plugins**：在控制器建構 system prompt 時，往裡面補內容。
- **Lifecycle plugins**：掛進執行期事件——例如 LLM 呼叫前後、工具呼叫前後、子代理產生前後。

合起來看，plugin 是在 *不 fork 任何模組* 的前提下加行為的主要方式。

## 為什麼它存在

大多數實用的 agent 行為，既不是新工具，也不是新 LLM——而是一條跑在它們之間的規則。例如：

- 「每次 bash 呼叫前，都先用安全政策檢查一次。」
- 「每次 LLM 呼叫後，都統計 token 方便計費。」
- 「每次 LLM 呼叫前，都把相關的歷史事件撈出來注入訊息裡。」
- 「永遠在 system prompt 前面加上一段專案專屬指示。」

這些事情都可以靠 subclass 某個模組來做，但那樣既侵入又脆弱——你 fork 了、上游改了、你就得 rebase。plugin 讓你可以碰接縫，不必動積木。

## 我們怎麼定義它

### Prompt plugins

一個 `BasePlugin` subclass，具備：

- `name` 與 `priority`（數值越低，越早出現在 prompt 裡）
- `get_content(context) → str | None`，回傳一段 prompt 文字（若回傳 `None`，代表不提供任何內容）

聚合器（`prompt/aggregator.py`）會依照 priority 排序已註冊的 plugins，然後把它們的輸出串接成最終的 system prompt。

內建的有：`ToolListPlugin`（自動工具索引）、`FrameworkHintsPlugin`（如何呼叫工具／使用 `##commands##`）、`EnvInfoPlugin`（working dir、日期、平台）、`ProjectInstructionsPlugin`（載入 `CLAUDE.md` / `.claude/rules.md`）。

### Lifecycle plugins

一個 `BasePlugin` subclass，可以實作以下任意 hooks：

- `on_load(context)`, `on_unload()`
- `pre_llm_call(messages, **kwargs) → list[dict] | None`
- `post_llm_call(response) → ChatResponse | None`
- `pre_tool_execute(name, args) → dict | None`
- `post_tool_execute(name, result) → ToolResult | None`
- `pre_subagent_run(name, context) → dict | None`
- `post_subagent_run(name, output) → str | None`
- Fire-and-forget：`on_tool_start`, `on_tool_end`, `on_llm_start`,
  `on_llm_end`, `on_processing_start`, `on_processing_end`,
  `on_startup`, `on_shutdown`, `on_compact_start`,
  `on_compact_complete`, `on_event`。

`pre_*` hook 可以丟出 `PluginBlockError("message")` 來中止操作——那段訊息會變成工具結果，或是一個被阻擋的 `tool_complete` 事件。

## 我們怎麼實作它

`PluginManager.notify(hook, **kwargs)` 會迭代所有已註冊且已啟用的 plugins，並依序 await 每一個有對應方法的實作。`bootstrap/plugins.py` 會在 agent 啟動時載入 config 宣告的 plugins；package 宣告的 plugins 則可透過 `kohaku.yaml` 被發現。

## 因此你可以做什麼

- **安全護欄。** 用 `pre_tool_execute` plugin 拒絕危險指令。
- **Token 記帳。** 用 `post_llm_call` 統計 token 並寫進外部儲存。
- **無縫記憶。** 用 `pre_llm_call` 對歷史事件做 embedding lookup，把相關上下文插到前面——本質上就是不透過工具呼叫，直接對 session history 做 RAG。
- **智慧護欄。** 用 `pre_tool_execute` plugin 跑一個小型的 *nested agent*，判斷某個動作能不能做。plugin 是 Python，agent 也是 Python，所以這是合法的。參見 [patterns](../patterns.md)。
- **Prompt 組合。** 用 prompt plugin 注入由 scratchpad state 或 session metadata 動態推導出的指示。

## 不要被邊界綁住

plugin 是可選的。沒有 plugin 的生物也能正常運作。但當你開始覺得「我需要一種遍佈整個迴圈的新行為」，答案幾乎總是 plugin，而不是新模組。

## 延伸閱讀

- [Controller](controller.md) — hooks 在哪裡觸發。
- [Prompt aggregation](../impl-notes/prompt-aggregation.md) — prompt plugins 怎麼插進去。
- [Patterns — smart guard, seamless memory](../patterns.md) — plugin 裡包 agent。
- [reference/plugin-hooks.md](../../reference/plugin-hooks.md) — 每個 hook 的簽章。
