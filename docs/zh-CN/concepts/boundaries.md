---
title: 边界
summary: Creature 抽象是预设值，不是紧身衣——框架会在哪里弯曲自己的抽象，以及什么时候它根本不适合。
tags:
  - concepts
  - philosophy
---

# 边界

在 KohakuTerrarium 里，Creature 抽象是 Agent 的预设形状。
它 **不是** 法律。这一页整理的是：什么情况下忽略这个预设反而是正确做法——以及什么情况下，这个框架根本不是好选择。

## 抽象是预设值，不是紧身衣

六个模块（Controller、Input、Trigger、Tool、Sub-Agent、Output）在大多数Creature里都会一起出现，但每一个其实都可以独立省略：

- **没有 input**。 `input: { type: none }`。cron Creature、只收 webhook 的接收器、背景监控器——都不需要用户打字。
- **没有 triggers**。 纯 request/response 的聊天Creature，不靠任何环境唤醒也完全没问题。
- **没有 tools**。 只负责响应的专家型Creature（摘要、格式整理、翻译）可以完全不带工具。LLM 本身就已经很有能力。
- **没有 sub-agents**。 从不委派的短任务Creature很常见。
- **没有 output**。 只做 side effect 的 Creature 也存在。若一个 Creature唯一的工作是写入外部数据库，它就不需要 sink。
- **没有 memory / compaction / session**。 `--no-session` 和 `compact.enabled: false` 就能覆盖这类情况。

框架并不偏袒「六模块齐全」这种形状。它只是让你在想用这种形状时，成本很低。

## 框架会弯曲自己的抽象

这不是抽象泄漏，而是设计重点。**例子：channels**。 channel 并不在聊天机器人 → Agent 的那条推导路径里。它是为了多 Agent 系统引入的通讯基底，而最简单的实现就是「某个 tool 写入消息；某个 trigger 在消息到达时触发」。这等于为了一个概念，把两个模块混在一起用。但这也是最自然的做法；如果硬要假装不是这样，只会多发明一个没有实质收益的新 primitive。** 例子：root Agent**。 root 本质上就是「一只带有特定工具集与特定监听接线方式的 Creature」。在结构上，它和其他Creature没有差别；但在概念上，它的位置很重要。我们把它点名成一个独立角色，是因为这个区分很有用，不是因为框架强制如此。

框架里的抽象，是帮助思考的工具，不是墙。

## 什么时候 KohakuTerrarium 适合你

- 你的 Agent 系统需求 **还不稳定，或持续在演变**。你还不知道哪些工具、触发器、提示词能活过下一轮。当你在做的东西还会变形，框架就会开始体现价值。
- 你想 **尝试新的 Agent 设计**——某种新颖的工具、触发器或 sub-agent 组合——而不想重写整个基底。
- 你想要 **开箱即用且可定制的 Creature**。`kt-biome` 会给你起点；继承它、换掉几个模块，就完成了。
- 你想把 **Agent 行为嵌入现有 Python 代码** 里，而不是另外跑一个独立服务。
- 你想要一个 **可分享可重用元件的框架**（例如：内含 creatures、plugins、tools、presets 的套件），在团队之间或跨专案重复使用。

## 什么时候它不适合

- 你 **已经很满意某个现成 Agent 产品，** 而且你的需求也 ** 很稳定**。如果 Claude Code、OpenClaw，或某个现成内部工具已经能满足你的需求，而且你也不预期需求会变，切过来只会增加成本，不会带来回报。
- 你的 **心智模型与框架不匹配**。如果你对 Agent 的理解无法自然映射到 controller / tools / triggers / sub-agents / channels，上硬套只会让事情更糟，不会更好。这时候就用别的东西——或者自己写另一个框架。
- 你的工作负载需要 **超低延迟**——你真的在意每个操作都低于 50 ms。KohakuTerrarium 优先优化的是正确性与灵活性；asyncio 开销、事件队列、output router、session persistence 都会带来一些成本。大多数时候没问题；有时候就是不行。
- 你就是 **不想用它**。这是完全合理的理由。若维护者对某个框架心生反感，那它就不该待在那个 codebase 里。

## 把这一页当成一种授权

概念文件一开始问的是「这是什么？」最后要问的是「这适合我吗？」如果上面任何一个 **不适合 ** 的情况描述到你，那正确做法就是去用别的东西（甚至什么都不用），这不是框架的失败。如果你符合的是某种 ** 适合** 情况的组合，那后面的文件就是写给你的。

## 延伸阅读

- [为什么是 KohakuTerrarium](foundations/why-kohakuterrarium.md) — 这个框架背后的出发点。
- [什么是 Agent](foundations/what-is-an-agent.md) — 这一页允许你偏离的那条标准推导路径。
- [模式](patterns.md) — 一些刻意打破「一个模块 = 一个用途」直觉的模块组合方式。
- [ROADMAP](../../ROADMAP.md) — 那些还很粗糙的部分，之后要往哪里去。
