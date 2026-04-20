---
title: 第一个插件
summary: 构建生命周期插件，挂接 pre/post tool execution 以拦截、阻止或增强调用。
tags:
  - tutorials
  - plugin
  - extending
---

# 第一个插件

**问题** ： 你需要一种不属于任何单一模块的行为，例如为每次 LLM 调用注入上下文，或者在全局范围拦截某类工具调用模式。这时，新工具不是合适的形态，新 output 模块也不是；插件才是。

**完成状态** ： 你会通过 `config.yaml` 在一个 Creature 中接入两个可运行的插件：

1. 一个 **上下文注入器**，把当前 UTC 时间作为简短的 system message 加入每次 LLM 调用。
2. 一个 **工具守卫**，拦截任何包含 `rm -rf` 的 `bash` 调用，并返回一条模型可读的说明性错误。

**前置条件** ： [第一个 Creature](first-creature.md)，最好也看过 [第一个自定义工具](first-custom-tool.md)。你应该已经熟悉如何编辑 Creature 的 `config.yaml`，以及如何把 Python 文件放在它旁边。

插件修改的是 **模块之间的连接**，不是模块本身。关于这条边界为什么存在，请参阅 [插件概念](../concepts/modules/plugin.md)。

## 第 1 步：选择目录

沿用你已有的 Creature，或者新建一个：

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

下面这两个插件都是生命周期插件，它们会继承 `kohakuterrarium.modules.plugin.base` 中的 `BasePlugin`。也只有这种类，才能通过 Creature 配置中的 `plugins:` 区段接入。

> 注意：框架中也有 *prompt plugins*（`kohakuterrarium.prompt.plugins.BasePlugin`），可以在构建时为 system prompt 贡献片段。
> 它们属于更底层的原语，不能直接通过配置接线。如果你的需求是“为每次调用都补充一点内容”，那么像下面这样使用 `pre_llm_call` 生命周期插件，通常是更合适的入口。

## 第 2 步：编写上下文注入插件

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

说明：

- `pre_llm_call` 会收到即将发送的完整 `messages` 列表。你可以返回修改后的列表替换原始内容，也可以返回 `None` 表示不做修改。
- `priority` 是整数。在 `pre_*` hook 中，值越小越早执行；在 `post_*` hook 中，值越小越晚执行。这里设置为 `90`，表示让它排在框架内置 hook 之后。
- `[utc_injector]` 前缀是一种约定，便于在记录 messages 时看出是哪一个插件贡献了这段内容。

## 第 3 步：编写工具守卫插件

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

说明：

- `pre_tool_execute` 会收到 `args`，以及包含 `tool_name`、`job_id` 等内容的关键字参数。请先根据 `tool_name` 过滤，再检查 `args`，因为这个 hook 会对 **每个** 工具触发。
- 抛出 `PluginBlockError(message)` 可以中止本次调用。这段 message 会成为 LLM 能看到的工具结果，因此内容必须足够明确，让模型知道该换用其他方式。
- 返回 `None` 表示不做修改并允许调用。如果返回修改后的 dict，则可以在执行前重写 `args`，例如强制增加更安全的标志。

## 第 4 步：把两者接入 Creature 配置

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

这些字段和上一篇教程中的自定义工具接线方式相同：

- `type: custom` —— 从本地文件加载。
- `module` —— 相对于 Agent 目录的路径。
- `class` —— 要实例化的插件类。（`class` 与 `class_name` 都支持。）

如果插件有选项，可以通过 `options:`（一个 dict）传入，并在 `__init__(self, options=...)` 中接收。上面的示例不需要任何选项，因此省略了这一块。

## 第 5 步：运行并确认

```bash
kt run creatures/tutorial-creature --mode cli
```

### 确认注入器

向 Agent 提一个依赖当前时间的问题：

```text
> what time is it right now, in UTC, to the nearest minute?
```

即使它本身没有时钟，这个 Creature 也应该能回答接近 **当前时间** 的结果。（如果你的日志级别是 `DEBUG`，还能直接看到被注入的 system message。）

### 确认守卫

让 Agent 递归删除一些内容：

```text
> run: rm -rf /tmp/tutorial-test-dir
```

controller 会分发这次工具调用，随后守卫会抛出 `PluginBlockError`。模型会把这段错误文本当作工具结果接收，通常会回答“我不能执行这个”，并给出替代方案。不会有任何文件被修改。

## 第 6 步：了解其他 hook 接口

上面两个 hook 只是最常见的一组。完整的生命周期插件接口如下：

- 生命周期：`on_load`、`on_unload`、`on_agent_start`、`on_agent_stop`
- LLM：`pre_llm_call`、`post_llm_call`
- 工具：`pre_tool_execute`、`post_tool_execute`
- Sub-agent：`pre_subagent_run`、`post_subagent_run`
- 回调：`on_event`、`on_interrupt`、`on_task_promoted`、`on_compact_start`、`on_compact_end`

`pre_*` hook 可以转换输入，也可以通过 `PluginBlockError` 中止流程。`post_*` hook 可以转换结果。回调则是 fire-and-forget 的观察点。完整签名和更多示例请参阅 [插件指南](../guides/plugins.md)，仓库中的 `examples/plugins/` 也提供了每类 hook 的完整实现示例。

## 你学到了什么

- 插件会在模块 **之间** 增加行为，处理的是接缝，而不是模块本体。最实用的两个 hook 是 `pre_llm_call`（注入上下文）和 `pre_tool_execute`（阻止 / 重写）。
- `PluginBlockError` 是插件以模型可读方式表达“不能这样做”的标准手段。
- `config.yaml` 中 `plugins:` 的接线方式与 `tools:` 接入自定义工具非常相似：`type: custom`、`module:`、`class:`。
- `priority` 是整数；在 `pre_*` 中值越小越早执行，在 `post_*` 中则越晚执行。

## 接下来读什么

- [插件概念](../concepts/modules/plugin.md) —— 为什么需要插件，以及它能解锁什么，包括“把 Agent 放进插件中”的模式。
- [插件指南](../guides/plugins.md) —— 带示例的完整 hook 参考。
- [组合模式](../concepts/patterns.md) —— 可以把这些想法扩展成更大系统中的 “smart guard” 和 “seamless memory” 模式。
