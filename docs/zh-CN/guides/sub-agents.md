---
title: 子代理
summary: 配置内置与内联子代理、运行时预算插件和自动压缩。
tags:
  - guides
  - sub-agent
  - budget
---

# 子代理

子代理是在同一个 Creature 内部进行纵向委派的方式：父控制器像调用工具一样调用一个专家，而这个专家拥有自己的对话、提示词、允许使用的工具、插件和预算。

当你希望父控制器保持轻量、专注于编排，而由专家负责探索、规划、修改、审查、研究、总结或生成最终用户回应时，就使用子代理。

## 内置子代理

框架内置以下配置：

- `explore`
- `plan`
- `worker`
- `critic`
- `research`
- `coordinator`
- `memory_read`
- `memory_write`
- `summarize`
- `response`

按名称引用：

```yaml
subagents:
  - explore
  - plan
  - worker
```

上面的字符串简写等价于：

```yaml
subagents:
  - name: explore
    type: builtin
```

内置子代理已经启用自动压缩和统一运行时预算插件：

```yaml
default_plugins: ["auto-compact"]
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      # 无 walltime_budget
```

每个预算轴的列表/元组形状是 `[soft, hard]`。软限制会在子代理下一轮 LLM 调用前注入提醒；硬限制会阻止继续派发工具/子代理，让专家用文字收尾，而不是继续消耗执行预算。

## 覆盖内置预算

对内置子代理，通过条目的 `options` 覆盖 `plugins`：

```yaml
subagents:
  - name: worker
    type: builtin
    options:
      plugins:
        - name: budget
          options:
            turn_budget: [60, 90]
            tool_call_budget: [120, 180]
```

只有在确实需要按墙钟时间截断时才设置 `walltime_budget`：

```yaml
subagents:
  - name: research
    type: builtin
    options:
      plugins:
        - name: budget
          options:
            turn_budget: [80, 120]
            tool_call_budget: [150, 220]
            walltime_budget: [300, 600]
```

多数长任务更适合使用 turn/tool 预算，而不是 walltime，因为模型和服务商延迟差异很大。

## 纯 YAML 内联子代理

很多子代理不需要 Python 模块。使用不带 `module`/`config` 的 `type: custom`，并把 `SubAgentConfig` 字段直接写在 YAML 中：

```yaml
subagents:
  - name: dependency_mapper
    type: custom
    description: Map dependency edges without editing files
    system_prompt: |
      You map imports and runtime dependencies. Return a compact graph and
      cite files as path:line when possible.
    tools: [glob, grep, read, tree]
    can_modify: false
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [40, 60]
          tool_call_budget: [75, 100]
```

内联配置支持与 `SubAgentConfig` 相同的字段，包括：

- `system_prompt`, `prompt_file`, `extra_prompt`, `extra_prompt_file`
- `tools`, `can_modify`, `interactive`, `output_to`, `output_module`
- `default_plugins`, `plugins`
- `compact`
- `model`, `temperature`
- `budget_inherit`, `budget_allocation`

运行时预算轴（`turn_budget`、`tool_call_budget`，以及可选的 `walltime_budget`）写在 `budget` 插件的 `options` 中；它们不是核心 `SubAgentConfig` 字段。

只有当你想在包之间共享配置对象、以程序方式构造提示词，或替换运行时行为时，才需要 Python 模块。

## 运行时插件与插件包

预算执行是基于插件的。内置运行时表面如下：

| 名称 | 类型 | 何时使用 |
|---|---|---|
| `budget` | 插件 | 需要 turn/tool/walltime 预算计数与执行；预算轴写在 `options` 下。 |
| `compact.auto` | 插件 | 已配置 `compact`，并希望在 LLM 轮次后自动检查压缩。 |
| `auto-compact` | 插件包 | 想通过 `default_plugins` 启用 `compact.auto`。 |

自定义或内联子代理需要显式加入预算与自动压缩：

```yaml
subagents:
  - name: reviewer
    type: custom
    system_prompt: "Review the proposed change."
    tools: [read, grep]
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [40, 60]
          tool_call_budget: [75, 100]
```

同名的用户声明插件会覆盖默认插件，因此子代理可以替换成更大或更小的预算配置。

## 父代理预算与子代理预算

`budget` 插件中的 `turn_budget` 和 `tool_call_budget` 选项是该子代理运行的独立多轴预算。

旧的父级共享迭代预算仍然存在：

```yaml
max_iterations: 100
subagents:
  - name: explore
    type: builtin
    options:
      budget_inherit: true      # 默认：存在父级迭代预算时共享
  - name: critic
    type: builtin
    options:
      budget_allocation: 10     # 单独的旧式 10-turn 切片
```

新配置优先使用显式的 `plugins: [{name: budget, options: ...}]` 加 `default_plugins: ["auto-compact"]`。只有在你想让父代理和子代理共享同一个全局上限时，才使用 `max_iterations`。

## 子代理自动压缩

子代理也可以有自己的压缩配置：

```yaml
subagents:
  - name: long_research
    type: custom
    system_prompt: "Research deeply, then summarize."
    tools: [web_search, web_fetch, read]
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [80, 120]
          tool_call_budget: [150, 220]
    compact:
      max_tokens: 120000
      threshold: 0.75
      target: 0.40
      keep_recent_turns: 4
```

`compact.auto` 插件会在 LLM 轮次后检查用量，并在达到阈值时触发压缩。没有 `compact.auto`（或 `auto-compact` 插件包）时，单独的 `compact:` 只会配置管理器，不会自动触发。

## 快速检查表

对于每个需要实际工作的非内置子代理：

1. 只给它必要工具。
2. 如果它有 `compact:` 配置，加上 `default_plugins: ["auto-compact"]`。
3. 加上 `budget` 插件，并至少设置 `turn_budget: [40, 60]` 和 `tool_call_budget: [75, 100]`。
4. 除非确实需要墙钟截断，否则避免 `walltime_budget`。
5. 提示词保持专家化，并要求返回紧凑的结构化结果。
