---
title: KohakuTerrarium 文档说明
summary: KohakuTerrarium 是一个用于构建真正 Agent 的框架，而不只是 LLM 包装器。
tags:
  - overview
  - docs
---
# KohakuTerrarium 文档说明

KohakuTerrarium 是一个用于构建真正 Agent 的框架，而不只是 LLM 包装器。

其中的一级抽象是 **creature**：一种可独立运行的代理，拥有自己的控制器、工具、子代理、触发器、提示词以及输入/输出。Creature 可以独立运行、继承自另一个 creature，或随套件一同发布。**terrarium** 则是可选的多代理连接层，可通过 channel 组合多个 creature。所有内容都以 Python 实现——你可以把其中任何部分嵌入自己的代码中。

这份文档分为四个层次：tutorials（教程）、guides（任务导向）、concepts（心智模型）和 reference（完整查询）。请根据你当前所处的阶段选择合适的内容。

## 选择你的路径

| 你现在是... | 从这里开始 |
|---|---|
| **正在评估这个项目** | [快速开始](guides/getting-started.md) · [什么是 Agent](concepts/foundations/what-is-an-agent.md) · [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome) |
| **正在使用 CLI / 仪表板** | [快速开始](guides/getting-started.md) · [服务部署](guides/serving.md) · [CLI 参考](reference/cli.md) |
| **正在创建 creature** | [Creature](guides/creatures.md) · [配置](guides/configuration.md) · [自定义模块](guides/custom-modules.md) |
| **正在嵌入到 Python** | [以编程方式使用](guides/programmatic-usage.md) · [组合](guides/composition.md) · [Python API](reference/python.md) |
| **正在为框架本身做贡献** | [开发指南](dev/README.md) · [框架内部机制](dev/internals.md) · [测试](dev/testing.md) |

## 文档结构

### Tutorials

循序渐进的学习路径。

- [第一个 Creature](tutorials/first-creature.md)
- [第一个 Terrarium](tutorials/first-terrarium.md)
- [第一个 Python 嵌入示例](tutorials/first-python-embedding.md)

### Guides

任务导向文档：回答“如何完成 X”。

- [快速开始](guides/getting-started.md) — 安装、验证、运行和恢复。
- [Creature](guides/creatures.md) — 结构、继承与封装。
- [Terrarium](guides/terrariums.md) — 多代理连接和 root agent。
- [会话](guides/sessions.md) — `.kohakutr` 的持久化与恢复。
- [记忆](guides/memory.md) — 对会话历史进行 FTS、语义和混合搜索。
- [配置](guides/configuration.md) — 面向任务的“如何配置 X”。
- [以编程方式使用](guides/programmatic-usage.md) — `Agent`、`AgentSession`、`TerrariumRuntime`、`KohakuManager`。
- [组合](guides/composition.md) — `>>`、`&`、`|`、`*` 管线。
- [自定义模块](guides/custom-modules.md) — 工具、输入、输出、触发器和子代理。
- [插件](guides/plugins.md) — 提示词与生命周期插件。
- [MCP](guides/mcp.md) — Model Context Protocol 服务器。
- [包](guides/packages.md) — `kohaku.yaml`、安装模式与发布。
- [服务部署](guides/serving.md) — `kt web`、`kt app`、`kt serve` 守护进程。
- [前端布局](guides/frontend-layout.md) — 仪表板面板与预设配置。
- [示例](guides/examples.md) — `examples/` 目录导览。

### Concepts

心智模型——解释事情为什么会这样。Concept 文档讲的是模型，而不是字段清单；它假设你希望理解原理，而不只是完成配置。

- [概览](concepts/README.md)
- [基础概念](concepts/foundations/README.md)
- [模块](concepts/modules/README.md) — controller、input、trigger、tool、sub-agent、output、channel、plugin、memory、session。
- [多代理](concepts/multi-agent/README.md) — terrarium、root agent、channel topology。
- [Python 原生](concepts/python-native/README.md) — 将代理视为 Python 值，以及组合代数。
- [模式](concepts/patterns.md) — agent-inside-plugin、agent-inside-tool 及相关用法。
- [边界](concepts/boundaries.md) — 何时应忽略这层抽象，以及何时这个框架并不适合。
- [实现说明](concepts/impl-notes/) — 流解析、提示词聚合和其他内部细节。

### Reference

完整查询资料。

- [CLI 参考](reference/cli.md) — 每个 `kt` 命令与标志。
- [配置参考](reference/configuration.md) — 每个配置字段、类型与默认值。
- [HTTP API](reference/http.md) — REST 与 WebSocket 端点。
- [Python API](reference/python.md) — 类、方法与协议。
- [内置项目录](reference/builtins.md) — 所有内置工具、子代理和 I/O 模块。
- [插件钩子](reference/plugin-hooks.md) — 每个 hook 的签名。

### Development

提供给框架贡献者。

- [开发指南](dev/README.md)
- [测试](dev/testing.md)
- [框架内部机制](dev/internals.md)
- [前端架构](dev/frontend.md)

## 代码库地图

源码按运行时子系统组织，而不是按读者意图分类。每个子包中的包内 `README.md` 都会说明其职责和依赖方向。

```
src/kohakuterrarium/
  core/             Agent 运行时、controller、executor、events、environment
  bootstrap/        LLM、tools、I/O、triggers 的初始化工厂
  cli/              CLI 命令处理器
  terrarium/        多代理运行时、拓扑连接、hot-plug
  builtins/         内置工具、子代理、I/O 模块、TUI、用户命令
  builtin_skills/   供按需文档化工具与子代理使用的 Markdown skill 清单
  session/          持久化、记忆搜索、embeddings
  serving/          与传输无关的服务管理与事件流
  api/              FastAPI HTTP 与 WebSocket 服务器
  modules/          tools、inputs、outputs、triggers、sub-agents 的协议
  llm/              LLM 提供者、profiles、API 密钥管理
  parsing/          工具调用解析与流式处理
  prompt/           提示词组装、聚合、plugins、skill 加载
  testing/          测试基础设施

src/kohakuterrarium-frontend/   Vue Web 前端
kt-biome (separate repo)        展示用包——creatures、terrariums、plugins
examples/                       可运行示例
docs/                           本目录树
```

## 这份文档的承诺

- **Guides** 告诉你如何完成 X。
- **Concepts** 告诉你为什么 X 会这样运行。
- **Reference** 告诉你有哪些 X。
- **Tutorials** 带你从零做出第一个可用的 X。

如果某个页面写着 “comprehensive”“powerful” 或 “seamless”——那它大概率已经过时了。欢迎提 PR。
