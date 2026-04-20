---
title: 示例
summary: 快速浏览随附的示例 Creature、Terrarium 与代码，了解应该先看哪些内容，以及原因。
tags:
 - guides
 - examples
---

# 示例

适合想找可执行代码与设置来学习的读者。

`examples/` 目录依类型整理可执行内容：独立代理设置、Terrarium设置、插件实作，以及将框架嵌入其中的 Python 脚本。每个目录都示范了一种你可以直接复制或继承的模式。

概念阅读指引：[boundaries 概念](../concepts/boundaries.md) —— 示例刻意涵盖系统边界情况。

## `examples/agent-apps/` —— 独立Creature

单一Creature 设置。执行方式：

```bash
kt run examples/agent-apps/<name>
```

| Agent | 模式 | 示范内容 |
|---|---|---|
| `swe_agent` | 程式开发代理 | 偏重工具使用的Creature，接近 `kt-biome/creatures/swe` |
| `discord_bot` | 群组聊天机器人 | 自定义 Discord I/O、短暂型、原生工具调用 |
| `planner_agent` | 规划－执行－反思 | 草稿区状态机 + 评审子代理 |
| `monitor_agent` | 触发器驱动 | `input: none` + 计时器触发器，没有用户介入 |
| `conversational` | 串流 ASR/TTS | Whisper 输入、TTS 输出、交互式子代理 |
| `rp_agent` | 角色扮演 | 以记忆为优先的设计、启动触发器、角色提示词 |
| `compact_test` | 压缩压力测试 | 小型上下文 + 自动压缩，用来验证压缩流程 |

相关指南：[Creatures 指南](creatures.md)、[配置指南](configuration.md)。

## `examples/terrariums/` —— 多代理Terrarium设置

```bash
kt terrarium run examples/terrariums/<name>
```

| Terrarium | 拓扑 | Creature |
|---|---|---|
| `novel_terrarium` | 带回馈的管线 | brainstorm → planner → writer |
| `code_review_team` | 带关卡的回圈 | developer、reviewer、tester |
| `research_assistant` | 星状加协调者 | coordinator + searcher + analyst |

相关指南：[Terrarium 指南](terrariums.md)。

## `examples/plugins/` —— 插件 hooks

每个 hook 类别各有一个示例。编写自己的插件时，可把它们当成参考。

| Plugin | Hooks | 等级 |
|---|---|---|
| `hello_plugin` | `on_load`、`on_agent_start/stop` | 初学 |
| `tool_timer` | `pre/post_tool_execute`、state | 初学 |
| `tool_guard` | `pre_tool_execute`、`PluginBlockError` | 进阶入门 |
| `prompt_injector` | `pre_llm_call`（消息变更） | 进阶入门 |
| `response_logger` | `post_llm_call`、`on_event`、`on_interrupt` | 进阶入门 |
| `budget_enforcer` | `pre/post_llm_call` 搭配阻挡与 state | 进阶 |
| `subagent_tracker` | `pre/post_subagent_run`、`on_task_promoted` | 进阶 |
| `webhook_notifier` | Fire-and-forget 回呼、`inject_event`、`switch_model` | 进阶 |

相关指南：[插件指南](plugins.md)。完整逐字段说明请见 `examples/plugins/README.md`。

## `examples/code/` —— Python 嵌入

这些脚本示范如何把框架嵌入你的程式中，并由你的代码担任协调者。每个示例都使用 compose algebra 的不同片段，或 `Agent` / `TerrariumRuntime` / `KohakuManager` API。

| Script | 模式 | 使用的功能 |
|---|---|---|
| `programmatic_chat.py` | 将 Agent 当作函式库使用 | `AgentSession.chat()` |
| `run_terrarium.py` | 以代码建立 Terrarium | `TerrariumRuntime`、频道注入 |
| `discord_adventure_bot.py` | 由 Bot 拥有互动流程 | `agent()`、动态建立、游戏状态 |
| `debate_arena.py` | 多代理轮流互动 | `agent()`、`>>`、`async for`、持久代理 |
| `task_orchestrator.py` | 动态代理拓扑 | `factory()`、`>>`、`asyncio.gather` |
| `ensemble_voting.py` | 以多样性实现冗余 | `&`、`>>` 自动包装、`\|`、`*` |
| `review_loop.py` | 编写 → 审查 → 修订 | `.iterate()`、持久 `agent()` |
| `smart_router.py` | 分类并派送 | `>> {dict}` 路由、`factory()`、`\|` 后备 |
| `pipeline_transforms.py` | 数据提取管线 | `>>` 自动包装（`json.loads`、lambda）、代理 + 函式 |

相关指南：[程序化使用指南](programmatic-usage.md)、[组合代数指南](composition.md)。

## 新读者建议阅读顺序

1. **先运行一个**。 `kt run examples/agent-apps/swe_agent` —— 先感受 Creature 如何工作。
2. **再从它继承**。 复制目录、调整 `config.yaml`，然后重新执行。
3. **加入插件**。 把 `examples/plugins/tool_timer.py` 加到你的Creature `plugins:` 清单中。
4. **进入 Python**。 打开 `examples/code/programmatic_chat.py` 并执行它。
5. **试试组合**。 用 `examples/code/review_loop.py` 看 compose algebra 如何工作。
6. **改为运行多代理**。 执行 `examples/terrariums/code_review_team`，观察频道流量。

## 另请参阅

- [快速开始指南](getting-started.md) —— 环境设置。
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome) —— 演示包；许多示例与它共用相同模式。
- [开发指南教程](../tutorials/README.md) —— 与这些示例搭配的引导式教学。