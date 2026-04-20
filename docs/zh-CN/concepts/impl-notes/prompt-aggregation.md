---
title: 提示词聚合
summary: 说明 system prompt 如何由人格、工具清单、框架提示与按需加载的技能组合而成。
tags:
  - concepts
  - impl-notes
  - prompt
---

# 提示词聚合

## 这要解决的问题

代理的「system prompt」不是单一字串，而是由下列内容组合而成：

- Creature的人格／角色，
- 可用工具清单（名称 + 描述），
- 在这个Creature所选格式中，实际上该如何调用工具，
- 任何频道拓扑资讯（在Terrarium中），
- 对具名输出的说明（让 LLM 知道何时该送到
  Discord、何时该送到 stdout），
- 由插件贡献的区段（专案规则、环境资讯等），
- 每个工具的完整文件（若使用 `static` skill
  模式）——或者完全不包含这些内容（若使用 `dynamic` 模式）。

如果把这件事交给手写 prompt，你就会把 bug 一起交付出去：
工具清单过时、调用语法错误、区段重复。这个框架会以确定性的方式
组装整份内容。

## 曾考虑的方案

- **手写 prompts**。 很脆弱。每次新增工具都可能坏掉。
- **永远使用完整静态 prompts**。 很完整，但也非常大——光是工具文件
  就可能有数万 tokens。
- **按需加载文件**。 只提供名称；需要时再让代理透过 `info`
  framework command 拉取完整文件。
- **可配置**。 每个 Creature自行选择取舍：`skill_mode:
  dynamic` 或 `skill_mode: static`。这就是实际采用的方案。

## 我们实际怎么做

`prompt/aggregator.py:aggregate_system_prompt(...)` 会依照以下顺序
串接各个区段：

1.**基础 prompt**。 使用 Jinja2 渲染（safe-undefined fallback）；
  内容包含Creature的人格，以及宣告在 `prompt_context_files`
  底下的任何专案上下文文件。
2.**工具区段**。
  - `skill_mode: dynamic` → 工具*索引*：每个工具提供名称 +
  一行描述。代理会在需要时透过 `info` framework command
  加载完整文件。
  - `skill_mode: static` → 直接内嵌每个工具的完整文件。
3. **频道拓扑区段**（仅限Terrarium中的 Creature）。描述
  「你会监听 X、Y；你可以传送到 Z；另一端是谁。」
  由 `terrarium/config.py:build_channel_topology_prompt`
  产生。
4.**框架提示**。 说明如何用这个Creature的格式调用工具
  （bracket / XML / native）、如何使用内嵌 framework commands
  （`read_job`、`info`、`jobs`、`wait`），以及输出协定
  长什么样子。
5.**具名输出区段**。 对每个 `named_outputs.<name>`，简短说明
  何时该把文字路由到该处。
6.**Prompt 插件区段**。 每个已注册的 prompt plugin
  （依优先级排序，由低到高）都会贡献一个区段。内建有：
  `ToolListPlugin`、`FrameworkHintsPlugin`、`EnvInfoPlugin`、
  `ProjectInstructionsPlugin`。

当 MCP 工具已连线时，还会额外插入一个名为
「Available MCP Tools」的区段，依服务器用条列方式列出工具。

## 维持不变的条件

- **具确定性**。 给定相同的 config + registry + plugin 集合，
  产生的 prompt 在位元组层级上是稳定的。
- **自动区段不会取代手写区段**。 如果你在 `system.md` 里自行放入
  工具清单，aggregator 的工具清单仍然会被加入；框架不会依内容去重。
- **Skill mode 是调节旋钮，不是策略**。 系统中其他任何部分都不会因
  `skill_mode` 而改变——它纯粹是 prompt 大小上的取舍。
- **插件顺序是明确的**。 依优先级排序。若优先级相同，则保持稳定的
  插入顺序。

## 代码中的位置

- `src/kohakuterrarium/prompt/aggregator.py` — 组合函数。
- `src/kohakuterrarium/prompt/plugins.py` — 内建 prompt plugins。
- `src/kohakuterrarium/prompt/templates.py` — Jinja 安全渲染。
- `src/kohakuterrarium/terrarium/config.py` — 频道拓扑区块。
- `src/kohakuterrarium/core/Agent.py` — `_init_controller()` 会在
  启动时调用 aggregator 一次。

## 另请参阅

- [Plugin](../modules/plugin.md) — 如何撰写 prompt plugins。
- [Tool](../modules/tool.md) — 工具文件如何被注册。
- [reference/configuration.md — skill_mode, tool_format, include_* 参考](../../reference/configuration.md) — 相关配置旋钮。
