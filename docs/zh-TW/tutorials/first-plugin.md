---
title: 第一個外掛
summary: 建立生命週期外掛，掛接 pre/post tool execution 以阻擋或增強呼叫。
tags:
  - tutorials
  - plugin
  - extending
---

# 第一個外掛

**問題：**你需要一種不屬於任何單一模組的行為 —— 例如替每次 LLM 呼叫注入脈絡，或在所有地方攔截某種工具呼叫模式。這時新工具不是對的形狀，新 output 模組也不是對的形狀；外掛才是對的形狀。

**完成狀態：**你會透過 `config.yaml` 在一個生物中接上兩個可運作的外掛：

1. 一個**脈絡注入器**，把目前 UTC 時間作為簡短的 system message 加到每次 LLM 呼叫中。
2. 一個**工具守衛**，阻擋任何包含 `rm -rf` 的 `bash` 呼叫，並回傳模型可讀的說明性錯誤。

**先決條件：**[第一個生物](first-creature.md)，最好也看過[第一個自訂工具](first-custom-tool.md) —— 你應該已經熟悉如何編輯生物的 `config.yaml`，以及把 Python 檔案放在它旁邊。

外掛修改的是**模組之間的連接**，不是模組本身。關於這條邊界為什麼存在，請參考[外掛概念](../concepts/modules/plugin.md)。

## 步驟 1 —— 選一個資料夾

沿用你已經有的生物，或建立一個新的：

```text
creatures/tutorial-creature/
  config.yaml
  plugins/
    utc_injector.py
    bash_guard.py
```

```bash
mkdir -p creatures/tutorial-creature/plugins
```

下面兩個外掛都是生命週期外掛 —— 它們會繼承 `kohakuterrarium.modules.plugin.base` 中的 `BasePlugin`。這就是能透過生物設定中的 `plugins:` 區段接線的類別。

> 注意：框架中也有 *prompt plugins*
> （`kohakuterrarium.prompt.plugins.BasePlugin`），可在建構時替 system prompt 貢獻區段。
> 它們是更底層的原語，不能直接透過設定接線。若你的需求是「替每次呼叫都加點東西」，那麼 `pre_llm_call` 生命週期外掛（如下）才是最合適的入口。

## 步驟 2 —— 撰寫脈絡注入外掛

`creatures/tutorial-creature/plugins/utc_injector.py`：

```python
"""Inject current UTC time into every LLM call."""

from datetime import datetime, timezone

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext


class UTCInjectorPlugin(BasePlugin):
    name = "utc_injector"
    priority = 90  # Late — run after other pre_llm_call plugins.

    async def on_load(self, context: PluginContext) -> None:
        # Nothing to do here; defined to show the lifecycle hook.
        return

    async def pre_llm_call(
        self, messages: list[dict], **kwargs
    ) -> list[dict] | None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        injection = {
            "role": "system",
            "content": f"[utc_injector] Current UTC time: {now}",
        }

        # Insert after the first system message so the agent's real
        # personality prompt stays first.
        modified = list(messages)
        insert_at = 1
        for i, msg in enumerate(modified):
            if msg.get("role") == "system":
                insert_at = i + 1
                break
        modified.insert(insert_at, injection)
        return modified
```

說明：

- `pre_llm_call` 會收到即將送出的完整 `messages` 清單。你可以回傳修改後的清單來取代原本內容，或回傳 `None` 表示不改動。
- `priority` 是整數。在 `pre_*` hook 中，數值較小者先執行；在 `post_*` hook 中，數值較小者較晚執行。`90` 會讓我們排在框架內建 hook 之後。
- `[utc_injector]` 前綴是一種慣例，這樣在記錄 messages 時，你能看出是哪個外掛貢獻了哪段內容。

## 步驟 3 —— 撰寫工具守衛外掛

`creatures/tutorial-creature/plugins/bash_guard.py`：

```python
"""Block `bash` calls that contain dangerous patterns."""

from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)

DANGEROUS_PATTERNS = ("rm -rf",)


class BashGuardPlugin(BasePlugin):
    name = "bash_guard"
    priority = 1  # First — block before anything else runs.

    async def on_load(self, context: PluginContext) -> None:
        return

    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        tool_name = kwargs.get("tool_name", "")
        if tool_name != "bash":
            return None  # Not our concern.

        command = args.get("command", "") or ""
        for pattern in DANGEROUS_PATTERNS:
            if pattern in command:
                raise PluginBlockError(
                    f"bash_guard: blocked — command contains "
                    f"'{pattern}'. Use a safer approach (explicit paths, "
                    f"trash instead of delete)."
                )
        return None  # Allow.
```

說明：

- `pre_tool_execute` 會收到 `args`，以及包含 `tool_name`、`job_id` 等內容的關鍵字參數。請先根據 `tool_name` 過濾，再檢查 args —— 因為這個 hook 會對**每個**工具都觸發。
- 丟出 `PluginBlockError(message)` 可以中止此次呼叫。這段 message 會成為 LLM 看見的工具結果，因此內容要足夠明確，讓模型能選擇不同做法。
- 回傳 `None` 代表不改動並允許呼叫。若回傳修改過的 dict，則可在執行前重寫 args（例如強制加上比較安全的旗標）。

## 步驟 4 —— 把兩者接進生物設定

`creatures/tutorial-creature/config.yaml`：

```yaml
name: tutorial_creature
version: "1.0"
base_config: "@kt-biome/creatures/general"

system_prompt_file: prompts/system.md

plugins:
  - name: utc_injector
    type: custom
    module: ./plugins/utc_injector.py
    class: UTCInjectorPlugin

  - name: bash_guard
    type: custom
    module: ./plugins/bash_guard.py
    class: BashGuardPlugin
```

這些欄位和上一份教學中的自訂工具接線方式相同：

- `type: custom` —— 從本機檔案載入。
- `module` —— 相對於代理資料夾的路徑。
- `class` —— 要實例化的外掛類別。（`class` 與 `class_name` 都可接受。）

若有選項，可透過 `options:`（一個 dict）傳入，並在 `__init__(self, options=...)` 中接收。上面的範例不需要任何選項，因此省略此區塊。

## 步驟 5 —— 執行並確認

```bash
kt run creatures/tutorial-creature --mode cli
```

### 確認注入器

問代理一個答案依賴目前時間的問題：

```text
> what time is it right now, in UTC, to the nearest minute?
```

即使它本身沒有時鐘，這個生物也應該回答出接近*現在*的時間。（如果你的 log level 是 `DEBUG`，你會直接看到被注入的 system message。）

### 確認守衛

請代理遞迴刪除某些東西：

```text
> run: rm -rf /tmp/tutorial-test-dir
```

controller 會派送工具呼叫，守衛接著丟出 `PluginBlockError`，而模型會把這段錯誤文字當作工具結果收到 —— 通常會回應「我不能執行這個」，並提出替代方案。不會有任何檔案被動到。

## 步驟 6 —— 了解其他 hook 介面

上面兩個 hook 只是最常見的一組。完整的生命週期外掛介面如下：

- 生命週期：`on_load`、`on_unload`、`on_agent_start`、`on_agent_stop`
- LLM：`pre_llm_call`、`post_llm_call`
- 工具：`pre_tool_execute`、`post_tool_execute`
- Sub-agents：`pre_subagent_run`、`post_subagent_run`
- Callbacks：`on_event`、`on_interrupt`、`on_task_promoted`、`on_compact_start`、`on_compact_end`

`pre_*` hook 可以轉換輸入，或透過 `PluginBlockError` 中止流程。`post_*` hook 可以轉換結果。Callbacks 則是 fire-and-forget 的觀察點。完整簽章與更多範例請參考[外掛指南](../guides/plugins.md)，而 repo 中的 `examples/plugins/` 則提供每種 hook 的完整實作範例。

## 你學到了什麼

- 外掛會在模組**之間**加入行為 —— 處理的是接縫，不是積木本體。最實用的兩個 hook 是 `pre_llm_call`（注入脈絡）與 `pre_tool_execute`（阻擋 / 重寫）。
- `PluginBlockError` 是外掛用來以模型可讀方式說「不行」的手段。
- `config.yaml` 中的 `plugins:` 接線方式和 `tools:` 接自訂工具幾乎相同 —— `type: custom`、`module:`、`class:`。
- `priority` 是整數；在 `pre_*` 中數值小者先執行，在 `post_*` 中則較晚執行。

## 接下來讀什麼

- [外掛概念](../concepts/modules/plugin.md) —— 為什麼需要外掛，以及它能解鎖什麼，包括「把 agent 放進外掛中」的模式。
- [外掛指南](../guides/plugins.md) —— 帶範例的完整 hook 參考。
- [組合模式](../concepts/patterns.md) —— 可把這些想法擴展成更大系統的「smart guard」與「seamless memory」模式。