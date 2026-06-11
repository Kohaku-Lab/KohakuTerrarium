<p align="center">
  <img src="images/banner.png" alt="KohakuTerrarium" width="800">
</p>
<p align="center">
  <strong>一台“造 Agent 的机器”，让你不必每造一个新 Agent，就把机器本身重造一遍。</strong>
</p>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-KohakuTerrarium--1.0-green" alt="License">
  <img src="https://img.shields.io/badge/version-2.0.0-orange" alt="Version">
</p>

<p align="center">
  <a href="README.md">English</a> &nbsp;·&nbsp; <a href="README.zh.md">繁體中文</a> &nbsp;·&nbsp; <strong>简体中文</strong>
</p>
<p align="center">
  <a href="https://terrarium.kohaku-lab.org"><strong>文档网站</strong></a>
</p>

---

## 跑起来看看（60 秒）

```bash
pip install kohakuterrarium                 # 安装
kt login codex                              # 认证一个模型提供商
kt install @kt-biome                        # 安装官方生物包
kt run @kt-biome/creatures/swe --mode cli   # 运行一个完整的编程 Agent
```

你会得到一个交互式终端，里面是一个完整的编程 Agent：文件工具、Shell 访问、网页搜索、子代理、可恢复的会话，一应俱全。`Ctrl+D` 退出；`kt resume --last` 从你停下的地方原样接着干。

同一个 Agent，当库来用只要四行：

```python
from kohakuterrarium import Agent

agent = await Agent.build("@kt-biome/creatures/swe")
await agent.start()
result = await agent.run("Explain what this codebase does.")  # -> TurnResult
print(result.text, result.usage)
```

想要更细致的引导？看[快速上手](docs/zh-CN/guides/getting-started.md)。想自己造一个？看[第一个生物](docs/zh-CN/tutorials/first-creature.md)。想嵌进自己的程序？看[编程式用法](docs/zh-CN/guides/programmatic-usage.md)。

## 它适合你吗？

**你大概率需要 KohakuTerrarium，如果：**你想做一种新形态的 Agent，又不想重造底层；你想要开箱即用、还能深度定制的 Agent；你想用自己的 Python 代码驱动 Agent（批处理、机器人、流水线）；你的需求还在不断变化。

**你大概率不需要它，如果：**现成的 Agent 产品（Claude Code、Codex 等）已经满足你稳定的需求；controller / tools / triggers / sub-agents / channels 这套模型跟你的思路对不上；你需要单次操作 50 ms 以内的延迟。更坦诚的讨论见[边界](docs/zh-CN/concepts/boundaries.md)。

## KohakuTerrarium 是什么

KohakuTerrarium 是一个构建 Agent 的框架，而不是又一个 Agent。

过去两年涌现了大量 Agent 产品：Claude Code、Codex、Gemini CLI、OpenCode、OpenClaw、Hermes Agent……它们确实是不同的产品，但都在从零重写同一套底层：控制器循环、工具调度、触发器、子代理、会话、持久化、多 Agent 连线。每出现一种新的 Agent 形态，这套管线就要重造一遍。

KohakuTerrarium 把这套底层收拢到一处。于是下一种 Agent 形态的成本变成一份配置文件加几个自定义模块，而不是一个新仓库。

核心抽象是**生物 (creature)**：一个独立的 Agent，拥有自己的控制器、工具、子代理、触发器、记忆和 I/O。生物由 **Terrarium 引擎**托管：这是一个图运行时，负责频道、生命周期、输出连线、热插拔，以及图结构变化时随之而来的拓扑与会话簿记。再往上是 **Studio** 管理层，负责目录、身份、活动会话、持久化，以及 Web / 桌面 / API 这些管理界面。可选的 **Laboratory** 传输层能把宿主和引擎拆到不同机器上：Studio 和 Terrarium 原样不动，中间插进一段 WebSocket 跳转。

一切都是 Python。Agent 是可以 `await` 的对象，返回带类型的结果，可以嵌进你的工具、你的机器人、你的批处理任务，甚至嵌进别的 Agent 里。

想立刻体验开箱即用的生物，看 [**kt-biome**](https://github.com/Kohaku-Lab/kt-biome)，这是基于本框架构建的官方实用 Agent 与插件包。

## 它的定位

|  | 产品 | 框架 | 工具 / 包装层 |
|--|------|------|---------------|
| **LLM 应用** | ChatGPT、Claude.ai | LangChain、LangGraph、Dify | DSPy |
| **Agent** | ***kt-biome***、Claude Code、Codex、OpenCode、OpenClaw、Hermes Agent… | ***KohakuTerrarium***、smolagents | （无） |
| **多 Agent** | ***kt-biome*** | ***KohakuTerrarium*** | CrewAI、AutoGen |

大多数工具要么位于 Agent 层之下，要么直接跳到多 Agent 编排、却只带着一个很单薄的“Agent”概念。KohakuTerrarium 从 Agent 本身做起。

一个生物由这些部分组成：

- **控制器（Controller）**：推理循环
- **输入（Input）**：事件如何进入 Agent
- **输出（Output）**：结果如何离开 Agent
- **工具（Tools）**：它能执行哪些动作
- **触发器（Triggers）**：什么会唤醒它
- **子代理（Sub-agents）**：面向专项任务的内部委派

一个 terrarium 则通过频道、生命周期管理和可观测性，把多个生物横向组合起来。

## 关键特性

- **以 Agent 为单位的抽象。**六模块的生物模型是一等概念。做一种新的 Agent 形态等于“写份配置、也许加几个自定义模块”，而不是“重建运行时”。
- **货真价实的 Python API。**`Agent.build`、带类型的 `TurnResult` 轮次、真正能取消的超时、带类型的流式事件、用 `@kt.tool` 把任意函数变成 Agent 工具、直接注入 LLM 实例、默认严格报错而非静默回退。
- **内置会话持久化与恢复。**引擎负责创建并持有会话文件（`session=` / `Terrarium(session_dir=)`）；几小时后用 `kt resume` 或 `Terrarium.resume` 接着干。`SessionReader` 可以离线重放任何已完成的运行。
- **可搜索的会话历史。**每个事件都会被索引。`kt search` 和 `search_memory` 工具让你（和 Agent 自己）查找过去的工作。
- **非阻塞的上下文压缩。**长时间运行的 Agent 在后台压缩上下文的同时继续干活。
- **完备的内置工具与子代理。**文件、Shell、网页、JSON、Notebook、搜索、编辑、规划、评审、研究，外加特权节点上的 `group_*` 图编辑工具。
- **MCP 支持。**按 Agent 或全局连接 stdio / streamable-HTTP MCP 服务器；四个元工具保证不管接多少服务器，提示词都不会膨胀。
- **包系统 + 市场。**`kt install @name` 通过 [TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket) 解析；`kohakuterrarium.packages.ensure("@name")` 是脚本侧的幂等原语。
- **组合代数。**用 `>>`、`&`、`|`、`*`、`.iterate` 把 Agent 拼成流水线。
- **多种运行界面。**CLI、TUI、Web 仪表盘、原生桌面应用，开箱即用。
- **可选的四层认证。**主机令牌、管理员密码、多用户账户，按层选用；默认全部关闭。见[认证](docs/zh-CN/guides/authentication.md)。

## 快速开始

> **推荐 Python 版本**：3.12 或更新。CI 验证 3.12+；3.10 和 3.11 仍可安装运行（`requires-python = ">=3.10"`），但仅尽力支持。

### 1. 安装 KohakuTerrarium

```bash
# From PyPI
pip install kohakuterrarium
# Optional extras: pip install "kohakuterrarium[full]"

# Or from source (for development; uv is the project convention)
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"

# Build the web frontend (required for `kt web` / `kt app` from source)
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

### 2. 安装开箱即用的生物与插件

```bash
kt install @kt-biome                 # 官方包，经由 TerrariumMarket
kt marketplace search                # 浏览市场里的所有内容
kt install <git-url>                 # 用 URL 安装任意第三方包
kt install ./my-creatures -e         # 本地可编辑安装
```

来源配置、版本锁定和环境变量覆盖见 [`docs/zh-CN/guides/packages.md`](docs/zh-CN/guides/packages.md)。

### 3. 认证一个模型提供商

```bash
kt login codex                       # Codex OAuth（ChatGPT 订阅）
kt model default gpt-5.4
# API-key 类提供商用 `kt config key set <provider>`
```

支持 Codex OAuth、OpenRouter/OpenAI、Anthropic 原生、Google Gemini、Kimi Code、GLM Coding Plan，以及任何 OpenAI 兼容 API。

### 4. 跑点什么

```bash
kt run @kt-biome/creatures/swe --mode cli       # 单个生物
kt terrarium run @kt-biome/terrariums/swe_team  # 多 Agent 团队
kt serve start                                  # Web 仪表盘
kt app                                          # 原生桌面应用
kt doctor                                       # 体检你的环境
```

## 选一条路

### 我想马上跑点什么

- [快速上手](docs/zh-CN/guides/getting-started.md)
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome)
- [CLI 参考](docs/zh-CN/reference/cli.md)
- [示例](examples/README.md)

### 我想构建自己的生物

- [第一个生物教程](docs/zh-CN/tutorials/first-creature.md)
- [生物指南](docs/zh-CN/guides/creatures.md)
- [自定义模块](docs/zh-CN/guides/custom-modules.md)
- [插件](docs/zh-CN/guides/plugins.md)
- [第一个自定义工具教程](docs/zh-CN/tutorials/first-custom-tool.md)

### 我想要多 Agent 组合

- [第一个 Terrarium 教程](docs/zh-CN/tutorials/first-terrarium.md)
- [Terrarium 指南](docs/zh-CN/guides/terrariums.md)
- [多 Agent 概念](docs/zh-CN/concepts/multi-agent/README.md)

### 我想把它嵌进 Python

- [第一次 Python 嵌入教程](docs/zh-CN/tutorials/first-python-embedding.md)
- [编程式用法](docs/zh-CN/guides/programmatic-usage.md)
- [组合代数](docs/zh-CN/guides/composition.md)
- [Python API 参考](docs/zh-CN/reference/python.md)

### 我想搞清楚里面发生了什么

- [概念文档](docs/zh-CN/concepts/README.md)
- [术语表](docs/zh-CN/concepts/glossary.md)：大白话定义
- [为什么是 KohakuTerrarium](docs/zh-CN/concepts/foundations/why-kohakuterrarium.md)
- [什么是 Agent](docs/zh-CN/concepts/foundations/what-is-an-agent.md)

### 我想部署它

- [Docker 部署](docs/zh-CN/guides/deployment-docker.md)：AIO、宿主 + 工作机、分布式 compose 配方
- [systemd 部署](docs/zh-CN/guides/deployment-systemd.md)：`kt service install` + 加固的 unit
- [反向代理部署](docs/zh-CN/guides/deployment-reverse-proxy.md)：nginx / Cloudflare Tunnel + TLS
- [Laboratory](docs/zh-CN/guides/laboratory.md)：多节点 lab-host / lab-client 模型

### 我想参与框架本身的开发

- [开发主页](docs/zh-CN/dev/README.md)
- [内部机制](docs/zh-CN/dev/internals.md)
- [测试](docs/zh-CN/dev/testing.md)
- [`AGENTS.md`](AGENTS.md)：面向编码 Agent 的单文件简报
- [`src/kohakuterrarium/`](src/kohakuterrarium/README.md) 下各子包的 README

## 核心心智模型

### 生物 (Creature)

```text
    List, Create, Delete  +------------------+
                    +-----|   Tools System   |
      +---------+   |     +------------------+
      |  Input  |   |          ^        |
      +---------+   V          |        v
        |   +---------+   +------------------+   +--------+
        +-->| Trigger |-->|    Controller    |-->| Output |
User input  | System  |   |    (Main LLM)    |   +--------+
            +---------+   +------------------+
                              |          ^
                              v          |
                          +------------------+
                          |    Sub Agents    |
                          +------------------+
```

生物是一个独立的 Agent，拥有自己的运行时、工具、子代理、提示词和状态。

```bash
kt run path/to/creature
kt run @package/path/to/creature
```

### 运行时层级

```text
User / API / Desktop
        |
        v
+----------------------+     no reasoning loop
| Studio / App Layer   |  catalog, identity, active sessions,
|                      |  persistence, attach, editors, live traces
+----------------------+
        |
        v
+----------------------+     optional: only in multi-node mode
| Laboratory (Lab)     |  WebSocket transport + custom envelope,
|                      |  spans the host across N worker machines
+----------------------+     transparent to Studio + Terrarium
        |
        v
+----------------------+     no LLM; owns structure
| Terrarium Engine     |  creature graph, topology, channels,
|                      |  hot-plug, output wiring, session
|                      |  merge / split bookkeeping
+----------+-----------+
           |
   +-------+----------------+
   |                        |
Privileged node         Worker creatures
(user-facing, group     swe / coder / reviewer / ...
 tools, designated by
 recipe `root:`)
   |
   v
Sub-agents inside each creature
(vertical/private delegation)
```

- **Studio** 是 Web 仪表盘、桌面应用和 HTTP API 共用的管理框架。它负责目录视图、身份与设置、活动会话、持久化、挂载/恢复、编辑器和实时 trace。它不做推理。
- **Laboratory (Lab)** 是 Studio 和 Terrarium 之间可选的网络层。单机模式下它甚至不会被导入。在 `--mode lab-host` 下，一台宿主通过 WebSocket 协调 N 台工作机上的生物；Studio 和 Terrarium 不需要任何改动。见 [Laboratory 概念](docs/zh-CN/concepts/laboratory.md)与[指南](docs/zh-CN/guides/laboratory.md)。
- **Terrarium** 是托管进程内所有运行中生物的运行时引擎。单独运行的 Agent 就是一张单生物的图；团队则是一张连通图。引擎不跑 LLM，但掌管*结构*：哪些生物同属一个连通分量、存在哪些频道、轮次结束的输出送到哪里、哪个会话存储支撑哪张图，以及拓扑变化后的自动合并/自动拆分簿记。
- **特权节点**是被授予 `group_*` 工具（图编辑器：生成/移除生物、建立/删除频道、启动/停止成员）的生物。配方的 `root:` 关键字会把某个节点提升为特权节点并套上标准的面向用户连线；特权也可以内联授予（`privileged: true`）或命令式授予（`is_privileged=True`）。
- **生物**掌管推理：控制器、工具、触发器、子代理、插件、记忆、I/O、提示词、私有状态。生物不需要知道自己是单独运行还是身处一张图中。
- **子代理**是单个生物内部的纵向/私有委派。当一个控制器能在内部拆解任务时优先用子代理；当对等的生物需要横向协作时再用 Terrarium。

### 频道与输出连线

- **频道（Channel）**：具名的广播管道。每个监听者都会收到每条消息。适合条件性、可选或观察类的流量。
- **输出连线（Output wiring）**：确定性的流水线边，自动把生物轮次结束的输出投递到具名目标，无需 `send_message`。

### 模块

一个生物有六个概念模块。**其中五个可由用户扩展**：你可以在配置或 Python 里替换它们的实现。第六个是控制器，即驱动这一切的推理循环。

| 模块 | 职责 | 自定义示例 |
|------|------|-----------|
| **输入** | 接收外部事件 | Discord 监听器、Webhook、语音输入 |
| **输出** | 投递 Agent 输出 | Discord 发送器、TTS、文件写入 |
| **工具** | 执行动作 | API 调用、数据库访问、RAG 检索 |
| **触发器** | 产生自动事件 | 定时器、调度器、频道监视 |
| **子代理** | 受委派的任务执行 | 规划、代码评审、研究 |

此外还有**插件**：在不分叉模块的前提下，改写模块*之间*的连接（提示词插件、生命周期钩子、门控）。见[插件指南](docs/zh-CN/guides/plugins.md)。

### 环境与会话

- **环境（Environment）**：terrarium 共享状态（共享频道）。
- **会话（Session）**：生物私有状态（便笺、私有频道、子代理状态）。

默认私有，按需共享。

## 编程式用法

Agent 是带类型结果的异步 Python 值。三种用法，从小到大：

先从一个裸 Agent 开始：构建、跑一个轮次、读 `TurnResult`。

```python
import asyncio
from kohakuterrarium import Agent, tool

@tool
def count_words(text: str) -> str:
    """Count the words in a text."""
    return str(len(text.split()))

async def main():
    agent = await Agent.build(
        "@kt-biome/creatures/general",
        llm="default",            # profile name, preset, or a provider instance
        tools=[count_words],      # plain functions become agent tools
    )
    await agent.start()
    result = await agent.run("How many words in the README?", timeout=300)
    print(result.status, result.text, result.usage)   # failures are typed, not silent
    await agent.stop()

asyncio.run(main())
```

再到引擎：托管多个生物，各自有工作目录和持久化会话。

```python
from kohakuterrarium import Terrarium

async with Terrarium() as engine:
    worker = await engine.add_creature(
        "@kt-biome/creatures/swe",
        llm="fast",                          # bad name? raises here, not mid-run
        pwd=workdir,                         # per-creature cwd, no global chdir
        session=workdir / "run.kohakutr",    # engine mints + closes the store
    )
    result = await worker.run("Fix the failing test.", timeout=1800)
```

一个引擎托管 60 个生物和托管 1 个一样轻松。完整的批处理模式见 [`examples/code/batch_grading.py`](examples/code/batch_grading.py)（约 50 行），事后重放任何一次运行见 [`SessionReader`](docs/zh-CN/reference/python.md)。

最后是组合代数，把 Agent 和普通可调用对象拼成流水线：

```python
from kohakuterrarium.compose import agent, factory

async with await agent("@kt-biome/creatures/swe") as swe:
    result = await (swe >> extract_code >> reviewer)(task)

# Operators: >> (sequence), & (parallel), | (fallback), * (retry)
safe = (expert * 2) | generalist
results = await (analyst & writer & designer)(task)

async for draft in (writer >> reviewer).iterate(task):
    if "APPROVED" in draft:
        break
```

更多：[编程式用法](docs/zh-CN/guides/programmatic-usage.md)、[组合](docs/zh-CN/guides/composition.md)、[Python API](docs/zh-CN/reference/python.md)，以及 [`examples/code/`](examples/)。

## 运行界面

### CLI 与 TUI

- **cli**：富文本的内联终端体验
- **tui**：全屏 Textual 应用
- **plain**：面向管道和 CI 的纯 stdout/stdin

见 [CLI 参考](docs/zh-CN/reference/cli.md)。

### Web 仪表盘

基于 Vue 的仪表盘 + FastAPI 服务器，底层是 Studio 管理层。

```bash
kt web                       # 一次性，前台运行
kt serve start               # 长驻守护进程
# Frontend dev: npm run dev --prefix src/kohakuterrarium-frontend
```

见 [HTTP API](docs/zh-CN/reference/http.md)、[服务指南](docs/zh-CN/guides/serving.md)、[前端架构](docs/zh-CN/dev/frontend.md)。

### 桌面应用

`kt app` 把 Web UI 放进原生桌面窗口（需要 `pywebview`）。

### 部署（Docker / systemd / 多节点）

GHCR 上有三个官方 Docker 镜像，按需选择：

```bash
# AIO: lab-host + an embedded worker in one container
docker run -d -p 8001:8001 -v kt:/home/kt/.kohakuterrarium \
  ghcr.io/kohaku-lab/kohakuterrarium:2.0.0

# Host + workers (different boxes): two images, same shared token
docker run -d -p 8001:8001 -p 8100:8100 \
  -e KT_HOST_TOKEN=$TOKEN ghcr.io/kohaku-lab/kohakuterrarium-host:2.0.0
docker run -d -e KT_HOST_URL=ws://host:8100 -e KT_HOST_TOKEN=$TOKEN \
  -e KT_CLIENT_NAME=worker-a ghcr.io/kohaku-lab/kohakuterrarium-client:2.0.0
```

偏好 systemd 的话，一条命令就能安装加固的原生服务：

```bash
sudo kt service install --all                              # AIO unit
sudo kt service install --host                             # host unit
sudo kt service install --client --name worker-a --host-url ws://… --host-token …
sudo systemctl enable --now kohakuterrarium-host kohakuterrarium-client@worker-a
```

`examples/deployment/` 下有现成的 compose 文件（AIO、宿主 + 工作机、分布式）和用于 TLS 终结的 nginx 模板。`/healthz` + `/readyz` 端点支撑 Docker `HEALTHCHECK` 与反向代理的主动健康检查。

见 [Docker 部署](docs/zh-CN/guides/deployment-docker.md)、[systemd 部署](docs/zh-CN/guides/deployment-systemd.md)、[反向代理部署](docs/zh-CN/guides/deployment-reverse-proxy.md)。

## 会话、记忆与恢复

除非禁用，会话保存在 `~/.kohakuterrarium/sessions/`。

```bash
kt resume            # 交互式挑选
kt resume --last     # 恢复最近一次
kt resume swe_team   # 按名称前缀恢复
```

同一份存储也支撑可搜索的历史：

```bash
kt embedding <session>                       # 构建 FTS + 向量索引
kt search <session> "auth bug fix"           # 混合/语义/FTS 搜索
```

Agent 可以通过 `search_memory` 工具搜索自己的历史，Python 可以重放任何一次运行：

```python
from kohakuterrarium import SessionReader

with SessionReader("runs/student-42.kohakutr") as r:
    for turn in r.turns():
        print(turn.user_text, "->", turn.assistant_text[:80], turn.tool_calls)
```

`.kohakutr` 文件保存对话、工具调用、事件、便笺、子代理状态、频道消息、任务、可恢复触发器和配置元数据。

见[会话](docs/zh-CN/guides/sessions.md)、[记忆](docs/zh-CN/guides/memory.md)。

## 包、默认资源与示例

生物天生就是用来打包、安装、复用和分享的。

```bash
kt install @kt-biome                              # 市场
kt install https://github.com/someone/pack.git    # git URL
kt install ./my-creatures -e                      # 本地可编辑安装
kt list
kt update --all
```

用包引用运行已安装的配置，也可以在 Python 里用：

```bash
kt run @cool-creatures/creatures/my-agent
kt terrarium run @cool-creatures/terrariums/my-team
```

```python
from kohakuterrarium import packages

packages.ensure("@kt-biome")   # 幂等；放在任何脚本开头都安全
```

可用资源：

- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome)：官方生物、terrarium 与插件包
- `examples/agent-apps/`：配置驱动的生物示例
- `examples/code/`：Python 用法示例
- `examples/terrariums/`：多 Agent 示例
- `examples/plugins/`：插件示例

见 [examples/README.md](examples/README.md)。

## 代码库地图

```text
src/kohakuterrarium/
  core/              # Agent runtime: controller, turn API, executor, events, environment
  bootstrap/         # Agent initialisation factories (LLM, tools, I/O, triggers, plugins)
  cli/               # `kt` command dispatcher
  studio/            # Management facade: catalog, identity, sessions, persistence, attach, editors
  terrarium/         # Runtime engine: creature graph, topology, channels, output wiring, hot-plug
  builtins/          # Built-in tools, sub-agents, I/O modules, TUI, user commands, CLI UI
  builtin_skills/    # Markdown skill manifests for on-demand docs
  session/           # Session persistence, SessionReader, memory search, embeddings
  serving/           # Launch/transport helpers
  api/               # FastAPI HTTP + WebSocket adapters over Studio and Terrarium
  compose/           # Composition algebra primitives
  mcp/               # MCP client manager
  modules/           # Base protocols for tools, inputs, outputs, triggers, sub-agents, user commands
  llm/               # LLM providers, profiles, API key management
  parsing/           # Tool-call parsing and stream handling
  prompt/            # Prompt aggregation, plugins, skill loading
  errors.py          # The typed exception hierarchy (KTError and friends)
  validate.py        # Pre-flight checks behind `kt doctor`
  testing/           # Test infrastructure (ScriptedLLM, TestAgentBuilder, recorders)

src/kohakuterrarium-frontend/   # Vue web frontend
examples/                       # Example creatures, terrariums, code samples, plugins
docs/                           # Tutorials, guides, concepts, reference, dev
```

每个子包都有自己的 README，描述文件构成、依赖方向和不变量。

## 文档地图

完整文档在 [`docs/`](docs/zh-CN/README.md)。

### 教程
[第一个生物](docs/zh-CN/tutorials/first-creature.md) · [第一个 Terrarium](docs/zh-CN/tutorials/first-terrarium.md) · [第一次 Python 嵌入](docs/zh-CN/tutorials/first-python-embedding.md) · [第一个自定义工具](docs/zh-CN/tutorials/first-custom-tool.md) · [第一个插件](docs/zh-CN/tutorials/first-plugin.md)

### 指南
[快速上手](docs/zh-CN/guides/getting-started.md) · [生物](docs/zh-CN/guides/creatures.md) · [Terrarium](docs/zh-CN/guides/terrariums.md) · [会话](docs/zh-CN/guides/sessions.md) · [记忆](docs/zh-CN/guides/memory.md) · [配置](docs/zh-CN/guides/configuration.md) · [编程式用法](docs/zh-CN/guides/programmatic-usage.md) · [组合](docs/zh-CN/guides/composition.md) · [自定义模块](docs/zh-CN/guides/custom-modules.md) · [插件](docs/zh-CN/guides/plugins.md) · [MCP](docs/zh-CN/guides/mcp.md) · [包](docs/zh-CN/guides/packages.md) · [服务](docs/zh-CN/guides/serving.md) · [Laboratory](docs/zh-CN/guides/laboratory.md) · [Docker 部署](docs/zh-CN/guides/deployment-docker.md) · [systemd 部署](docs/zh-CN/guides/deployment-systemd.md) · [反向代理部署](docs/zh-CN/guides/deployment-reverse-proxy.md) · [示例](docs/zh-CN/guides/examples.md)

### 概念
[术语表](docs/zh-CN/concepts/glossary.md) · [为什么是 KohakuTerrarium](docs/zh-CN/concepts/foundations/why-kohakuterrarium.md) · [什么是 Agent](docs/zh-CN/concepts/foundations/what-is-an-agent.md) · [组装一个 Agent](docs/zh-CN/concepts/foundations/composing-an-agent.md) · [模块](docs/zh-CN/concepts/modules/README.md) · [Agent 作为 Python 对象](docs/zh-CN/concepts/python-native/agent-as-python-object.md) · [组合代数](docs/zh-CN/concepts/python-native/composition-algebra.md) · [多 Agent](docs/zh-CN/concepts/multi-agent/README.md) · [模式](docs/zh-CN/concepts/patterns.md) · [边界](docs/zh-CN/concepts/boundaries.md)

### 参考
[CLI](docs/zh-CN/reference/cli.md) · [HTTP](docs/zh-CN/reference/http.md) · [Python API](docs/zh-CN/reference/python.md) · [配置](docs/zh-CN/reference/configuration.md) · [内置组件](docs/zh-CN/reference/builtins.md) · [插件钩子](docs/zh-CN/reference/plugin-hooks.md)

## 路线图

近期方向包括：更可靠的 terrarium 流程，CLI / TUI / Web 上更丰富的 UI 输出与交互模块，更多内置生物、插件与集成，以及面向长时间运行和远程使用的更好的守护进程工作流。见 [ROADMAP.md](ROADMAP.md)。

## 参与贡献

- [贡献指南](CONTRIBUTING.md)
- [开发主页](docs/zh-CN/dev/README.md)
- [测试](docs/zh-CN/dev/testing.md)
- [内部机制](docs/zh-CN/dev/internals.md)
- [前端架构](docs/zh-CN/dev/frontend.md)

## 许可证

[KohakuTerrarium License 1.0](LICENSE)：基于 Apache-2.0，附加命名与署名要求。

- 衍生作品的名称必须包含 `Kohaku` 或 `Terrarium`。
- 衍生作品必须提供可见的署名并链接回本项目。

Copyright 2024-2026 Shih-Ying Yeh (KohakuBlueLeaf) and contributors.

## 社区
- QQ 群：1097666427
- Discord：https://discord.gg/xWYrkyvJ2s
- 论坛：https://linux.do/

## FAQ

### 综合

**KohakuTerrarium 是什么？**
一个 Python 原生的 Agent 构建框架。公开的层级是：**生物 (creature)** 是 Agent 单元，**Terrarium** 是持有生物图的运行时引擎（拓扑、频道、会话，自身不跑 LLM），**Studio** 是引擎之上的管理层（目录、会话、持久化、API）。

**它和其他 Agent 框架有什么不同？**
职责始终分离：生物掌管推理和工具，引擎掌管图拓扑/频道/生命周期/会话簿记，Studio 掌管管理界面。横向团队用 Terrarium 配方和频道；Python 侧的请求流水线用组合代数。

### 安装与设置

**需要什么 Python 版本？**
Python 3.10 或更高；**推荐 3.12+**（CI 验证的就是这些版本）。通过 `pip install kohakuterrarium` 安装。

**支持哪些 LLM 提供商？**
Codex OAuth、OpenAI/OpenRouter 风格的提供商、Anthropic 原生、Google Gemini、Kimi Code、GLM Coding Plan、本地 OpenAI 兼容服务器（Ollama、vLLM），以及其他 OpenAI 兼容的云提供商。用 `kt login`、`kt config llm add` 或提供商 API key 配置。`kt doctor` 可以验证配置。

**能用本地模型吗？**
能。把 LLM 端点指向你的本地服务器（Ollama、vLLM 等），并在生物配置或 LLM profile 里设置模型名。

### 核心概念

**什么是“生物 (Creature)”？**
独立的 Agent 单元：控制器、工具、触发器、子代理、插件、记忆、I/O、提示词、私有状态。它可以单独运行，也可以作为 Terrarium 图中的一个节点。

**什么是“Terrarium”？**
托管生物图的运行时引擎。它不跑 LLM、没有推理循环，但掌管结构性决策：连通分量、频道注册表、热插拔、输出连线、会话合并/拆分簿记。

**什么是“插件 (Plugins)”？**
基于钩子的扩展，包裹框架行为：工具调用、LLM 调用和子代理运行前后的 pre/post 钩子，外加生命周期回调。沙箱、预算和权限门控都以普通插件的形式提供。

### 开发

**怎么创建自定义生物？**
写一份 YAML 配置，定义工具、提示词和行为；也可以在 Python 里用 `Agent.build` / `engine.add_creature` 构建。见[第一个生物](docs/zh-CN/tutorials/first-creature.md)。

**能把 Agent 嵌进我的 Python 应用吗？**
能，而且这是一等用法。`await agent.run(...)` 返回带类型的 `TurnResult`；`run_stream` 产出带类型的事件；引擎负责工作目录、会话和大量并发生物。见 [`examples/code/`](examples/code/) 和[编程式用法指南](docs/zh-CN/guides/programmatic-usage.md)。

**多 Agent 组合是怎么工作的？**
横向团队用 Terrarium 配方/频道/输出连线。不需要长驻图结构时，Python 侧的轻量请求流水线用 `compose`（`>>`、`&`、`|`、重试）。

### 故障排查

**生物为什么没反应？**
先跑 `kt doctor`，它会一次性检查提供商认证、profile 解析和配置有效性。再检查网络连通性和 API key 是否有效。

**怎么调试 Agent 行为？**
用 `kt run --verbose` 看详细日志。用 `kt resume` 恢复或检查之前的工作，用 `kt search` 搜索，用 `SessionReader` 重放，或在 Web/桌面 UI 里用 Studio 会话查看器。

**去哪里求助？**
- QQ 群：1097666427
- Discord：https://discord.gg/xWYrkyvJ2s
- 论坛：https://linux.do/
