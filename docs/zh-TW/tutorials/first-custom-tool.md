---
title: 第一個自訂工具
summary: 撰寫 Python 工具、註冊它，並把它接進生物的設定。
tags:
  - tutorials
  - tool
  - extending
---

# 第一個自訂工具

**問題：**你的代理需要某種內建工具沒有提供的能力。你想替它加入一個可供 LLM 呼叫的新函式。

**完成狀態：**你會有一個 `BaseTool` 子類別，放在你的生物資料夾中，透過 `config.yaml` 接線，在執行時載入，並在收到請求時由 LLM 呼叫。

**先決條件：**[第一個生物](first-creature.md)。你應該已經有一個屬於自己的生物資料夾。

這裡的工具範例是一個很簡單的 `wordcount` —— 用來計算字串中的單字數。重點在於形狀，不在邏輯本身。若想了解工具除了簡單函式之外還*可以*是什麼，請參考[工具概念](../concepts/modules/tool.md)。

## 步驟 1 —— 選一個資料夾

建立一個會擁有這個工具的生物資料夾。我們把它命名為 `creatures/tutorial-creature/`。工具原始碼會和設定檔放在一起：

```text
creatures/tutorial-creature/
  config.yaml
  prompts/
    system.md
  tools/
    wordcount.py
```

建立目錄：

```bash
mkdir -p creatures/tutorial-creature/prompts
mkdir -p creatures/tutorial-creature/tools
```

## 步驟 2 —— 撰寫工具

`creatures/tutorial-creature/tools/wordcount.py`：

```python
"""Word count tool — counts words in a given text."""

from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)


class WordCountTool(BaseTool):
    """Count the words in a string."""

    @property
    def tool_name(self) -> str:
        return "wordcount"

    @property
    def description(self) -> str:
        # One line — goes straight into the system prompt.
        return "Count the words in a given piece of text."

    @property
    def execution_mode(self) -> ExecutionMode:
        # Pure, fast, in-memory — direct mode. See Step 5.
        return ExecutionMode.DIRECT

    # The JSON schema the LLM sees for args.
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to count words in.",
            }
        },
        "required": ["text"],
    }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        text = args.get("text", "")
        count = len(text.split())
        return ToolResult(
            output=f"{count} words",
            metadata={"count": count},
        )
```

重點如下：

- 繼承 `BaseTool`。實作 `tool_name`、`description` 與 `_execute`。`BaseTool` 上公開的 `execute` 包裝器已經處理好 try/except，發生例外時會回傳 `ToolResult(error=...)`。
- `parameters` 是與 JSON Schema 相容的 dict。controller 會用它來建立讓 LLM 看見的工具 schema。
- `_execute` 是 async。請回傳 `ToolResult`。`output` 可以是字串，也可以是 `ContentPart` 清單，用於多模態結果。
- 如果你的工具需要工作目錄 / 工作階段 / scratchpad，請在類別上設定 `needs_context = True`，並在 `_execute` 中接受 `context` 關鍵字參數。完整的 `ToolContext` 介面請參考[工具概念](../concepts/modules/tool.md)。

## 步驟 3 —— 把它接進生物設定

`creatures/tutorial-creature/config.yaml`：

```yaml
name: tutorial_creature
version: "1.0"
base_config: "@kt-biome/creatures/general"

system_prompt_file: prompts/system.md

tools:
  - name: wordcount
    type: custom
    module: ./tools/wordcount.py
    class_name: WordCountTool
```

各欄位的作用：

- `type: custom` —— 從本機 Python 檔案載入（而不是 `builtin` 或 `package`）。
- `module` —— `.py` 檔案的路徑，會以代理資料夾（`creatures/tutorial-creature/`）為相對基準解析。
- `class_name` —— 該模組中的類別名稱。

由於 `tools:` 會延伸繼承而來的清單，因此你會保留完整的 `general` 工具集，並在其上額外加入 `wordcount`。

`creatures/tutorial-creature/prompts/system.md`：

```markdown
# Tutorial Creature

You are a helpful assistant for text experiments. When a user asks
about word counts, prefer the `wordcount` tool.
```

## 步驟 4 —— 執行並試用

```bash
kt run creatures/tutorial-creature --mode cli
```

對它下提示：

```text
> Count the words in "hello world foo bar"
```

controller 應該會以 `text="hello world foo bar"` 呼叫 `wordcount`，並顯示結果（`4 words`）。退出時，`kt` 會印出平常的恢復提示。如果你需要穩定地看到它觸發，請使用新的工作階段（可加上 `--no-session` 來做一次性的執行）。

## 步驟 5 —— 選對執行模式

工具有三種執行模式：

| 模式 | 何時使用 | 內建範例 |
|---|---|---|
| `DIRECT` | 快速、純粹，且能在本輪內完成。結果會在下一次 LLM 呼叫前被等待完成。 | `wordcount`、`read`、`grep` |
| `BACKGROUND` | 執行時間較長（數秒以上）。會回傳工作控制代碼；結果稍後會以事件形式抵達。LLM 可以繼續工作。 | `bash`（長命令）、sub-agents |
| `STATEFUL` | 多輪互動。工具會 yield；代理回應；工具再 yield。 | 有狀態精靈、REPL |

`BaseTool` 預設是 `BACKGROUND`。當這個預設不正確時，請覆寫 `execution_mode`（就像範例那樣）。純計算、耗時低於 100ms 的工具應該設為 `DIRECT`。

執行管線位於[工具概念 —— 我們如何實作](../concepts/modules/tool.md#how-we-implement-it)。串流在解析到結束區塊後就會立即啟動工具；多個 `DIRECT` 工具會透過 `asyncio.gather` 並行執行。

## 步驟 6 —— 用 ScriptedLLM 測試它（選用）

在單元測試中，你可以用可重現的 LLM 來驅動 controller。`kohakuterrarium.testing` 套件內建了幾個輔助工具：

```python
import asyncio

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


async def test_wordcount() -> None:
    agent = Agent.from_path("creatures/tutorial-creature")
    agent.llm = ScriptedLLM([
        ScriptEntry('[/wordcount]{"text": "one two three"}[wordcount/]'),
        ScriptEntry("Done — 3 words."),
    ])

    await agent.start()
    try:
        await agent.inject_input("count words in 'one two three'")
    finally:
        await agent.stop()


asyncio.run(test_wordcount())
```

腳本中的工具呼叫語法取決於該生物的 `tool_format`（`bracket` / `xml` / `native`）。若是 native function calling，請使用 provider 對應形狀的呼叫；若是 `bracket`（SWE 生物祖先的預設值），則使用 `[/name]{json}[name/]`。

`OutputRecorder`、`EventRecorder` 與 `TestAgentBuilder` 可參見 `src/kohakuterrarium/testing/`。

## 你學到了什麼

- 工具就是一個 `BaseTool` 子類別，包含 `tool_name`、`description`、`parameters` 與 `_execute`。
- `config.yaml` 中的 `tools:` 會透過 `type: custom`、`module:` 與 `class_name:` 把它接進來。
- 執行模式很重要 —— 快速而純粹的工作選 `DIRECT`，耗時較長的工作選 `BACKGROUND`。
- 測試時可用 `ScriptedLLM` 以可重現的方式驅動整個流程。

## 接下來讀什麼

- [工具概念](../concepts/modules/tool.md) —— 工具*可以*是什麼（訊息匯流排、狀態控制代碼、代理包裝器等）。
- [自訂模組指南](../guides/custom-modules.md) —— 一起看工具、sub-agents、triggers 與 outputs。
- [第一個外掛](first-plugin.md) —— 當你要的行為發生在模組之間的接縫，而不是單一模組內部時。