---
title: 工具 (Tool)
summary: LLM 可调用的具名能力——shell 命令、文件编辑、网页搜寻等等。
tags:
  - concepts
  - module
  - tool
---

# 工具

## 它是什么

**工具 (tool)** 是 Agent *做事* 的方式。它是一种向控制器注册的可执行能力，LLM 可以用名称加上参数来调用它。

在多数人的心智模型里，工具就是「LLM 可以调用的函数」：`bash`、`read`、`write`、`grep`、`web_search`。这样说没错，但还不完整。工具也可以是通往另一个 Agent 的消息总线、状态机控制柄、嵌套Creature、权限闸门，或同时兼具这些身分。

## 为什么它存在

聊天机器人只有嘴。工具让 Agent 长出手。没有工具时，LLM 只能说话；有了工具，它就能在世界里做各种工作。

这个框架的工作，是让工具执行变成 **容易使用，也容易撰写**：感知串流的派发、平行执行、上下文传播、背景作业，以及型别化中继数据。每个既有 Agent 产品几乎都会重做其中某个子集；把它一次做好放进底层，就不用一直重复造轮子。

## 我们怎么定义它

一个工具会实现：

- 一个 **名称** 与简短描述（自动插入 system prompt）
- 一份 **args schema**（`parameters`），相容于 JSON Schema
- 一个非同步 **`execute(args, context)` → `ToolResult`**- 一种 ** 执行模式**：`direct`（预设）、`background` 或 `stateful`
- 可选的 **完整文件**（`get_full_documentation()`），透过 `info` 框架命令按需加载

执行模式：

- **Direct**—— 在同一轮中等待工具完成；结果会作为 `tool_complete` 事件回馈。
- **Background**—— 提交后立即返回；结果会在之后的事件中送达。
- **Stateful**—— 跨多轮互动；像 generator 一样的工具，可产出中间结果供 Agent 响应。

## 我们怎么实现它

工具会注册到 `Registry`（`core/registry.py`）。控制器的 stream parser 会在工具区块结束时侦测到它，并立刻调用 `Executor.submit_from_event(...)`。executor 会建立 `asyncio.Task`；多个工具可平行执行。

每次工具执行都会收到一个 `ToolContext`，其中带有：

- Creature的工作目录；
- 会话（草稿区、私有频道）；
- 环境（共享频道，如果有的话）；
- 文件防护（先读后写、路径安全）；
- 文件读取状态（用于去重）；
- Agent 名称；
- job store（让 `wait` / `read_job` 框架命令能找到这个工具的作业）。

内建工具包含 shell（`bash`）、Python（`python`）、文件操作（`read`、`write`、`edit`、`multi_edit`）、搜寻（`glob`、`grep`、`tree`）、JSON（`json_read`、`json_write`）、Web（`web_fetch`、`web_search`）、通讯（`send_message`）、记忆（`scratchpad`、`search_memory`）、内省（`info`、`stop_task`），以及Terrarium管理（`terrarium_create`、`creature_start`、…）。

## 因此你可以做什么

- **把工具当成消息总线**。 `send_message` 会写入某个频道；另一个 Creature上的 `ChannelTrigger` 会读取它。两个工具加上一个 trigger，就能重现群聊模式，而不需要新增任何原语。
- **把工具当成状态控制柄**。 `scratchpad` 工具就是典型的 KV API；任何协作中的工具都可以透过它会合。
- **会安装 trigger 的工具**。 任何通用 trigger 类别（预设为 `TimerTrigger`、`ChannelTrigger`、`SchedulerTrigger`）都能以工具形式暴露——在 `tools:` 下列出 `type: trigger`，就会让 `add_timer` / `watch_channel` / `add_schedule` 出现在工具清单中，而调用它就会把该 trigger 安装到活跃的 `TriggerManager` 上。`terrarium_create` 更是会直接启动一整个嵌套系统。
- **包装子 Agent的工具**。 任何子 Agent调用本身就是工具形状，因为 LLM 仍然是用名称加参数去调用它。
- **会执行 Agent 的工具**。 因为工具就是普通 Python，某个工具可以内含一个 Agent——例如先用一个小型判断 Agent 检查参数，再派发真正动作的 guard 工具。参见 [patterns](../patterns.md)。

## 不要被它框住

工具不必是「纯函数」。它们可以改变状态、启动长时间工作、和其他Creature协调，或编排整个Terrarium。它们也不必很直观：一个唯一效果只是把会话标记成「准备好压缩了」的工具，也完全合理。抽象的重点只是「LLM 可以调用的某个东西」；至于调用背后发生什么，框架不会替你设限。

## 另见

- [impl-notes/stream-parser](../impl-notes/stream-parser.md) —— 为什么工具会在 LLM 停止前就开始执行。
- [子 Agent](sub-agent.md) —— 那个「它也是一种工具」的兄弟概念。
- [频道](channel.md) —— 把工具当消息总线的另一半。
- [模式](../patterns.md) —— 工具的各种非常规用法。
- [reference/builtins.md — Tools 参考](../../reference/builtins.md) —— 完整目录。
