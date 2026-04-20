---
title: 快速开始
summary: 安装 KohakuTerrarium、安装 kt-biome 展示包，并在几分钟内启动一个可用的 agent。
tags:
 - guides
 - install
 - getting-started
---

# 快速开始

给从未执行过 KohakuTerrarium、但想在几分钟内于自己电脑上启动一个可用 agent 的读者。

KohakuTerrarium 提供核心框架，以及可复用的 Creature / 插件包安装方式。官方包 `kt-biome` 提供可直接使用的 SWE agent、reviewer、researcher，以及几个Terrarium。你无需编写任何内容即可开始试用。

概念预习：[什么是Creature](../concepts/foundations/what-is-an-agent.md)、[为什么是这个框架](../concepts/foundations/why-kohakuterrarium.md)。

## 1. 安装

### 从 PyPI 安装（建议）

```bash
pip install kohakuterrarium
# 或安装更多选用相依（语音、embeddings 等）
pip install "kohakuterrarium[full]"
```

这会提供 `kt` 指令。请确认：

```bash
kt --version
```

### 从原始码安装（用于开发）

```bash
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"
```

如果你想让 `kt web` 或 `kt app` 提供前端，请先建置一次：

```bash
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

若未执行建置步骤，`kt web` 只会打印提示消息，而 `kt app` 会无法开启。

## 2. 安装默认 Creature 包

`kt-biome` 内含开箱即用（OOTB）的Creature（`swe`、`reviewer`、`researcher`、`ops`、`creative`、`general`、`root`）与几个Terrarium。

```bash
kt install https://github.com/Kohaku-Lab/kt-biome.git
kt list
```

已安装的包会存放在 `~/.kohakuterrarium/packages/<name>/`，并以 `@<package>/path` 语法引用。

## 3. 验证模型提供者

选一种：

 **Codex（ChatGPT 订阅，无需 API key）** 
```bash
kt login codex
kt model default gpt-5.4
```

会开启浏览器视窗；完成 device-code 流程后，token 会写入 `~/.kohakuterrarium/codex-auth.json`。

 **兼容 OpenAI 的提供者（API key）** 
```bash
kt config key set openai          # 会提示你输入 key
kt config llm add                 # 交互式默认建立器
kt model default <preset-name>
```

 **其他提供者**：`anthropic`、`openrouter`、`gemini` 等都是内置后端。详情请见 `kt config provider list` 与 [配置](configuration.md)。

## 4. 执行一只 Creature

```bash
kt run @kt-biome/creatures/swe --mode cli
```

你会进入 SWE agent 的交互式提示环境。输入一个请求后，它会在目前工作目录中使用 shell、文件与编辑工具。Ctrl+C 可干净结束，并打印恢复提示。

模式：

- `cli` — Rich 行内介面（TTY 时默认）
- `tui` — 全萤幕 Textual 应用程式
- `plain` — 纯 stdout/stdin，适合 pipe 或 CI

覆盖单次执行的模型：

```bash
kt run @kt-biome/creatures/swe --llm claude-opus-4.6
```

## 5. 恢复

会话会自动储存到 `~/.kohakuterrarium/sessions/*.kohakutr`（除非你传入 `--no-session`）。重新启动任何过去的会话：

```bash
kt resume --last                # 最近一次
kt resume                       # 交互式选择器
kt resume swe_20240101_1234     # 依名称前缀
```

agent 会根据储存的设置重建、回放对话、重新注册可恢复的触发器，并还原 scratchpad 与频道历史。完整持久化模型请见[会话](sessions.md)。

## 6. 搜索会话历史（提示）

因为会话是以可操作的形式储存，所以你可以像查询小型本地知识库一样搜索它们：

```bash
kt embedding ~/.kohakuterrarium/sessions/<name>.kohakutr
kt search <name> "auth bug"
```

完整教学请见[记忆指南](memory.md)。

## 7. 开启 Web UI 或桌面应用程式

```bash
kt web           # 本地 Web 服务器，位于 http://127.0.0.1:8001
kt app           # 原生桌面视窗（需要 pywebview）
```

若你需要比终端机生命周期更长的常驻行程：

```bash
kt serve start
kt serve status
kt serve logs --follow
kt serve stop
```

何时该用哪一种介面，请见 [服务部署指南](serving.md)。

## 疑难排解

- **`kt login codex` 没有开启浏览器**。 复制 CLI 打印的 URL，手动贴到浏览器中。如果 callback port 被占用，请先释放再重试。
- **`kt web` 没有提供内容／`/` 回传 404**。 前端尚未建置。执行 `npm install --prefix src/kohakuterrarium-frontend && npm run build --prefix src/kohakuterrarium-frontend`。从 PyPI 安装时通常已内置建置好的资产。
- **写入 `~/.kohakuterrarium/` 时出现 `Permission denied`**。 框架会在首次执行时建立该目录。若它已存在但属于其他用户（常见于 `sudo pip install` 之后），请修正权限：`chown -R $USER ~/.kohakuterrarium`。
- **`kt run` 显示 "no model set"**。 你跳过了第 3 步。请执行 `kt model default <name>` 或传入 `--llm <name>`。
- **`ModuleNotFoundError: pywebview`**。 `kt app` 需要桌面额外依赖：`pip install 'kohakuterrarium[full]'`（或改用 `kt web`）。

## 另请参见

- [Creatures 指南](creatures.md)：了解如何继承或自定义 OOTB agent。
- [会话](sessions.md)：了解恢复语意与压缩。
- [服务部署指南](serving.md)：决定该用 `kt web`、`kt app` 还是 `kt serve`。
- [参考 / CLI](../reference/cli.md)：查看所有指令与旗标。
