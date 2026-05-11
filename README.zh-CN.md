<p align="center">
  <img src="images/banner.png" alt="KohakuTerrarium" width="800">
</p>
<p align="center">
  <strong>构建 Agent 的引擎 —— 让你无需在每次创建新 Agent 时都从零开始打造。</strong>
</p>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-KohakuTerrarium--1.0-green" alt="License">
  <img src="https://img.shields.io/badge/version-1.4.0-orange" alt="Version">
</p>

<p align="center">
  <a href="README.md">English</a> &nbsp;·&nbsp; <a href="README.zh.md">繁體中文</a> &nbsp;·&nbsp; <strong>简体中文</strong>
</p>
<p align="center">
  <a href="https://terrarium.kohaku-lab.org"><strong>文档网站</strong></a>
</p>

---

## 快速一览 (60 秒)

```bash
pip install kohakuterrarium                                         # 安装
kt login codex                                                      # 认证
kt install https://github.com/Kohaku-Lab/kt-biome.git               # 拉取官方包
kt run @kt-biome/creatures/swe --mode cli                           # 运行一个 Agent
```

你将获得一个交互式的 shell，其中包含完整的编程 Agent — 具备文件工具、Shell 访问、网页搜索、子代理、可恢复的会话。`Ctrl+D` 离开；`kt resume --last` 再恢复。

想要了解更多？看[快速开始](docs/zh-CN/guides/getting-started.md)。想自己构建？看[第一个智能体](docs/zh-CN/tutorials/first-creature.md)。

## 它适合你吗？

**在以下情况下，你可能会想用 KohakuTerrarium：** 你需要一个新的 Agent 形态又不想重建底层；你想要开箱即用 (OOTB) 的智能体同时又希望能自定义；你想将 Agent 行为嵌入现有的 Python 程序中；你的需求还在演化。

**在以下情况下，你可能不需要它：** 现有的 Agent 产品 (Claude Code、Codex…) 已经满足你稳定的需求；你对 Agent 的心智模型跟 controller / tools / triggers / sub-agents / channels 这套对不上；你需要每操作 <50ms 的延迟。更诚实的讨论放在[边界](docs/zh-CN/concepts/boundaries.md)。

## 什么是 KohakuTerrarium

KohakuTerrarium 是**构建 Agent 的框架** — 而不仅仅是另一个 Agent 产品。

过去两年涌现了大量令人惊叹的 Agent 产品：Claude Code、Codex、OpenClaw、Gemini CLI、Hermes Agent、OpenCode…等等。它们各具特色，但往往都在从零开始重复构建同一套底层机制：控制器循环、工具调度、触发器系统、子代理机制、会话、持久化、多 Agent连线。每一次推出新形态的 Agent，底层的流水线都需要重新打造一遍。

KohakuTerrarium 的目标就是将这些底层基础设施统一起来。这样，下一个新形态的 Agent 只需要一份配置文件和几个自定义模块，而无需开启一个新的代码库。

核心抽象是 **智能体 (Creature)**：一个独立的 Agent，拥有自己的控制器、工具、子代理、触发器、记忆和 I/O。智能体由 **Terrarium** 引擎托管：它是负责通道、生命周期、输出连线、热插拔和会话挂载的图运行时。其上还有 **Studio** 管理层，负责目录、身份、活动会话、持久化、实时 trace，以及 Web / 桌面 / API 管理。所有组件都是原生 Python 对象，因此 Agent 可以被无缝嵌入到其他 Agent 的工具、触发器、插件或输出中。

想立刻体验开箱即用 (OOTB) 的智能体，看 [**kt-biome**](https://github.com/Kohaku-Lab/kt-biome) — 官方扩展包，其中包含基于本框架构建的各种实用 Agent 和插件。

## 它的定位

|  | 产品 | 框架 | 工具 / 包装层 |
|--|------|------|---------------|
| **LLM App** | ChatGPT、Claude.ai | LangChain、LangGraph、Dify | DSPy |
| **Agent** | ***kt-biome***、Claude Code、Codex、OpenCode、OpenClaw、Hermes Agent… | ***KohakuTerrarium***、smolAgents | — |
| **多Agent** | ***kt-biome*** | ***KohakuTerrarium*** | CrewAI、AutoGen |

大多数现有的工具要么停留在 Agent 这一层之下（如 LLM 封装），要么直接跳跃到多 Agent 编排，而对 Agent 本身的概念抽象较为薄弱。KohakuTerrarium 则从 Agent 本身的核心概念出发。

一个智能体由以下部分组成：

- **Controller (控制器)** — 推理循环
- **Input (输入)** — 事件如何进入 Agent
- **Output (输出)** — 结果如何离开 Agent
- **Tools (工具)** — 可运行的动作
- **Triggers (触发器)** — 唤醒条件
- **Sub-Agents (子代理)** — 内部委派给专门任务

生态瓶通过通道 (Channels)、生命周期管理和可观察性机制，将多个智能体横向组合在一起。

## 主要特性

- **Agent 层级的抽象。** 六模块的智能体模型是一等公民。每一个新形态的 Agent 都是“编写一份配置 + 可能需要几个自定义模块”，而不是“重写整个运行时”。
- **内置的会话持久化与恢复。** 会话保存的不仅是聊天记录，而是完整的操作状态。几小时后用 `kt resume` 恢复之前的工作。
- **可搜索的会话历史。** 每个事件都会被索引。`kt search` 和 `search_memory` 工具让你 (以及 Agent) 可以检索过去的工作。
- **非阻塞的上下文压缩。** 长时间运行的 Agent 在后台压缩上下文时继续工作。
- **丰富的内置工具与子代理。** 涵盖文件操作、Shell、网页浏览、JSON、notebook/Jupyter、搜索、编辑、规划、审查、研究以及生态瓶管理。
- **MCP 支持。** 可以为单一智能体或全局连接 stdio、streamable HTTP 或旧式 SSE/HTTP MCP 服务器；工具会自动注入到 Prompt 中。
- **包管理系统。** 支持从 Git 或本地路径安装智能体、生态瓶、插件和 LLM 默认配置；可以通过继承的方式组合已安装的包。
- **Python 原生。** Agent 本身就是异步的 Python 对象。可以将它们嵌入到其他 Agent 的工具、触发器、插件或输出处理流程中。
- **组合代数。** 使用 `>>`、`&`、`|`、`*`、`.iterate` 运算符将 Agent 串联成流水线 (Pipeline)。
- **多重运行时接口。** 提供 CLI、TUI、Web Dashboard 以及桌面 App，皆为开箱即用。
- **通过 [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome) 提供实用的开箱即用（OOTB）智能体。** 可以先运行功能强大的默认 Agent，后续再根据需要进行自定义或继承。

## 快速开始

### 1. 安装 KohakuTerrarium

```bash
# 从 PyPI
pip install kohakuterrarium
# 可选附加: pip install "kohakuterrarium[full]"

# 或从源代码 (开发用 — 项目惯例使用 uv)
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"

# 从源代码运行 `kt web` / `kt app` 需要先构建前端
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

### 2. 安装开箱即用（OOTB）的智能体与插件

```bash
# 官方扩展包
kt install https://github.com/Kohaku-Lab/kt-biome.git

# 任何第三方包
kt install <git-url>
kt install ./my-creatures -e        # 可编辑安装
```

### 3. 认证模型提供商

```bash
# Codex OAuth (ChatGPT 订阅)
kt login codex
kt model default gpt-5.4

# 或原生 Anthropic / OpenAI 兼容提供者：`kt config llm add`
```

支持 Codex OAuth、OpenRouter/OpenAI、原生 Anthropic、Google Gemini，以及任何 OpenAI 兼容 API。

### 4. 运行试试

```bash
# 运行单一智能体
kt run @kt-biome/creatures/swe --mode cli
kt run @kt-biome/creatures/researcher

# 运行多 Agent 生态瓶
kt terrarium run @kt-biome/terrariums/swe_team

# Web Dashboard
kt serve start

# 启动原生桌面 App
kt app
```

## 选择你的路径

### 我现在就想运行点东西

- [快速开始](docs/zh-CN/guides/getting-started.md)
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome)
- [CLI 参考](docs/zh-CN/reference/cli.md)
- [示例](examples/README.md)

### 我想自己构建一个智能体

- [第一个智能体教程](docs/zh-CN/tutorials/first-creature.md)
- [编写智能体](docs/zh-CN/guides/creatures.md)
- [自定义模块](docs/zh-CN/guides/custom-modules.md)
- [插件](docs/zh-CN/guides/plugins.md)
- [第一个自定义工具教程](docs/zh-CN/tutorials/first-custom-tool.md)

### 我想构建多 Agent 组合

- [第一个生态瓶教程](docs/zh-CN/tutorials/first-terrarium.md)
- [生态瓶使用指南](docs/zh-CN/guides/terrariums.md)
- [多 Agent 概念](docs/zh-CN/concepts/multi-agent/README.md)

### 我想嵌入 Python

- [第一次 Python 嵌入教程](docs/zh-CN/tutorials/first-python-embedding.md)
- [程序化使用](docs/zh-CN/guides/programmatic-usage.md)
- [组合代数](docs/zh-CN/guides/composition.md)
- [Python API](docs/zh-CN/reference/python.md)

### 我想了解其内部运作原理

- [概念文档](docs/zh-CN/concepts/README.md)
- [词汇表](docs/zh-CN/concepts/glossary.md) — 白话定义
- [Why KohakuTerrarium](docs/zh-CN/concepts/foundations/why-kohakuterrarium.md)
- [什么是 Agent](docs/zh-CN/concepts/foundations/what-is-an-agent.md)

### 我想为框架贡献代码

- [开发者首页](docs/zh-CN/dev/README.md)
- [内部架构](docs/zh-CN/dev/internals.md)
- [测试指南](docs/zh-CN/dev/testing.md)
- 各个子包的 README 请参考 [`src/kohakuterrarium/`](src/kohakuterrarium/README.md)

## 核心心智模型

### 智能体 (Creature)

```text
           list / create / delete
                  |
                  v
            +------------+
            |   Tools    |
            +------------+
                  ^
                  |
+---------+   +---------+   +--------------+   +--------+
| Input   |-->| Trigger |-->| Controller   |-->| Output |
+---------+   | /System |   |  (main LLM)  |   +--------+
    ^         +---------+   +--------------+
    |               |              |
    |               |              v
用户输入            |         +------------+
                    +-------->| Sub-agent  |
                              +------------+
```

智能体是独立的 Agent，拥有自身的运行时、工具集、子代理、Prompt 以及状态。

```bash
kt run path/to/creature
kt run @package/path/to/creature
```

### 运行时层级

```text
用户 / API / 桌面端
        |
        v
+----------------------+     不做 Agent 推理
| Studio / App 层      |  目录、身份、活动会话、持久化、
|                      |  attach、编辑器、实时 trace
+----------------------+
        |
        v
+----------------------+     无 LLM、无决策
| Terrarium 引擎       |  智能体图、拓扑、通道、生命周期、
|                      |  输出连线、会话挂载
+----------+-----------+
           |
   +-------+----------------+
   |                        |
Root 智能体              工作团队智能体
(面向用户)               swe / coder / reviewer / ...
   |
   v
每个智能体内部的子代理
(纵向 / 私有委派)
```

- **Studio** 是 Web Dashboard、桌面 App 和 HTTP API 使用的管理门面。它负责目录视图、身份与设置、活动会话、持久化、attach/resume、编辑器和实时 trace；它不做推理。
- **Terrarium** 是托管进程内所有运行中智能体的运行时引擎。独立 Agent 是一个单智能体图；多 Agent 团队则是 connected graph。引擎管理拓扑、通道、生命周期、热插拔、输出连线和会话挂载；没有 LLM，也不做决策。
- **Root 智能体** 是可选的。它是由同一个 Terrarium 引擎托管的普通智能体，在概念上位于工作团队之外，并通过 terrarium 管理工具面向用户。
- **智能体 (Creature)** 拥有推理能力：控制器、工具、触发器、子代理、插件、记忆、I/O、提示词和私有状态。它不需要知道自己是独立运行还是图中的一个节点。
- **子代理** 是单个智能体内部的纵向 / 私有委派。当一个控制器能够在内部拆解任务时优先使用子代理；当多个平级智能体需要横向协作时使用 Terrarium。

### 通道与输出连线

通道和输出连线构成智能体之间的横向协作机制：

- **Queue** — 每条消息只会被一个消费者接收。
- **Broadcast** — 每条消息会被所有订阅者接收。
- **输出连线 (output wiring)** — 确定性的 pipeline 边，会把某个智能体回合结束输出自动送到指定目标。

### 模块

一个智能体包含六个概念模块。**其中五个是用户可扩展的** —— 可以在配置文件或 Python 中替换实现。第六个模块——控制器——负责驱动整体推理循环；通常你几乎不会替换它（如果真的需要替换，那你实际上已经在写下一代框架了）。

| 模块 | 作用 | 自定义示例 |
| --- | --- | --- |
| **Input** | 接收外部事件 | Discord 监听器、Webhook、语音输入 |
| **Output** | 发送 Agent 输出 | Discord 发送器、TTS、文件写入 |
| **Tool** | 执行操作 | API 调用、数据库访问、RAG 检索 |
| **Trigger** | 产生自动化事件 | 计时器、调度器、通道监控器 |
| **Sub-Agent** | 委派执行任务 | 规划、代码审查、研究 |

另外还有**插件**，用于修改模块**之间**的连接方式，而不需要 fork 它们（例如 Prompt 插件、lifecycle hook）。详见 [插件使用指南](docs/zh-CN/guides/plugins.md)。

### 环境与会话

- **环境 (Environment)** — 生态瓶共享状态 (共用通道)。
- **会话 (Session)** — 智能体私有状态 (scratchpad、私有通道、子代理状态)。

默认情况下状态是私有的，共享状态需要显式声明 (opt-in)。

## 核心能力

KohakuTerrarium 开箱即内置了以下功能：

- 文件、Shell、网页、JSON、notebook/Jupyter、通道、触发器、内省工具，包括单次编辑与多点编辑原语。
- 用于探索、规划、实现、审查、摘要和研究的内置子代理。
- 背景工具运行与非阻塞的 Agent 流程。
- 会话持久化，支持随时恢复之前的操作状态。
- FTS + 向量记忆搜索（model2vec / sentence-transformer / API embedding 提供者）。
- 针对长时间运行的 Agent 提供非阻塞的自动上下文压缩。
- MCP (Model Context Protocol) 集成 —— 支持 stdio、streamable HTTP 和旧式 SSE/HTTP 传输。
- 智能体、插件、生态瓶、可复用 Agent 包的包管理器 (`kt install`、`kt update`)。
- 通过 `Terrarium` 引擎嵌入 Python，需要更底层控制时也可直接使用 `Agent`。
- 内置 HTTP 与 WebSocket 服务器。
- Web Dashboard 与原生桌面 App。
- 灵活的自定义模块与插件系统。

## 程序化使用

Agent 是异步的 Python 对象。每个进程一个 `Terrarium` 引擎托管所有运行中的智能体 —— 独立 Agent 就是引擎里的 1-creature graph。

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    # 独立 Agent
    engine, alice = await Terrarium.with_creature("@kt-biome/creatures/swe")
    try:
        async for chunk in alice.chat("说明这个代码库是做什么的。"):
            print(chunk, end="", flush=True)
    finally:
        await engine.shutdown()

    # 多 Agent recipe
    engine = await Terrarium.from_recipe("@kt-biome/terrariums/swe_team")
    try:
        async for chunk in engine["swe"].chat("修复 auth bug。"):
            print(chunk, end="", flush=True)
    finally:
        await engine.shutdown()

asyncio.run(main())
```

### 组合代数

因为 Agent 本身是 Python 对象，所以它们可以使用运算符进行组合。支持：`>>`（顺序）、`&`（并行）、`|`（回退）、`*N`（重试）、`.iterate`（异步循环）：

```python
import asyncio
from kohakuterrarium.compose import agent, factory
from kohakuterrarium.core.config import load_agent_config

def make_agent(name, prompt):
    config = load_agent_config("@kt-biome/creatures/general")
    config.name, config.system_prompt, config.tools, config.subagents = name, prompt, [], []
    return config

async def main():
    # 持久化 Agent（对话上下文会累积）
    async with await agent(make_agent("writer", "你是一位专业的作家。")) as writer, \
               await agent(make_agent("reviewer", "你是严格的代码审查者。如果没问题请回复 APPROVED。")) as reviewer:

        pipeline = writer >> (lambda text: f"审查这篇内容：\n{text}") >> reviewer

        async for feedback in pipeline.iterate("写一首关于编程的俳句"):
            print(f"Reviewer: {feedback[:100]}")
            if "APPROVED" in feedback:
                break

    # 并行 ensemble + retry + fallback
    fast = factory(make_agent("fast", "请简短回答。"))
    deep = factory(make_agent("deep", "请提供详尽的回答。"))
    safe = (fast & deep) >> (lambda results: max(results, key=len))
    safe_with_retry = (safe * 2) | fast
    print(await safe_with_retry("什么是递归？"))

asyncio.run(main())
```

了解更多：[程序化使用](docs/zh-CN/guides/programmatic-usage.md)、[组合](docs/zh-CN/guides/composition.md)、[Python API](docs/zh-CN/reference/python.md)、[`examples/code/`](examples/)。

## 运行时接口

### CLI 与 TUI

- **cli** — 丰富的命令行交互体验
- **tui** — 全屏 Textual 应用
- **plain** — 简单的 stdout/stdin 交互，适用于管道和 CI

请参考 [CLI 参考](docs/zh-CN/reference/cli.md)。

### Web Dashboard

基于 Vue 的 Dashboard 加上由 Studio 管理层驱动的 FastAPI 后端服务器。

```bash
kt web                       # 一次性、前台运行
kt serve start               # 长期常驻
# 前端开发：npm run dev --prefix src/kohakuterrarium-frontend
```

请参考 [HTTP API](docs/zh-CN/reference/http.md)、[Serving 指南](docs/zh-CN/guides/serving.md)、[前端架构](docs/zh-CN/dev/frontend.md)。

### 桌面 App

`kt app` 会在原生桌面窗口中打开网页 UI（需要 `pywebview`）。

## 会话、记忆与恢复

会话默认存储在 `~/.kohakuterrarium/sessions/` (除非停用)。

```bash
kt resume            # 交互式选择
kt resume --last     # 恢复最近的一次会话
kt resume swe_team   # 使用名称前缀进行恢复
```

同一个存储系统也为可搜索历史提供支持：

```bash
kt embedding <session>                       # 构建全文 (FTS) + 向量索引
kt search <session> "auth bug fix"           # 支持混合 / 语义 / 全文搜索
```

而且 Agent 可以通过 `search_memory` 工具搜索自己的历史。

`.kohakutr` 文件保存对话、工具调用、事件、scratchpad、子代理状态、通道消息、job、可恢复的触发器、设置 metadata。

请参考 [会话](docs/zh-CN/guides/sessions.md)、[记忆](docs/zh-CN/guides/memory.md)。

## 包管理、默认配置与示例

智能体从设计之初就支持被打包、安装、复用和分享。

```bash
kt install https://github.com/someone/cool-creatures.git
kt install ./my-creatures -e
kt list
kt update --all
```

用包引用并运行已安装的设置：

```bash
kt run @cool-creatures/creatures/my-Agent
kt terrarium run @cool-creatures/terrariums/my-team
```

可用资源：

- [`kt-biome/`](https://github.com/Kohaku-Lab/kt-biome) — 官方提供的演示智能体、生态瓶以及插件包
- `examples/agent-apps/` — 配置驱动的智能体示例
- `examples/code/` — Python API 使用示例
- `examples/terrariums/` — 多 Agent 示例
- `examples/plugins/` — 插件示例

请参考 [examples/README.md](examples/README.md)。

## Codebase 地图

```text
src/kohakuterrarium/
  core/              # Agent 运行时、控制器、执行器 (Executor)、事件流、Environment
  bootstrap/         # Agent 初始化构建工厂 (LLM、工具、I/O、触发器、插件)
  cli/               # `kt` 命令分派
  studio/            # 管理门面：目录、身份、会话、持久化、attach、编辑器
  terrarium/         # 运行时引擎：智能体图、拓扑、通道、输出连线、热插拔
  builtins/          # 内置的工具集、子代理、I/O 模块、TUI、用户命令以及 CLI UI
  builtin_skills/    # Markdown 技能文件 (按需加载的参考说明)
  session/           # 会话状态持久化、记忆检索、Embeddings 向量化
  serving/           # 启动/传输辅助与兼容事件流包装层
  api/               # 基于 Studio 与 Terrarium 的 FastAPI HTTP / WebSocket adapter
  compose/           # 组合代数原语实现
  mcp/               # MCP (Model Context Protocol) 客户端管理器
  modules/           # 工具、输入、输出、触发器、子代理以及用户命令的基础协议 (Base Protocol)
  llm/               # LLM 提供者、配置文件、API 密钥管理
  parsing/           # 工具调用解析以及流式处理 (Streaming)
  prompt/            # Prompt 模板聚合、插件机制、技能 (Skill) 加载
  testing/           # 测试基础设施 (ScriptedLLM、TestAgentBuilder、recorder)

src/kohakuterrarium-frontend/   # 基于 Vue 构建的前端项目
kt-biome/                       # (独立代码库) 官方提供的开箱即用 (OOTB) 包合集
examples/                       # 智能体、生态瓶、代码与插件的示例集合
docs/                           # 包含教程、使用指南、核心概念、API 参考及开发指南
```

每个子包都有自己的 README 文档、依赖方向、不变式。

## 文档地图

完整的文档请参考 [`docs/`](docs/zh-CN/README.md)。

### 教程
[第一个智能体](docs/zh-CN/tutorials/first-creature.md) · [第一个生态瓶](docs/zh-CN/tutorials/first-terrarium.md) · [第一次 Python 嵌入](docs/zh-CN/tutorials/first-python-embedding.md) · [第一个自定义工具](docs/zh-CN/tutorials/first-custom-tool.md) · [第一个插件](docs/zh-CN/tutorials/first-plugin.md)

### 使用指南
[快速开始](docs/zh-CN/guides/getting-started.md) · [编写智能体](docs/zh-CN/guides/creatures.md) · [生态瓶](docs/zh-CN/guides/terrariums.md) · [会话](docs/zh-CN/guides/sessions.md) · [记忆](docs/zh-CN/guides/memory.md) · [配置文件](docs/zh-CN/guides/configuration.md) · [程序化使用](docs/zh-CN/guides/programmatic-usage.md) · [组合](docs/zh-CN/guides/composition.md) · [自定义模块](docs/zh-CN/guides/custom-modules.md) · [插件](docs/zh-CN/guides/plugins.md) · [MCP](docs/zh-CN/guides/mcp.md) · [包](docs/zh-CN/guides/packages.md) · [Serving](docs/zh-CN/guides/serving.md) · [示例](docs/zh-CN/guides/examples.md)

### 概念
[词汇表](docs/zh-CN/concepts/glossary.md) · [Why KohakuTerrarium](docs/zh-CN/concepts/foundations/why-kohakuterrarium.md) · [什么是 Agent](docs/zh-CN/concepts/foundations/what-is-an-agent.md) · [组合一个 Agent](docs/zh-CN/concepts/foundations/composing-an-agent.md) · [模块](docs/zh-CN/concepts/modules/README.md) · [Agent 作为 Python 对象](docs/zh-CN/concepts/python-native/agent-as-python-object.md) · [组合代数](docs/zh-CN/concepts/python-native/composition-algebra.md) · [多 Agent](docs/zh-CN/concepts/multi-agent/README.md) · [模式](docs/zh-CN/concepts/patterns.md) · [边界](docs/zh-CN/concepts/boundaries.md)

### 参考
[CLI](docs/zh-CN/reference/cli.md) · [HTTP](docs/zh-CN/reference/http.md) · [Python API](docs/zh-CN/reference/python.md) · [配置文件](docs/zh-CN/reference/configuration.md) · [内置模块](docs/zh-CN/reference/builtins.md) · [插件 hook](docs/zh-CN/reference/plugin-hooks.md)

## Roadmap

近期计划：更可靠的生态瓶流程、更丰富的 UI 输出 / 交互模块 (CLI / TUI / 网页)、更多内置智能体、插件、集成、更好的 daemon 背后的工作流 (给长时间运行与远程使用)。请参考 [ROADMAP.md](ROADMAP.md)。

## 贡献

- [贡献指南](docs/zh-CN/dev/README.md)
- [测试指南](docs/zh-CN/dev/testing.md)
- [内部架构](docs/zh-CN/dev/internals.md)
- [前端架构](docs/zh-CN/dev/frontend.md)

## 开源协议

[KohakuTerrarium License 1.0](LICENSE)：以 Apache-2.0 为基础，加上命名与署名要求。

- 衍生作品的名称必须包含 `Kohaku` 或 `Terrarium`。
- 衍生作品须在显著位置附上指向本项目的署名与链接。

Copyright 2024-2026 Shih-Ying Yeh (KohakuBlueLeaf) 与贡献者。

## 社区
- QQ: 1097666427
- Discord: https://discord.gg/xWYrkyvJ2s
- Forum: https://linux.do/
