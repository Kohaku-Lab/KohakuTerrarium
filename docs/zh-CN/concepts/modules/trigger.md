---
title: 触发器 (Trigger)
summary: 任何在没有明确用户输入时唤醒控制器的东西——计时器、闲置、频道、webhook、监控器。
tags:
  - concepts
  - module
  - trigger
---

# 触发器

## 它是什么

**触发器 (trigger)** 是任何在没有明确用户输入时唤醒控制器的东西。计时器、闲置侦测器、webhook 接收器、频道监听器，以及监控条件，全部都属于 trigger。每个 trigger 都会作为背景作业执行，并在触发条件成立时把 `TriggerEvent` 推进事件队列。

## 为什么它存在

纯粹由输入驱动的 Agent，只有在用户出现时才能工作。但真实的 Agent 需要：

- 在没人盯着时执行 `/loop` 风格的周期性计画；
- 响应另一个 Creature送来的频道消息；
- 在最后一个事件发生后 N 秒醒来做摘要；
- 接收来自外部服务的 webhook；
- 轮询某个资源，并在条件翻转时触发。

你可以把这些各自用临时代码硬接上去。这个框架的看法是：它们其实全都是同一种东西——事件来源——所以值得共享同一个抽象。

## 我们怎么定义它

一个 trigger 会实现：

- 一个会产出 `TriggerEvent` 的非同步 generator `fire()`；
- `to_resume_dict()` / `from_resume_dict()`，让 trigger 能跨会话保存与恢复；
- 一个 `trigger_id` 供定址使用（让工具可以列出 / 取消它）。

trigger manager 会为每个已注册 trigger 启动一个背景作业。每个作业都会反覆迭代 `fire()` 并推送事件。

## 我们怎么实现它

内建 trigger 类型：

- **`timer`**—— 每 N 秒触发一次，或依 cron 排程触发。
- **`idle`**—— 若 N 秒内没有任何事件就触发。
- **`channel`**—— 监听具名频道；收到消息时触发。
- **`webhook` / `http`**—— 接收 POST 请求。
- **`monitor`** —— 当某个对 scratchpad / context 的 predicate 返回 true 时触发。

接收端常见的 `TriggerEvent` 类型有：`user_input`（来自 input 模块）、`timer`、`channel_message`（来自 channel trigger）、`tool_complete`、`sub-agent_output`、`creature_output`（另一个 Creature透过 `output_wiring` 在回合结束时送出的输出——这是框架自动发出的，不是由模块触发），以及 `error`。

`TriggerManager`（`core/trigger_manager.py`）拥有这些执行中的作业，会把完成结果接回 Agent 的事件 callback，并把 trigger 状态持久化到会话储存中，让 `kt resume` 可以重新建立它们。

配置期的 trigger 宣告在 `config.triggers[]`。运行时 trigger 也可以由 Agent 自己安装——每个通用 trigger 类别（`universal = True` + `setup_*` metadata）都会被包成它自己的工具（`add_timer`、`watch_channel`、`add_schedule`），Creature可在 `tools: [{ name: add_timer, type: trigger }]` 下列出它——也能透过 `Agent.add_trigger(...)` 以编程方式安装。

## 因此你可以做什么

- **周期性 Agent**。 每小时触发一次的 `timer`，可以让某只Creature定期重新整理它对文件系统或某组指标的观察。
- **跨Creature连线**。 `channel` trigger 是让以频道为基础的Terrarium通讯成立的机制。对于确定性的 pipeline 边，框架也会在Creature宣告 `output_wiring` 时，于回合结束发出 `creature_output` 事件——见 [Terrarium](../multi-agent/terrarium.md)。
- **由闲置驱动的摘要**。 一个在安静两分钟后触发的 `idle` trigger，可以派遣 `summarize` 子 Agent，并把结果送到某个日志频道。
- **外部讯号**。 `webhook` trigger 可以把Creature变成 CI hook、部署事件或上游产品流量的接收者。
- **自适应监控器**。 某个自订 trigger 的 `fire()` 若内部跑一只小型嵌套 Agent，就能依据判断而不是固定规则来决定*何时*唤醒外层Creature。参见 [patterns](../patterns.md)。

## 不要被它框住

一个 Creature可以没有任何 trigger。也可以只有 trigger（没有 input）。框架不替这些配置排高低，只是全部都支持。而且因为 trigger 本身就是一个 Python 对象，你完全可以把一只 Agent 塞进去——做出一个会*思考*是否该触发，而不是照手写规则执行的 watcher。这种模式让「具 Agent 特性的环境式行为」变得很便宜。

## 另见

- [输入](input.md) —— 用户内容这个特殊案例的 trigger。
- [频道](channel.md) —— 支撑多 Agent通讯的那种 trigger。
- [reference/builtins.md — Triggers 参考](../../reference/builtins.md) —— 完整清单。
- [patterns.md —— adaptive watcher](../patterns.md) —— Agent-inside-trigger。
