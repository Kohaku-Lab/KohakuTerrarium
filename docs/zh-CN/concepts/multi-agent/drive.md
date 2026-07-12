---
title: Drive
summary: 引擎以普通事件投递的持久、可指派的运行时承诺。它是与 session、频道并列的 Terrarium 资源，选用性质，绝不是一个推理循环。
tags:
  - concepts
  - multi-agent
  - drive
---

# Drive

## 它是什么

一个 **Drive** 是一份持久、可寻址、可指派的运行时承诺，它可以为某只
Creature 产生普通事件。「持续调查这个事故，直到解决或被卡住」「盯着这次
迁移，进程重启后继续」「跨多个回合完成这个研究目标」。Drive 存下这份
承诺，决定它*何时*可以被推进，向持有它的 Creature 投递一个唤醒事件，能
挺过重启，并在图在它脚下变化时自我校正。

Drive 是一种 **选用的、由 Terrarium 管理的运行时资源**，与
[session](../modules/session-and-environment.md) 或
[频道](../modules/channel.md) 同属一族。引擎拥有这套设施；每个图像拥有它
的 session store 一样拥有自己的 Drive 记录。一只 Creature 在零个 Drive
下也能正常运行，一个不带 Drive 配置构建的 Terrarium 根本没有任何 Drive
机制。

Drive 刻意 **不是**：

- Creature 的第七个组件（Controller / Input / Trigger / Tool / Output /
  子 Agent / 插件才是全部）；
- 由 Creature 或 session 拥有的目标循环；
- 一个 LLM、规划器、评估器或动机官能；
- trigger、tool、插件、session 或频道的替代品；
- 保证某个外部副作用*恰好执行一次*的承诺。

## 它为什么存在

应用程序已经用插件状态、scratchpad、计时器、命令和手工编排来近似持久的
追求。这些拼块证明了这个想法可以组合，但每个应用都重造了一套微妙不同、
通常也不完整的外层生命周期：持久身份、指派、resume 校正、带版本的变更、
重试、以及管理性检视。缺的那块是 **运行时协调，而非推理**，它和 Terrarium
已经拥有的资源形状相同：

| 资源 | Terrarium 在机制上拥有 | Creature / 应用提供含义 |
|---|---|---|
| Session | 持久历史、附着、合并/分裂谱系 | 记住的内容意味着什么 |
| 频道 | 身份、接线、广播投递 | 消息意味着什么、是否行动 |
| **Drive** | 身份、指派、就绪、持久投递、恢复 | 这份承诺意味着什么、如何追求 |

Drive 还需要 Creature 本地模块拿不到的生命周期知识：Creature 的
启动/停止/移除、图成员关系、图合并/分裂、session 附着、远程归属、引擎
关停。Terrarium 已经拥有这些事实，而且能在*从不调用 LLM* 的前提下协调
它们。

## 所有权边界

这是承重的规则，也是 Drive 活在引擎里的理由：

- **Terrarium 拥有机制。** 全局稳定的 `drive_id` 和单调的 `revision`；
  scope 与指派；确定性的生命周期转换校验；就绪/依赖计算；持久化与
  事务性 outbox；物理投递、重试、确认与死信状态；陈旧 revision 与陈旧
  epoch 的抑制；在启动/停止/移除/重新指派/resume/拓扑变化时的校正；
  actor 身份、能力检查与审计；本地/远程/多节点的一致性。
- **Creature 拥有含义。** 解释 Drive 的 `kind`、`title` 与 `spec`；
  规划与工具选择；执行副作用；评估进度与收集证据；决定何时*提议*
  等待、卡住、完成或失败；在被打断的尝试之后的恢复推理。

### 非智力的运行时规则

Terrarium 只可以回答 **确定性** 的问题：Drive 是否存在、这个 revision
是否当前？它的状态可投递吗？受指派者在场且运行中吗？它的依赖是否到达了
配置的状态？`not_before` 过去了吗？actor 被授权了吗？注册的校验器接受了
提议吗？这个投递是陈旧、重复、超预算还是在等退避？

Terrarium **绝不能** 回答语义问题：目标真的达成了吗？这个计划好吗？
Creature 接下来该做什么？进度有意义吗？可以由 Creature、人类、外部服务
或确定性的注册验证器来*提议*这些结论；Terrarium 只应用有效的状态转换。
`COMPLETED` 意味着一个被授权的提议通过了配置的策略——而不是引擎对世界做了
推理。

## 生命周期状态

一个 Drive 每次只有一个 **运行时控制状态**（这些是引擎控制状态，不是引擎
对目标的看法）：

| 状态 | 可投递？ | 运行时含义 |
|---|---|---|
| `draft` | 否 | 存在但未被准入追求。 |
| `active` | 是，就绪时 | 有资格投递。 |
| `waiting` | 直到确定性唤醒条件才可 | 在等时间/依赖/外部信号。 |
| `blocked` | 否（默认） | 需要 actor 介入或策略定义的解卡。 |
| `paused` | 否 | 显式挂起，但未宣告失败。 |
| `completed` | 否 | 已接受的完成提议；终态。 |
| `failed` | 否 | 已接受的不可恢复失败；终态。 |
| `cancelled` | 否 | 显式放弃；终态。 |
| `retired` | 否 | 历史墓碑 / 保留终态。 |

通用转换图：

```text
 draft ------> active <------ paused
   |             |  ^            ^
   |             |  |            |
   +-> cancelled |  +-- waiting -+
                 |       |
                 +-----> blocked
                 |
                 +-----> completed
                 +-----> failed
                 +-----> cancelled

 completed / failed / cancelled ---> retired
```

超出这张通用图的任何东西都需要一个已启用注册项的策略。**默认禁止重开
终态 Drive**；预期做法是创建一个带 `metadata.parent_drive_id` 的后继
Drive。如果某个注册项显式允许重开，仓库会递增该 Drive 的 `lifecycle_epoch`
（这会使先前的每次投递失效）并写一条审计记录。waiting 的 Drive 只携带
确定性的唤醒条件——一个时间戳、一个依赖谓词、一个具名外部信号、一个注册
就绪函数，或一位被授权 actor 的手动唤醒。管理器绝不从自由文本推断就绪。

## 投递：至少一次，逻辑去重

一个 Drive 通过变成一个普通的 `TriggerEvent`（`drive_ready` /
`drive_resume` / `drive_recovery`）来成为工作，经由公共的 Creature 入口
投递——和任何 trigger 走同一条准入、串行化、插件、controller、tool 与
output 路径。分发器不调用任何私有 agent 方法，也不启动第二个推理循环；
Creature 仍是单回合串行器。

诚实陈述的投递保证：

> 物理 Drive 事件投递是 **至少一次**。处理在逻辑上按 delivery ID、Drive
> revision、lifecycle epoch、assignment ID 和 readiness generation 去重。

**没有恰好一次保证，框架也从不宣称有。** 恰好一次的副作用无法跨越一次
模型回合、一次工具调用和一个可以各自独立失败的外部系统来承诺。引擎把
*物理分发* 和 *逻辑确认* 分开：当 Creature 接受事件时投递变为
`admitted`，当那个回合落定时变为 `acknowledged`。`acknowledged` 意味着
「回合落定了」——**不** 意味着「Drive 完成了」，**也不** 意味着「外部
副作用恰好发生了一次」。在准入前，分发器会拒绝或作废任何 Drive 已消失或
终态、revision 或 epoch 陈旧、指派已改变、或已被准入过的投递。

执行副作用的工具应当携带自己的幂等键；Drive 投递上下文正是为此暴露了
`delivery_id`，让有副作用的工具有一个稳定的键去去重。

### 恢复对不确定性诚实

如果 Creature 在准入与确认之间停止（或进程崩溃），先前那次尝试是
**不确定的**：它的副作用可能跑了也可能没跑。在 Creature 重启并通过下文
的恢复屏障之后，管理器会通过 `drive_resume` 或 `drive_recovery` 事件
重新引入仍然当前的 Drive，Creature 看到的投影会说：

> 先前的一次尝试可能已经执行了副作用。在重复动作之前检视当前状态并
> 校正。在支持之处用 delivery ID 作为幂等键。

恢复事件绝不指示盲目重放，任何 UI 也绝不把恢复或卡住状态渲染成普通
成功。

## 恢复屏障

Drive 绝不能对着一个半恢复的运行时被投递。某些构建路径会在 session
store 附着之前就启动 Creature；Drive 要求一个显式的次序：

```text
构建 creature
-> 恢复 conversation / scratchpad / 插件 / session 状态
-> 附着图的 SessionStore 与 Drive 仓库
-> 重放运行时拓扑
-> 启动 creature
-> 完成启动 trigger
-> 标记 creature 恢复就绪
-> 校正 Drive
```

在这道屏障之前不投递任何 Drive。正是这一点防止一个 Drive 对着空对话或
半恢复的图去追求目标。冷启动时次序永远是：先恢复，然后启动 trigger，
最后 Drive 校正。

## 注册项：已安装不等于已启用

新的 Drive **实例** 在运行时动态创建。新的可执行 Drive **kind** 不是——
一个 Drive 的 `kind` 由一个 **Drive 注册项** 服务，它是一个确定性的
运行时扩展，为那个 kind 提供 schema 校验、就绪规则、事件投影、可选的
完成验证器，以及一段有界的 prompt 贡献。注册项不运行 LLM、不写仓库、也
不分发事件；它只回答核心问过来的确定性问题。框架自带一个内置的
`generic` 注册项（不透明 spec、手动终态提议）；其他 kind——例如
`goal`——以已安装的包的形式到来。

两个容易混淆的独立概念：

- **发现** —— 一个包声明 `drive_registrations:` manifest 槽位使某注册项
  *可用*。Studio 目录可以列出它而无需 import 它的代码。
- **启用** —— 一个注册项只有被显式启用（在 Drive 设置里，或把实例传给
  `Terrarium(drive_registrations=[...])`）才变得可用。**已安装绝不会被
  自动启用。** 只有已启用的注册项才能为它的 kind 创建、校验、投影、
  调度或贡献 prompt 文本。

注册项 `name` 重复以及 `kind` 归属冲突都是硬校验错误，会在任何应用之前
被暴露出来。

### 当注册项被禁用或不可用

已持久化的 Drive 记录 **绝不会** 仅因其注册项被关掉就被删除或改写。
可用性是一个*派生的*运行时条件（`DriveAvailability`），不是新状态，也不是
消耗一个 revision 的理由：

- 记录仍可列出，仍可被管理性地 pause / cancel / retire；
- 派生条件是 `registration_disabled`、`registration_unavailable` 或
  `registration_incompatible`，只要它成立就 **不准入任何投递**；
- 任何需要该注册项语义的操作——spec 编辑、就绪评估、投影、终态验证——都
  **fail closed（失败即拒）**；
- 重新启用一个兼容的注册项会清除该条件并校正仍活跃的记录；不兼容的
  schema 版本需要先做一次显式迁移；
- 通用的读取/状态视图与已保存 session 查看器全程可用。

## 持久化

一个 Drive 的持久性取决于它的图如何设置：

| 引擎 / 图设置 | Drive 行为 |
|---|---|
| 附着了 session store / autosession | **持久**；进程重启后可 resume。 |
| 无 session 且无单独 Drive store | **仅内存**；能挺过 Creature 停止，挺不过引擎关停。 |
| 显式 `drive_store=` | **持久**，独立于对话 session（用于服务/守护型应用）。 |

当附着了 session 时，Drive 仓库活在一个 **与 session 配对的专用 sidecar
文件** 里——在 `<name>.kohakutr` 旁边的 `<name>.kohakutr.drives`——这样
Drive 的写和对话的写就绝不在同一个数据库上争抢。复制一个带持久 Drive 的
session 意味着也要复制那个 sidecar。机制细节见
[programmatic 指南](../../guides/programmatic-drive.md)。

## 它与 `/goal` 的关系

`/goal` 功能是在通用 Drive 设施之上的 **一种选用组合**，而不是 Drive 的
定义。它是两个独立的开关：一个 `goal` Drive *注册项*（确定性的 kind
语义）和一个 `GoalPlugin`（`/goal` 斜杠命令及其 prompt 指导）。任一个都
可以在没有另一个的情况下启用。见
[Goal：Drive 之上的组合](../../guides/goal.md)。

## 因此你能构建什么

- **持久的事故追求。** 一只 Creature 跨重启保持一个 `blocked`/`active`
  的 Drive；恢复事件告诉它在行动前重新检视。
- **调度/等待型工作。** 一个 `waiting` 的 Drive 在某个时间戳或某个依赖
  Drive 到达终态时重新武装。
- **operator 可见的承诺。** 因为 Drive 是一等运行时资源，它的状态、
  owner、受指派者以及恢复/卡住警告在任何操作 Terrarium 的地方都可检视——
  与是否安装了 `/goal` 无关。

## 别被框住

一只 Creature 在没有 Drive 时也完全有效，而且大多数 Creature 永远不需要
它。只有当承诺确实比单个回合更持久 *而且* 需要引擎的协调（身份、指派、
resume、恢复）时，才伸手去拿 Drive。一次性任务是一个回合；周期性检查是
一个 trigger；一个持久、可指派、可恢复的目标才是一个 Drive。

## 延伸阅读

- [Session 与环境](../modules/session-and-environment.md)：持久 Drive 在
  旁边持久化的每图状态。
- [频道](../modules/channel.md)：另一个广播投递的运行时资源。
- [Programmatic Drive](../../guides/programmatic-drive.md)：从 Python 直接
  驱动 Drive 运行时。
- [Goal](../../guides/goal.md)：`/goal` 作为 Drive 之上的选用组合。
- [配置参考](../../reference/configuration.md)：`drive-settings.yaml`
  schema 与 `drive_registrations:` manifest 槽位。
