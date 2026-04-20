---
title: 第一个 Creature
summary: 编写 Creature 配置、在 CLI / TUI / Web 中运行，并自定义提示词与工具。
tags:
  - tutorials
  - creature
  - getting-started
---

# 第一个 Creature

**问题** ： 你已经安装好 KohakuTerrarium，现在想从零开始做出一个可自定义、可运行，而且自己也能理解的 Creature。

**完成状态** ： 你已经运行过一个现成的 Creature、恢复过工作会话、把 Creature fork 到自己的文件夹、修改了 system prompt、添加了一个工具，然后再次运行它。

**先决条件** ： `PATH` 中已有 `kt`（可通过仓库执行 `uv pip install -e .` 安装，或安装已发布版本），并且机器可以访问相应 API。

Creature 是一个独立 Agent，包含 controller、input、output、tools，以及可选的 triggers、sub-agents、plugins。本教程会用最短路径带你接触这些核心组件。

## 第 1 步：安装默认套件

目标：把随附的 Creatures（swe、general、reviewer、root 等）安装到本机，这样就可以通过 `@kt-biome/...` 引用它们。

```bash
kt install https://github.com/Kohaku-Lab/kt-biome.git
```

`kt install` 可以接受 git URL 或本地路径。安装完成后，套件会位于 `~/.kohakuterrarium/packages/kt-biome/`，其他配置都可以通过 `@kt-biome/...` 引用它。

验证：

```bash
kt list
```

你应该能看到 `kt-biome` 以及其中包含的 Creatures（`swe`、`general`、`reviewer`、`root`、`researcher`、`ops`、`creative`）。

## 第 2 步：验证 LLM

目标：选择一个 provider 并完成登录。SWE Creature 使用默认模型，因此你需要配置对应凭证。

如果你有 ChatGPT 订阅并希望使用 OAuth：

```bash
kt login codex
```

否则，请为其他后端（OpenAI、Anthropic、OpenRouter 等）设置密钥：

```bash
kt config key set openai
```

你也可以设置默认模型 preset，这样每次运行命令时就不用再带 `--llm`：

```bash
kt model list
kt model default gpt-5.4
```

## 第 3 步：运行现成的 Creature

目标：在修改任何内容之前，先看看一个完整的 Creature 是如何工作的。

```bash
kt run @kt-biome/creatures/swe --mode cli
```

问它一个简单问题：

```text
> 列出这个目录中的 python 文件
```

你应该会看到它流式输出答案、调用工具（`glob`、`read`）并展示结果。使用 `/exit` 或 Ctrl+C 退出。退出时，`kt` 会打印类似 `kt resume <session-name>` 的恢复提示；工作会话会自动保存到 `~/.kohakuterrarium/sessions/*.kohakutr`。

## 第 4 步：恢复工作会话

目标：确认工作会话可以持久保存并恢复。

```bash
kt resume --last
```

这会恢复最近一次工作会话。你会回到同一段对话，并保留相同的草稿区、工具历史和模型。确认后再退出即可。

## 第 5 步：把 Creature fork 到本地文件夹

目标：得到一个属于你自己的 Creature，并在 SWE 的基础上叠加修改。

```bash
mkdir -p creatures/my-swe/prompts
```

`creatures/my-swe/config.yaml`：

```yaml
name: my_swe
version: "1.0"
base_config: "@kt-biome/creatures/swe"

system_prompt_file: prompts/system.md
```

`creatures/my-swe/prompts/system.md`：

```markdown
# My SWE

You are a careful repo-surgery agent.

House rules:
- read before editing, always
- keep diffs small and obvious
- when unsure, ask rather than guess
```

`base_config` 会继承 SWE Creature 中的全部内容，包括 LLM 默认配置、工具集、sub-agents，以及上游 system prompt。你的 `system.md` 会追加到继承来的 prompt 后面，形成完整的继承链。其他未设置的部分会继续沿用继承值。

## 第 6 步：添加一个工具

目标：在继承的工具列表上额外添加一个项目。Web 搜索就是很实用的选择。

编辑 `creatures/my-swe/config.yaml`：

```yaml
name: my_swe
version: "1.0"
base_config: "@kt-biome/creatures/swe"

system_prompt_file: prompts/system.md

tools:
  - { name: web_search, type: builtin }
```

像 `tools:` 和 `subagents:` 这样的列表，除非你通过 `no_inherit:` 明确指定不继承，否则都会在继承列表基础上继续扩展，并按 `name` 去重。因此，这里会把 `web_search` 添加到 SWE 的工具集中，而不需要重新声明其他条目。

## 第 7 步：运行你的 Creature

```bash
kt run creatures/my-swe --mode cli
```

问它一个需要联网的问题：

```text
> 搜索网络上的 "kohakuterrarium github"，并摘要第一个结果
```

你应该会看到 system prompt 中的 house rules 生效，同时新的 `web_search` 工具也可用。正常结束即可；工作会话会自动保存。

## 你学到了什么

- Creature 是 **一个带配置的文件夹**，而不只是一个 prompt。
- `kt install` + `kt login` + `kt run` 构成了完整的开箱即用流程。
- `kt resume` 会从磁盘恢复完整工作会话。
- `base_config: "@pkg/creatures/<name>"` 会继承全部内容；标量字段会被覆盖，`tools:` / `subagents:` 列表则会扩展。
- `system_prompt_file` 会沿着继承链依次拼接。

## 接下来读什么

- [Creatures 指南](../guides/creatures.md) —— 帮助你在上下文中理解各个配置字段。
- [配置参考](../guides/configuration.md) —— 精确说明 schema 与继承规则。
- [第一个自定义工具](first-custom-tool.md) —— 当 `builtin` 不够用时该怎么做。
- [什么是 Agent](../concepts/foundations/what-is-an-agent.md) —— 帮助你理解这种配置形态背后的心智模型。
