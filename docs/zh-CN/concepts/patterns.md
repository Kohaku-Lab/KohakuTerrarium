---
title: 模式
summary: 把既有模块组合起来就会自然长出的做法——群聊、智能守门员、自适应监控器、输出接线。
tags:
  - concepts
  - patterns
---

# 模式

这一页上的每个模式，都不需要新增任何框架功能。
它们只是把已经存在的模块组合起来而已。这里每一种形状，你今天都能用六个模块、channels、plugins，以及 Python 原生基底做出来。

把这一页当成一份型录，或者把它当成一个证明：这些抽象之所以值得保持精简，就是因为它们真的能自然组出有用的东西。

## 1. 用 tool + trigger 做群聊

**怎么组成：** 一个 creature 有 `send_message` tool。另一个 creature 有 `ChannelTrigger`，监听同一个 channel 名称。当前者送出消息时，后者就会用 `channel_message` event 被唤醒。

**为什么能这样用：** channel 本质上就是具名队列。tool 往里写；trigger 从里读。两个模块彼此都不知道对方的存在。

**什么时候适合用：** 你想要横向多 agent 系统，但又不想引入 `terrarium.yaml` 那套机制；或者发送端是否要送出消息，本身就是条件式决策（批准 vs. 修改、保留 vs. 丢弃）。

**最小示例：**

```yaml
# creature_a
tools:
  - name: send_message

# creature_b
triggers:
  - type: channel
    options:
      channel: chat
```

## 1b. 用 output wiring 做确定性的 pipeline 边

**怎么组成：** 一个 creature 在配置中声明 `output_wiring:`，指定一个或多个目标 creature。每次 turn 结束时，框架都会往每个目标的事件队列送出一个 `creature_output` `TriggerEvent`——携带该 creature 最后一轮 assistant 产生的文字（如果 `with_content: false`，则只送 lifecycle ping）。

**为什么能这样用：** 这个接线位于框架层，不需要发送方调用 tool，也不需要接收方订阅 trigger，中间也没有 channel。目标端会通过原本就拿来处理用户输入、timer 触发、channel 消息的同一条 `agent._process_event` 路径看到这个事件。

**什么时候适合用：** 这条 pipeline 边是确定性的——也就是“每次 A 完成一轮，B 都会收到输出”。如果是 reviewer / navigator 这类角色，或 analyzer 需要依内容决定分支，还是更适合留在模式 1（channels），因为 wiring 不能条件式触发。

**最小示例：**

```yaml
# terrarium.yaml creature block
- name: coder
  base_config: "@kt-biome/creatures/swe"
  output_wiring:
    - runner                            # shorthand
    - { to: root, with_content: false } # lifecycle ping
```

**和 channels 的区别：** channels 需要 LLM 记得去送；wiring 不管 LLM 做什么都一定会触发。两种机制可以在同一个 terrarium 里自由共存——kt-biome 的 `auto_research` 就是用 wiring 来处理棘轮式边（ideator → coder → runner → analyzer），再用 channels 处理 analyzer 的保留 / 丢弃决策，以及团队聊天状态。

## 2. 用 agent-in-plugin 做智能守门员

**怎么组成：** 一个 lifecycle plugin 挂在 `pre_tool_execute`。它的实现会跑一个嵌套的小 `Agent`，审查即将执行的 tool call，并返回 `allow` / `deny` / `rewrite`。plugin 再依此返回改写后的参数，或抛出 `PluginBlockError`。

**为什么能这样用：** plugins 是 Python；agents 也是 Python。plugin 调用 agent，和调用任何 async 函数没有区别。

**什么时候适合用：** 你需要基于策略的工具守门机制，而这个判断本身并不简单——太复杂，不能靠静态规则；又太偏领域，不适合用通用解法。

## 3. 用 agent-in-plugin 做无缝记忆

**怎么组成：** 一个 `pre_llm_call` plugin 会跑一个小型 retrieval agent。retrieval agent 会搜寻 session store（或外部向量数据库）中和当前上下文相关的事件，整理命中结果，再把它们以前置 system messages 的方式插入。外层 creature 不用调用任何 tool，prompt 就会悄悄变得更丰富。

**为什么能这样用：** creature 本身不需要决定“我现在要不要检索某些东西”——plugin 会固定替它做，而 LLM 每一轮都看得到结果。

**什么时候适合用：** RAG 风格记忆对你有帮助，但你不想让主 agent 为此消耗 tool 预算。

## 4. 用 agent-in-trigger 做自适应监控器

**怎么组成：** 一个自定义 trigger，其 `fire()` 内容会定时跑一个小型 judging agent。这个 agent 检查当前世界状态，返回 `fire / don't fire`。若决定触发，就向外层 creature 送出一个 event。

**为什么能这样用：** trigger 本质上只是异步的事件产生器。这个产生器要看什么，完全由你决定，而“内嵌一个迷你 agent”就是其中一种合法选项。

**什么时候适合用：** 固定时间间隔太粗糙，固定规则太脆弱，但每个 tick 跑一次完整 LLM turn 的成本你还负担得起。

## 5. 沉默 controller + 外部 sub-agent

**怎么组成：** 某只 creature 的 controller 不产生任何对用户可见的文字——只做 tool calls，最后派发一个 sub-agent。这个 sub-agent 配置为 `output_to: external`，因此真正串流给用户看的是 *它* 的文字，而父层自己保持隐形。

**为什么能这样用：** output routing 会把 sub-agent 的串流和 controller 本身的串流视为平行地位。你可以决定要让用户看到哪一条。

**什么时候适合用：** 你希望用户面前呈现的是某个专家角色的声音（人格、格式、限制条件），而 orchestration 则留在幕后。kt-biome 很多聊天 creature 都用了这种做法。

## 6. Tool-as-state-bus

**怎么组成：** 在同一个 terrarium 内合作的两只 creature，都把共享 environment 里像 scratchpad 一样的 channels 当作汇合点：一方写入 `tasks_done: 3` 这种记录；另一方轮询它。或者，它们用共享 session key 搭配 `scratchpad` tool。

**为什么能这样用：** sessions 和 environments 本来就有 KV 存储。tools 只是把它们暴露给 LLM 使用。

**什么时候适合用：** 你需要粗粒度的协调，但不想为此设计一整套消息传递协议。

## 7. 混合轴多 agent 系统

**怎么组成：** 一个 terrarium，它的 root（或其中的 creatures）本身又在内部使用 sub-agents。顶层是横向；每一个 creature 内部则是纵向。

**为什么能这样用：** sub-agents 和 terrariums 是正交的。框架里没有任何地方禁止你两者一起用。

**什么时候适合用：** 团队本身有角色分工，而某些角色内部又适合进一步拆解（规划 → 实现 → 审查），但你不需要把那层拆解显示成独立 creature。

## 8. 用 framework commands 做 inline control

**怎么组成：** 在同一轮内，controller 可以送出一些直接跟框架对话的小型 inline 指令：`info` 可按需加载某个 tool 的完整文档，`read_job` 可读取执行中后台工具的部分输出，`jobs` 可列出待处理工作，`wait` 可等待某个 stateful sub-agent。这些都是 inline 执行——不需要新的 LLM round-trip。

语法取决于 creature 配置的 `tool_format`；在默认的 bracket 形式下，一条 command 调用会写成 `[/info]tool_name[info/]`。

**为什么能这样用：** framework commands 是 parser 层级的 affordance，不是 tools，所以调用它们本身几乎没有成本。

**什么时候适合用：** 你希望 LLM 在同一轮中检查自己的状态，而不必为此消耗一个 tool slot。

## 不是封闭清单

这一页的重点不是这些模式本身，而是：小而可组合的模块，会自然产出有用的形状，你不需要把它们硬编码进框架里。如果这里某个模式和你的需求很接近，相关调整多半仍然可以落在同一组 building blocks 之内。如果你发明了新的模式，欢迎对这个文件开 PR。

## 延伸阅读

- [Agent 作为 Python 对象](python-native/agent-as-python-object.md)
  —— 让第 2–4 种模式成立的关键性质。
- [Tool](modules/tool.md)、[Trigger](modules/trigger.md)、
  [Channel](modules/channel.md)、[Plugin](modules/plugin.md) ——
  这些模式所组合的基本元件。
- [边界](boundaries.md) —— 抽象是预设值，不是法律；有些模式就是刻意跨越预设边界。
