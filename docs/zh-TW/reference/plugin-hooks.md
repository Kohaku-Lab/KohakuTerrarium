---
title: 外掛 hooks
summary: 外掛可註冊的所有生命週期 hook、觸發時機，以及會收到的 payload。
tags:
  - reference
  - plugin
  - hooks
---

# 外掛 hooks

這份文件整理所有暴露給外掛的生命週期、LLM、工具、子代理與 callback hook。這些 hook 由 `kohakuterrarium.modules.plugin` 裡的 `Plugin` protocol 定義；`BasePlugin` 提供預設的 no-op 實作。實際接線點在 `bootstrap/plugins.py`。

心智模型請先讀 [concepts/modules/plugin](../concepts/modules/plugin.md)。任務導向說明請看 [guides/plugins](../guides/plugins.md) 與 [guides/custom-modules](../guides/custom-modules.md)。

## 回傳值語意

- **轉換型 hooks**（`pre_*`、`post_*`）：回傳 `None` 代表保持原值不變；回傳新值則會取代傳入下一個外掛或框架的值。
- **Callback hooks**（`on_*`）：回傳值會被忽略；屬於 fire-and-forget。

## 阻擋

任何 `pre_*` hook 都可以丟出 `PluginBlockError` 來短路該操作。框架會把錯誤往外呈現，請求不再繼續，且對應的 `post_*` hook **不會** 觸發。Callback hooks 不能阻擋。

---

## 生命週期 hooks

| Hook | 簽章 | 觸發時機 | 回傳值 |
|---|---|---|---|
| `on_load` | `async on_load(ctx: PluginContext) -> None` | 外掛被載入到 agent 時。 | 忽略 |
| `on_unload` | `async on_unload() -> None` | 外掛被卸載，或 agent 停止時。 | 忽略 |

`PluginContext` 讓外掛能存取 agent、其設定、scratchpad 與 logger。詳細結構請看 `kohakuterrarium.modules.plugin.context`。

---

## LLM hooks

| Hook | 簽章 | 觸發時機 | 回傳語意 |
|---|---|---|---|
| `pre_llm_call` | `async pre_llm_call(messages: list[dict], **kwargs) -> list[dict] \| None` | 每次 LLM 請求前（controller、sub-agent、compact 都會走）。 | `None` 保留原訊息；回傳新 list 則取代。可丟出 `PluginBlockError`。 |
| `post_llm_call` | `async post_llm_call(response: ChatResponse) -> ChatResponse \| None` | LLM 回應組裝完成後。 | `None` 保留原回應；回傳新 `ChatResponse` 則取代。 |

---

## 工具 hooks

| Hook | 簽章 | 觸發時機 | 回傳語意 |
|---|---|---|---|
| `pre_tool_execute` | `async pre_tool_execute(name: str, args: dict) -> dict \| None` | 工具送進 executor 前。 | `None` 保留 `args`；回傳新 dict 則取代。可丟出 `PluginBlockError`。 |
| `post_tool_execute` | `async post_tool_execute(name: str, result: ToolResult) -> ToolResult \| None` | 工具完成後（包含錯誤結果）。 | `None` 保留結果；回傳新 `ToolResult` 則取代。 |

---

## 子代理 hooks

| Hook | 簽章 | 觸發時機 | 回傳語意 |
|---|---|---|---|
| `pre_subagent_run` | `async pre_subagent_run(name: str, ctx: SubAgentContext) -> dict \| None` | 子代理被建立並啟動前。 | `None` 保留啟動上下文；回傳 dict 會 merge 成覆寫值。可丟出 `PluginBlockError`。 |
| `post_subagent_run` | `async post_subagent_run(name: str, output: str) -> str \| None` | 子代理完成後（其輸出即將以 `subagent_output` 事件送回）。 | `None` 保留輸出；回傳新字串則取代。 |

---

## Callback hooks

所有 callback 都是 fire-and-forget。回傳值會被忽略。它們由 plugin scheduler 並行執行；慢速 callback 不會阻塞 agent。

| Hook | 簽章 | 觸發時機 |
|---|---|---|
| `on_tool_start` | `async on_tool_start(name: str, args: dict) -> None` | 工具執行即將開始。 |
| `on_tool_end` | `async on_tool_end(name: str, result: ToolResult) -> None` | 工具執行完成。 |
| `on_llm_start` | `async on_llm_start(messages: list[dict]) -> None` | LLM 請求送出。 |
| `on_llm_end` | `async on_llm_end(response: ChatResponse) -> None` | LLM 回應收到。 |
| `on_processing_start` | `async on_processing_start() -> None` | Agent 進入處理回合。 |
| `on_processing_end` | `async on_processing_end() -> None` | Agent 離開處理回合。 |
| `on_startup` | `async on_startup() -> None` | Agent `start()` 完成。 |
| `on_shutdown` | `async on_shutdown() -> None` | Agent `stop()` 執行中。 |
| `on_compact_start` | `async on_compact_start(reason: str) -> None` | 開始 compact。 |
| `on_compact_complete` | `async on_compact_complete(summary: str) -> None` | compact 完成。 |
| `on_event` | `async on_event(event: TriggerEvent) -> None` | 任意事件被注入 controller。 |

---

## Prompt plugins（獨立類別）

Prompt plugins 在 system prompt 組裝過程中執行，實作位於 `prompt/aggregator.py`。它們與 lifecycle plugins 分開載入。

`BasePlugin`（位於 `kohakuterrarium.prompt.plugins`）具備：

```python
priority: int       # 越小越早
name: str
async def get_content(self, context: PromptContext) -> str | None
```

- `get_content(context) -> str | None`：回傳要插入的文字區塊；回傳 `None` 代表不提供內容。
- `priority`：排序鍵。內建外掛大致落在 50/45/40/30。

內建 prompt plugins 請看 [builtins.md — Prompt plugins](builtins.md#prompt-plugins)。

自訂 prompt plugin 一樣透過 creature config 的 `plugins` 欄位註冊；框架會依 plugin class 是 subclass 自 lifecycle `Plugin` protocol，還是 prompt `BasePlugin`，來決定走哪一套調度。

---

## 撰寫外掛

最小的 lifecycle plugin 範例：

```python
from kohakuterrarium.modules.plugin import BasePlugin, PluginBlockError

class GuardPlugin(BasePlugin):
    async def pre_tool_execute(self, name, args):
        if name == "bash" and "rm -rf" in args.get("command", ""):
            raise PluginBlockError("unsafe command")
        return None  # 保持 args 不變
```

在 creature config 中註冊：

```yaml
plugins:
  - name: guard
    type: custom
    module: ./plugins/guard.py
    class: GuardPlugin
```

執行時可透過 `/plugin toggle guard`（見 [builtins.md — User commands](builtins.md#user-commands)）或 HTTP 的外掛切換端點啟用／停用。

---

## 延伸閱讀

- Concepts: [plugin](../concepts/modules/plugin.md)、[patterns](../concepts/patterns.md)
- Guides: [plugins](../guides/plugins.md)、[custom modules](../guides/custom-modules.md)
- Reference: [python](python.md)、[configuration](configuration.md)、[builtins](builtins.md)
