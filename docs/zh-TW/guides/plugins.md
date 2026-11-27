---
title: 外掛
summary: 外掛既掛在執行期事件上，也能貢獻 prompt 文字：各自掛在哪裡、怎麼組合，以及什麼時候該用。
tags:
  - guides
  - plugin
  - extending
---

# 外掛

給想在模組之間的*接縫*加上行為、又不想 fork 任何模組的讀者。

外掛修改的是 controller、工具、子代理與 LLM 之間的連接方式，而不是模組本身。外掛是一個類別 `BasePlugin`：它掛在執行時事件上（pre/post LLM、pre/post tool 等），也可以往 system prompt 貢獻內容。

概念先讀：[plugin](../concepts/modules/plugin.md)、[patterns](../concepts/patterns.md)。

## 什麼時候該寫 plugin、tool 或 module

- *tool* 是 LLM 可以用名字呼叫的東西。
- *module*（input/output/trigger/sub-agent）是一整個執行時介面。
- *plugin* 是在它們*之間*執行的規則，像 guard、accounting、prompt injection、memory retrieval。

如果你的需求是「每次在 X 前後，都做 Y」，答案幾乎總是 plugin。

## Prompt 貢獻

契約：

- 繼承 `BasePlugin`。
- 設定 `name`、`priority`（數字越小，在外掛貢獻中越靠前）。
- 實作 `get_prompt_content(context) -> str | None`。

```python
# plugins/project_header.py
from kohakuterrarium.modules.plugin.base import BasePlugin


class ProjectHeaderPlugin(BasePlugin):
    name = "project_header"
    priority = 35

    def __init__(self, text: str = ""):
        super().__init__()
        self.text = text

    def get_prompt_content(self, context) -> str | None:
        if not self.text:
            return None
        return f"## Project Header\n\n{self.text}"
```

prompt 聚合器會依 priority 收集這些貢獻，放進 system prompt 的「外掛貢獻」位置，同時適用於父代理與子代理。框架自己的區段（工具清單、工具指引、skills、group 區塊、執行模型）是框架區塊而不是外掛，請見 [prompt 聚合](../concepts/impl-notes/prompt-aggregation.md)。

執行期外掛也可以實作 `get_tool_visibility(context) -> ToolVisibility | None`，按請求裁切暴露給模型的原生工具目錄（`allowed_tools` / `allowed_subagents`，`None` 表示不限制）。

除了外掛以外，工具本身也可以透過 `prompt_contribution()` 貢獻短的 prompt 指引，彙整進 `## Tool guidance` 區段。

Priority 越低越早執行。你可以藉此把外掛插到正確位置。

## Lifecycle hook

繼承 `BasePlugin`，並實作以下任意 hook。全部都是 async。

| Hook | Signature | 效果 |
|---|---|---|
| `on_load(context)` | agent 啟動時初始化 |（無）|
| `on_unload()` | 停止時清理 |（無）|
| `on_disable()` | 同步；執行時停用生效後呼叫 | 只是通知，不是拆除屏障：釋放外掛持有的資源；管理器不會等待清理，需自行安排它與之後重新啟用時 `on_load` 的順序，並在 `on_unload` 中等待 |
| `pre_llm_call(messages, **kwargs)` | 回傳 `list[dict] \| None` | 取代送往 LLM 的訊息 |
| `post_llm_call(response)` | 回傳 `ChatResponse \| None` | 取代回應 |
| `pre_tool_execute(name, args)` | 回傳 `dict \| None`；或 raise `PluginBlockError` | 取代參數或阻擋呼叫 |
| `post_tool_execute(name, result)` | 回傳 `ToolResult \| None` | 取代工具結果 |
| `pre_subagent_run(name, context)` | 回傳 `dict \| None` | 取代子代理上下文 |
| `post_subagent_run(name, output)` | 回傳 `str \| None` | 取代子代理輸出 |

Fire-and-forget 回呼（沒有回傳值、也無法修改內容）：

- `on_tool_start`, `on_tool_end`
- `on_llm_start`, `on_llm_end`
- `on_processing_start`, `on_processing_end`
- `on_startup`, `on_shutdown`
- `on_compact_start`, `on_compact_complete`
- `on_event`

### 固定上下文（pinned context）

`context.pin_message(key, role, content)` 會把一則訊息放在每次請求的 system prompt 之後；`role` 為 `"user"` 或 `"system"`，`content` 可以是字串或內容片段清單（可含圖片）。同一個 key 再次 pin 會原地取代，`context.unpin_message(key)` 移除它。

固定訊息不在對話歷史裡：壓縮（compaction）不會摘要或丟棄它們，壓縮後的順序是 system prompt、固定訊息、摘要、近期回合；edit、regenerate、rewind、切換分支與 `/clear` 都會保留它們，這些操作使用的訊息索引也不計入它們；它們存在 session state 中，resume 與 fork 後各恢復一次；fork 出來的子代理與旁支對話會繼承它們。詳見英文文件 [plugins.md](../../en/guides/plugins.md#pinned-context)。

## 範例：tool guard

阻擋危險的 shell 命令。

```python
# plugins/tool_guard.py
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError


class ToolGuard(BasePlugin):
    name = "tool_guard"

    def __init__(self, deny_patterns: list[str]):
        super().__init__()
        self.deny_patterns = deny_patterns

    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        if kwargs.get("tool_name") != "bash":
            return None
        command = args.get("command", "")
        for pat in self.deny_patterns:
            if pat in command:
                raise PluginBlockError(f"Blocked by tool_guard: {pat!r}")
        return None
```

設定：

```yaml
plugins:
  - name: tool_guard
    type: custom
    module: ./plugins/tool_guard.py
    class: ToolGuard
    options:
      deny_patterns: ["rm -rf /", "dd if=/dev/zero"]
```

丟出 `PluginBlockError` 會中止該操作，錯誤訊息會成為工具結果。

## 範例：token accounting

```python
class TokenAccountant(BasePlugin):
    name = "token_accountant"

    async def post_llm_call(self, response):
        usage = response.usage or {}
        my_db.record(tokens_in=usage.get("prompt_tokens"),
                     tokens_out=usage.get("completion_tokens"))
        return None   # 不取代回應
```

## 範例：seamless memory（在外掛裡用 agent）

做一個 `pre_llm_call` 外掛，先取回相關的歷史事件，再把它們 prepend 到 messages 前面。你甚至可以呼叫一個小型巢狀 agent 來判斷哪些內容相關：plugin 就是普通 Python，所以裡面用 agent 完全合法。可參考 [concepts/python-native/agent-as-python-object](../concepts/python-native/agent-as-python-object.md)。

## 內建執行期外掛

子代理預算與自動壓縮都是普通 lifecycle 外掛：

| 名稱 | 類型 | 用途 |
|---|---|---|
| `budget` | 外掛 | 統一的 turn/tool/walltime 預算計數與執行；預算軸寫在 `options` 下。 |
| `compact.auto` | 外掛 | 在 LLM 輪次後檢查用量並觸發已設定的壓縮管理器。 |
| `auto-compact` | 外掛包 | 展開為 `compact.auto`；這是唯一的內建執行期外掛包。 |

在父生物上使用預算與自動壓縮：

```yaml
default_plugins: ["auto-compact"]
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      # walltime_budget: [300, 600]
```

或在每個子代理上使用：

```yaml
subagents:
  - name: reviewer
    type: custom
    system_prompt: "Review the change."
    tools: [read, grep]
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [40, 60]
          tool_call_budget: [75, 100]
```

內建子代理已經使用 `auto-compact` 和帶有這些最小選項的 `budget` 外掛。完整說明見 [子代理指南](sub-agents.md)。

## 在執行時管理外掛

Slash 指令：

```
/plugin list
/plugin enable tool_guard
/plugin disable tool_guard
/plugin toggle tool_guard
```

外掛只會在 agent 啟動時載入一次；enable/disable 只是執行時旗標，不是重新載入。若你修改了設定，仍然需要重啟。

## 工具來源與關閉工具

每個已註冊的工具都帶有來源標籤，說明是誰把它放進來的：

| 標籤 | 來源 |
|------|------|
| `builtin` | 框架工具：設定中宣告的內建工具、trigger 工具、provider 原生工具，或框架自行註冊的工具（`skill`、`send_channel`、`group_*`）。 |
| `package:<pkg>` | 由已安裝套件 `<pkg>` 提供的 `package` 或 `custom` 工具（`@<pkg>/...` 模組，或位於該套件資料夾內的模組檔案）。沒有任何已安裝套件認領的 `package` 模組，以其頂層 Python 模組名稱作為標籤。 |
| `custom:<creature>` | 來自所有已安裝套件之外的模組檔案的 `custom` 工具（例如 creature 自己的 `custom/` 資料夾）。 |
| `plugin:<name>` | 外掛 `<name>` 透過 `contribute_tools()` 提供的工具。 |
| `mcp:<server>` | 以 `ToolOrigin("mcp", "<server>")` 註冊的工具，用於轉送到 MCP 伺服器的工具。 |
| `runtime` | 從 Python 加入的工具：`Agent.build(tools=...)`，或未指定來源的 `agent.add_tool(tool)`。 |

`/module` 列出每個工具的來源與狀態（`●` 開啟，`○` 關閉）。Provider 原生工具列在
*Native tools* 下，一般工具列在 *Tools* 下。任何工具都可以針對單一 agent 關閉，
包括外掛沒有取代掉的工具：

```
/module disable bash
/module enable bash
/module toggle tool/bash
```

被關閉的工具：

- 從模型看到的目錄中移除：原生工具 schema 以及每個列出工具的提示詞段落。系統提示詞會立即重建。
- 模型仍然呼叫時會被拒絕。呼叫會回傳下列工具錯誤，工具本身不會執行：
  `Tool 'bash' is turned off for this agent. Use another tool, or turn it on with /module enable bash.`
- 以名稱保持關閉。被移除後再次註冊的工具（外掛停用後再啟用、以相同名稱呼叫 `add_tool`）仍然是關閉的。
- 會隨工作階段儲存，恢復（resume）時在 agent 執行任何東西之前再次關閉。

這個開關只作用於該 agent。Sub-agent 依自己的設定建立工具集，保留它列出的每個工具。
框架工具（`info`、`skill`、`send_channel`、`group_send`）也可以關閉；`/module`
會顯示警告，說明哪些功能會停止運作。`info` 關閉期間，原本需要先讀取文件的工具不會被阻擋。

在 Python 中使用 `agent.set_tool_enabled(name, enabled)`、`agent.is_tool_enabled(name)`
與 `agent.disabled_tools`。`agent.registry.get_origin(name)` 回傳工具的 `ToolOrigin`。
對 `agent.add_tool` 傳入 `origin=ToolOrigin("mcp", "<server>")`（來自
`kohakuterrarium.modules.tool`），即可為你為 MCP 伺服器註冊的工具加上標籤。

## 發佈外掛

打包進 package：

```yaml
# my-pack/kohaku.yaml
name: my-pack
plugins:
  - name: tool_guard
    module: my_pack.plugins.tool_guard
    class: ToolGuard
```

使用者在自己的生物中啟用它：

```yaml
plugins:
  - name: tool_guard
    type: package
    options: { deny_patterns: [...] }
```

詳見 [套件](packages.md)。

## Hook 的執行順序

當多個外掛實作同一個 hook 時：

- `pre_*` hook 依註冊順序執行；第一個回傳非 `None` 值的外掛勝出。
- `post_*` hook 依註冊順序執行；每個外掛都會收到上一個外掛處理後的輸出。
- Fire-and-forget hook 全都會執行（錯誤只記錄，不往外拋）。

任何 `pre_*` hook 只要丟出 `PluginBlockError`，就會直接短路後續外掛與該操作。

## 測試外掛

```python
from kohakuterrarium.testing.agent import TestAgentBuilder

env = (
    TestAgentBuilder()
    .with_llm_script(["[/bash]@@command=rm -rf /\n[bash/]", "Stopped."])
    .with_builtin_tools(["bash"])
    .with_plugin(ToolGuard(deny_patterns=["rm -rf /"]))
    .build()
)
await env.inject("cleanup")
assert any("Blocked" in act[1] for act in env.output.activities)
```

## 疑難排解

- **找不到外掛 class。** 檢查 `class` 欄位（不是 `class_name`；plugin 用的是 `class`）。設定載入器兩者都接受，但 package manifest 用的是 `class`。
- **Hook 從來沒觸發。** 確認 hook 名稱拼對；像 `pre_llm_call` 與 `pre_tool_execute` 若拼錯，會靜默失效。
- **`PluginBlockError` 丟出了，但呼叫還是執行了。** 你是在 `post_*` hook 裡丟出的。要阻擋，請用 `pre_tool_execute`。
- **對順序敏感的外掛堆疊行為不對。** `pre_*` hook 依註冊順序執行；請調整設定中 `plugins:` 清單的順序。

## 延伸閱讀

- [examples/plugins/](../../examples/plugins/)：每種 hook 類型各有一個範例。
- [自訂模組](custom-modules.md)：撰寫外掛所包圍的那些模組。
- [參考 / plugin hooks](../reference/plugin-hooks.md)：所有 hook 的完整 signature。
- [概念 / plugin](../concepts/modules/plugin.md)：設計理由。
