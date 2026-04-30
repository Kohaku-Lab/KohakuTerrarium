---
title: 会话与环境 (Session and environment)
summary: 每个 Creature的私有状态（session）与Terrarium共享状态（environment）之间的差异，以及它们如何互动。
tags:
  - concepts
  - module
  - session
  - environment
---

# Session 与 environment

## 它是什么

状态分成两个层级：

- **Session**— 属于单一Creature的私有状态。包含该Creature的
  scratchpad、私有 channel、TUI 参照、job store，以及任何
  自订 extras。
- **Environment**— 整个执行过程共享的状态（更精确地说，是整个
  terrarium 共享）。包含共享的 channel registry，以及一个小型的
  自订 context dict。

独立执行的 Creature会有一个 session。terrarium 则会有一个 environment，
并且每个 Creature各自有一个 session。

## 为什么会有它

在多 Agent 系统里，错误的预设是「所有东西都共享」。
如果每个 Creature都能写入其他Creature的 scratchpad，
那你其实只是绕了一大圈做出「全域可变状态（Global Mutable State）」。
除错会变得不可能。

这个框架的预设刚好相反：**预设私有，必须明确 opt-in 才共享**。
Creature会保留自己的状态，除非它明确把数据送到共享 channel。
terrarium 是唯一能看见所有Creature的东西；
Creature只能看见自己的 session，以及那些它主动要求监听的共享 channel。

## 我们如何定义它

```
Environment（可选，每个 terrarium 一个）
├── shared_channels  （ChannelRegistry）
├── context  （dict，由用户定义）
└── <这里没有私有状态>

Session（每个 Creature一个）
├── scratchpad  （key-value，私有）
├── channels  （私有 ChannelRegistry；可别名到共享 registry）
├── tui  （TUI 参照，适用时）
├── extras  （dict，由用户定义）
└── key  （session 识别键）
```

规则：

- 一个Creature只会有一个 session。
- environment 会在Creature之间共享。独立 Creature可以不使用它。
- 共享 channel 存在于 environment 上。Creature透过为特定 channel 名称
  加上一个 `ChannelTrigger` 来 opt-in。
- scratchpad 永远是 session 私有的。

## 我们如何实现它

`core/session.py` 定义了 `Session`，以及依照 key 取得／建立 session 的辅助函数。
`core/environment.py` 定义 `Environment`。
`Terrarium` 会建立 graph/environment 语境，并将 session 挂到每个 Creature 上。

内建的 `scratchpad` 工具会读写目前Creature的 session scratchpad。
`send_message` 工具则会选择正确的 channel registry
（先私有，再共享）。

## 因此你可以做什么

- **跨回合的私有记忆**。 每个 Creature都可以把 scratchpad 当成工作笔记本使用；
  不会有数据外漏。
- **共享汇合点**。 两个都在监听同一个共享 channel 的 Creature，
  可以在不了解彼此内部实现的情况下协调工作。
- **把 session 当成单一Creature的状态总线**。 同一个Creature内彼此协作的工具，
  可以把 scratchpad 当成 KV 汇合点。
- **以 environment 为范围的自订 context**。 驱动 terrarium 的 HTTP 应用程序，
  可以把用户识别／request-id 放进 environment 的 `context` dict，
  让 plugins 自行取用。

## 不要被框住

独立 Creature不需要 environment。只靠 trigger 的 Creature 也不一定需要 scratchpad。
框架只会在真正重要的地方强制区分私有／共享；
如果只有单一Creature，它也很乐意把 session 当成唯一的状态来源。

## 另请参考

- [Channel](channel.md) — 需要明确 opt-in 的共享原语。
- [多 Agent / terrarium](../multi-agent/terrarium.md) — environment 真正重要的地方。
- [impl-notes/session-persistence](../impl-notes/session-persistence.md) — session 状态实际如何落在磁碟上。
