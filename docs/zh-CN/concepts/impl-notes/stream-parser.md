---
title: 串流剖析器
summary: 以状态机将 LLM 输出解析为文字、工具调用、子 Agent派送与 framework commands。
tags:
  - concepts
  - impl-notes
  - parser
---

# 串流剖析器

## 这要解决的问题

当 LLM 在串流中途输出一个工具调用时，框架应该在什么时候开始执行它？

有两种选择：

1. **等到轮次结束**。 收集所有工具调用；一次批次派送；取得结果；
  然后可能再进行一次 LLM 调用。
2.**区块一关闭就立刻派送**。 每个工具都会与 LLM 其余输出并行执行；
  到 LLM 讲完时，有些工具可能已经完成。

方案 2 的响应速度显着更好——尤其是在长串流轮次且包含多个工具调用时——
而这正是框架采用的做法。

## 曾考虑的方案

- **轮次后派送**。 较简单，但浪费了串流视窗；工具只能排在 LLM 后面。
- **推测式派送**。 在 LLM 串流时就开始跑工具；如果后来发现区块不完整
  再取消。错误风险太高。
- **在区块关闭时，以确定性的状态机派送**。 这就是我们实际的做法。
  仅在文字区块完成解析时启动工具；绝不对部分输入执行。

## 我们实际怎么做

LLM 输出的串流会逐块送入 parser 状态机。Parser 会依照目前配置的
`tool_format`，追踪三种嵌套区块：

- **工具调用**— 例如在 bracket（预设）格式中是
  `[/bash]@@command=ls\n[bash/]`；在 XML 中是 `<bash command="ls"></bash>`；
  在 native 中则是 LLM provider 自己的 function-calling envelope。
- **子 Agent派送**— 使用相同的格式家族，只是改用 Agent tag。
- **Framework commands**— `info`、`jobs`、`wait`
  （以及在 parser 的 DEFAULT_COMMANDS 集合中的 `read_job`）。
  这些和工具调用共享相同的 bracket/XML 框架。关于格式如何配置，
  请参阅 [modules/tool — formats](../modules/tool.md) 与
  [modules/plugin](../modules/plugin.md)。

当一个区块关闭时，parser 会在其输出 generator 上发出事件。
控制器接着做出反应：

- `TextEvent` → 串流到输出。
- `ToolCallEvent` → `Executor.submit_from_event(event, is_direct=True)`
  → `asyncio.create_task(tool.execute(...))`。立即返回。
- `SubAgentCallEvent` → 类似处理，但走 `SubAgentManager.spawn`。
- `CommandEvent` → 直接就地执行（读取 job 输出、加载文件等）；
  这些操作很快且具确定性。

在串流结束时，控制器会等待所有在串流期间启动的 `direct` jobs，
将其结果收集为 `tool_complete` 事件，并在下一轮回馈给 LLM。

## 维持不变的条件

- **每个已关闭区块只派送一次**。 部分区块绝不执行。
- **同一轮中的多个工具会并行执行**。 对它们的 tasks 做 `gather`，
  而不是依序执行。
- **LLM 串流不会被工具执行阻塞**。 LLM 持续输出；工具在旁并行执行。
- **背景工具不会让轮次维持开启**。 被标记为 background 的工具，
  会先以 job id 作为占位结果返回；控制器继续前进；真正结果会在之后
  以事件形式送达。

## 代码中的位置

- `src/kohakuterrarium/parsing/` — parser 状态机；每种 tool-format
  变体（bracket、XML、native）各有一个模块。
- `src/kohakuterrarium/core/controller.py` — 消费 parser 事件。
- `src/kohakuterrarium/core/executor.py` — 把工具执行包成 tasks。
- `src/kohakuterrarium/core/Agent_tools.py` — submit-from-event 路径，
  将 parser 输出接到 executor。

## 另请参阅

- [Composing an Agent](../foundations/composing-an-agent.md) — 从轮次层级
  理解本页所放大的流程。
- [Tool](../modules/tool.md) — 执行模式（direct / background /
  stateful）。
