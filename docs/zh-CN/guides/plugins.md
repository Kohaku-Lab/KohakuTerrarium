---
title: 插件
summary: 插件既挂在运行时事件上，也能贡献 prompt 文本：各自挂在哪里、怎么组合，以及什么时候该用。
tags:
 - guides
 - plugin
 - extending
---

# 插件

给想在模块之间的*接缝*加上行为、又不想 fork 任何模块的读者。

插件修改的是 controller、工具、子代理与 LLM 之间的连接方式，而不是模块本身。插件是一个类 `BasePlugin`：它挂在执行时事件上（pre/post LLM、pre/post tool 等），也可以往 system prompt 贡献内容。

概念先读：[plugin 概念](../concepts/modules/plugin.md)、[patterns 概念](../concepts/patterns.md)。

## 什么时候该写 plugin、tool 或 module

- *tool* 是 LLM 可以用名字调用的东西。
- *module*（input/output/trigger/sub-agent）是一整个执行时介面。
- *plugin* 是在它们*之间*执行的规则，像 guard、accounting、prompt injection、memory retrieval。

如果你的需求是「每次在 X 前后，都做 Y」，答案几乎总是 plugin。

## Prompt 贡献

契约：

- 继承 `BasePlugin`。
- 设置 `name`、`priority`（数字越小，在插件贡献中越靠前）。
- 实作 `get_prompt_content(context) -> str | None`。

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

prompt 聚合器会按 priority 收集这些贡献，放进 system prompt 的“插件贡献”位置，同时适用于父代理与子代理。框架自己的区段（工具清单、工具指引、skills、group 区块、执行模型）是框架区块而不是插件，参见 [prompt 聚合](../concepts/impl-notes/prompt-aggregation.md)。

运行时插件还可以实现 `get_tool_visibility(context) -> ToolVisibility | None`，按请求裁剪暴露给模型的原生工具目录（`allowed_tools` / `allowed_subagents`，`None` 表示不限制）。

除了插件以外，工具本身也可以通过 `prompt_contribution()` 贡献短的 prompt 指引，汇总进 `## Tool guidance` 区段。

Priority 越低越早执行。你可以借此把插件插到正确位置。

## Lifecycle hook

继承 `BasePlugin`，并实作以下任意 hook。全部都是 async。

| Hook | Signature | 效果 |
|---|---|---|
| `on_load(context)` | agent 启动时初始化 | （无） |
| `on_unload()` | 停止时清理 | （无） |
| `on_disable()` | 同步；运行时停用生效后调用 | 只是通知，不是拆除屏障：释放插件持有的资源；管理器不会等待清理，需自行安排它与之后重新启用时 `on_load` 的顺序，并在 `on_unload` 中等待 |
| `pre_llm_call(messages, ** kwargs)` | 回传 `list[dict] \| None` | 取代送往 LLM 的消息 |
| `post_llm_call(response)` | 回传 `ChatResponse \| None` | 取代回应 |
| `pre_tool_execute(name, args)` | 回传 `dict \| None`；或 raise `PluginBlockError` | 取代参数或阻挡调用 |
| `post_tool_execute(name, result)` | 回传 `ToolResult \| None` | 取代工具结果 |
| `pre_subagent_run(name, context)` | 回传 `dict \| None` | 取代子代理上下文 |
| `post_subagent_run(name, output)` | 回传 `str \| None` | 取代子代理输出 |

Fire-and-forget 回呼（没有回传值、也无法修改内容）：

- `on_tool_start`, `on_tool_end`
- `on_llm_start`, `on_llm_end`
- `on_processing_start`, `on_processing_end`
- `on_startup`, `on_shutdown`
- `on_compact_start`, `on_compact_complete`
- `on_event`

### 固定上下文（pinned context）

`context.pin_message(key, role, content)` 会把一条消息放在每次请求的 system prompt 之后；`role` 为 `"user"` 或 `"system"`，`content` 可以是字符串或内容片段列表（可含图片）。同一个 key 再次 pin 会原地替换，`context.unpin_message(key)` 移除它。

固定消息不在对话历史里：压缩（compaction）不会总结或丢弃它们，压缩后的顺序是 system prompt、固定消息、摘要、近期回合；edit、regenerate、rewind、切换分支与 `/clear` 都会保留它们，这些操作使用的消息索引也不计入它们；它们存在 session state 中，resume 与 fork 后各恢复一次；fork 出来的子代理与旁支对话会继承它们。详见英文文档 [plugins.md](../../en/guides/plugins.md#pinned-context)。

## 示例：tool guard

阻挡危险的 shell 命令。

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

设置：

```yaml
plugins:
  - name: tool_guard
    type: custom
    module: ./plugins/tool_guard.py
    class: ToolGuard
    options:
      deny_patterns: ["rm -rf /", "dd if=/dev/zero"]
```

丢出 `PluginBlockError` 会中止该操作，错误消息会成为工具结果。

## 示例：token accounting

```python
class TokenAccountant(BasePlugin):
    name = "token_accountant"

    async def post_llm_call(self, response):
        usage = response.usage or {}
        my_db.record(tokens_in=usage.get("prompt_tokens"),
                     tokens_out=usage.get("completion_tokens"))
        return None   # 不取代回应
```

## 示例：seamless memory（在插件里用 agent）

做一个 `pre_llm_call` 插件，先取回相关的历史事件，再把它们 prepend 到 messages 前面。你甚至可以调用一个小型巢状 agent 来判断哪些内容相关；plugin 就是普通 Python，所以里面用 agent 完全合法。可参考 [concepts/python-native/agent-as-python-object 概念](../concepts/python-native/agent-as-python-object.md)。

## 内置运行时插件

子代理预算与自动压缩都是普通 lifecycle 插件：

| 名称 | 类型 | 用途 |
|---|---|---|
| `budget` | 插件 | 统一的 turn/tool/walltime 预算计数与执行；预算轴写在 `options` 下。 |
| `compact.auto` | 插件 | 在 LLM 轮次后检查用量并触发已配置的压缩管理器。 |
| `auto-compact` | 插件包 | 展开为 `compact.auto`；这是唯一的内置运行时插件包。 |

在父 Creature 上使用预算与自动压缩：

```yaml
default_plugins: ["auto-compact"]
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      # walltime_budget: [300, 600]
```

或在每个子代理上使用：

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

内置子代理已经使用 `auto-compact` 和带有这些最小选项的 `budget` 插件。完整说明见 [子代理指南](sub-agents.md)。

## 在执行时管理插件

Slash 指令：

```
/plugin list
/plugin enable tool_guard
/plugin disable tool_guard
/plugin toggle tool_guard
```

插件只会在 agent 启动时加载一次；enable/disable 只是执行时旗标，不是重新加载。若你修改了设置，仍然需要重启。

## 工具来源与关闭工具

每个已注册的工具都带有来源标签，说明是谁把它放进来的：

| 标签 | 来源 |
|------|------|
| `builtin` | 框架工具：配置中声明的内置工具、trigger 工具、provider 原生工具，或框架自行注册的工具（`skill`、`send_channel`、`group_*`）。 |
| `package:<pkg>` | 由已安装套件 `<pkg>` 提供的 `package` 或 `custom` 工具（`@<pkg>/...` 模块，或位于该套件文件夹内的模块文件）。没有任何已安装套件认领的 `package` 模块，以其顶层 Python 模块名称作为标签。 |
| `custom:<creature>` | 来自所有已安装套件之外的模块文件的 `custom` 工具（例如 creature 自己的 `custom/` 文件夹）。 |
| `plugin:<name>` | 插件 `<name>` 通过 `contribute_tools()` 提供的工具。 |
| `mcp:<server>` | 以 `ToolOrigin("mcp", "<server>")` 注册的工具，用于转发到 MCP 服务器的工具。 |
| `runtime` | 从 Python 加入的工具：`Agent.build(tools=...)`，或未指定来源的 `agent.add_tool(tool)`。 |

`/module` 列出每个工具的来源与状态（`●` 开启，`○` 关闭）。Provider 原生工具列在
*Native tools* 下，一般工具列在 *Tools* 下。任何工具都可以针对单一 agent 关闭，
包括插件没有替换掉的工具：

```
/module disable bash
/module enable bash
/module toggle tool/bash
```

被关闭的工具：

- 从模型看到的目录中移除：原生工具 schema 以及每个列出工具的提示词段落。系统提示词会立即重建。
- 模型仍然调用时会被拒绝。调用会返回下列工具错误，工具本身不会执行：
  `Tool 'bash' is turned off for this agent. Use another tool, or turn it on with /module enable bash.`
- 以名称保持关闭。被移除后再次注册的工具（插件停用后再启用、以相同名称调用 `add_tool`）仍然是关闭的。
- 会随会话保存，恢复（resume）时在 agent 执行任何东西之前再次关闭。

这个开关只作用于该 agent。Sub-agent 依自己的配置建立工具集，保留它列出的每个工具。
框架工具（`info`、`skill`、`send_channel`、`group_send`）也可以关闭；`/module`
会显示警告，说明哪些功能会停止运作。`info` 关闭期间，原本需要先读取文档的工具不会被阻挡。

在 Python 中使用 `agent.set_tool_enabled(name, enabled)`、`agent.is_tool_enabled(name)`
与 `agent.disabled_tools`。`agent.registry.get_origin(name)` 返回工具的 `ToolOrigin`。
对 `agent.add_tool` 传入 `origin=ToolOrigin("mcp", "<server>")`（来自
`kohakuterrarium.modules.tool`），即可为你为 MCP 服务器注册的工具加上标签。

## 发布插件

打包进 package：

```yaml
# my-pack/kohaku.yaml
name: my-pack
plugins:
  - name: tool_guard
    module: my_pack.plugins.tool_guard
    class: ToolGuard
```

用户在自己的Creature中启用它：

```yaml
plugins:
  - name: tool_guard
    type: package
    options: { deny_patterns: [...] }
```

详见 [包指南](packages.md)。

## Hook 的执行顺序

当多个插件实作同一个 hook 时：

- `pre_*` hook 依注册顺序执行；第一个回传非 `None` 值的插件胜出。
- `post_*` hook 依注册顺序执行；每个插件都会收到上一个插件处理后的输出。
- Fire-and-forget hook 全都会执行（错误只记录，不往外抛）。

任何 `pre_*` hook 只要丢出 `PluginBlockError`，就会直接短路后续插件与该操作。

## 测试插件

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

## 疑难排解

- **找不到插件 class**。 检查 `class` 字段（不是 `class_name`，plugin 用的是 `class`）。设置加载器两者都接受，但 package manifest 用的是 `class`。
- **Hook 从来没触发**。 确认 hook 名称拼对；像 `pre_llm_call` 与 `pre_tool_execute` 若拼错，会静默失效。
- **`PluginBlockError` 抛出了，但调用还是执行了**。 你是在 `post_*` hook 里丢出的。要阻挡，请用 `pre_tool_execute`。
- **对顺序敏感的插件堆叠行为不正确**。 `pre_*` hook 依注册顺序执行；请调整设置中 `plugins:` 清单的顺序。

## 延伸阅读

- [examples/plugins/](../../examples/plugins/)：每种 hook 类型各有一个示例。
- [自定义模块指南](custom-modules.md)：编写插件所包围的那些模块。
- [参考 / plugin hooks](../reference/plugin-hooks.md)：所有 hook 的完整 signature。
- [概念 / plugin](../concepts/modules/plugin.md)：设计理由。
