---
title: Goal
summary: /goal 作为 Drive 运行时之上的选用组合——两个独立开关（一个 goal 注册项与 GoalPlugin）、命令集、所有权、user-confirm 完成、只会暂停而不会完成的预算，以及诚实的恢复。
tags:
  - guides
  - goal
  - drive
---

# Goal

`/goal` **不是** 一个框架功能。它是完全建在通用
[Drive](../concepts/multi-agent/drive.md) 运行时之上的一个选用组合，作为
**内置** 插件 + 注册项发布：`GoalPlugin`
（`kohakuterrarium.builtins.plugins.goal`）与 `GoalDriveRegistration`
（`kohakuterrarium.terrarium.drive.goal`）。两者都内置于代码树，但
**默认禁用**——你按 agent 逐一启用它们，就和其它内置插件（sandbox /
budget / permgate / compact）一样。Drive 做持久的工作；Goal 加上一个
人类友好的斜杠命令和一个叫 `goal` 的 kind。因为它只是一个组合，`/goal`
所做的一切也都能通过 [programmatic 指南](programmatic-drive.md)里的通用
Drive 工具和 API 触达。

## 两个独立开关

要理解的最重要一点：Goal 是 **两个分离的开关**，而且任一个都不蕴含
另一个。

| 开关 | 它是什么 | 你如何启用它 |
|---|---|---|
| `goal` **注册项** | 确定性的 `kind="goal"` 策略：schema、感知 autonomy 的就绪、投影、终态验证器。 | 在 [Drive 设置](../reference/configuration.md)里启用内置的 `goal` 注册项，或把 `GoalDriveRegistration()` 传给 `Terrarium(drive_registrations=[...])`。 |
| `GoalPlugin` | 选用的内置插件，贡献 `/goal` 命令和一段简短的 Goal 语义 prompt。 | 在插件面板（`/plugin`、web/TUI 的 Plugins 标签页）里启用它，或把它列进某只 Creature 的 `plugins:` 配置，或 `agent.add_plugin(GoalPlugin())`。 |

- **启用 `GoalPlugin` 不会启用 `goal` 注册项**，反之亦然。它们是两个独立的
  内置开关——一个在插件面板，另一个在 Drive 设置。
- **注册项被禁用** → `drive_create(kind="goal")` 和 `/goal set` 会带着
  清晰的消息 fail closed；store 里已有的任何 Goal 记录保持可检视。
- **插件被禁用** → `/goal` 从每份命令清单里消失；先前创建的 Goal Drive
  在（仍启用的）注册项下继续运行，可通过通用 Drive 界面管理。

这个分离是刻意的：运行时可执行的策略（注册项）是一个 operator/设置的
决定，而 `/goal` 的 UX 是一个按 Creature 的插件决定。一个是*这个节点可以
运行哪些 kind*；另一个是*这只 Creature 是否提供斜杠命令*。

## 启用

两个开关都内置于代码树；**没有安装步骤**。为运行时启用注册项——通过被
托管的设置界面（它写 `drive-settings.yaml`）：

```yaml
# ~/.kohakuterrarium/drive-settings.yaml
runtime:
  enabled: true
registrations:
  goal:
    enabled: true
```

然后通过启用内置插件来让某只 Creature 启用 `/goal`——在插件面板
（`/plugin`、web/TUI 的 Plugins 标签页），或把它列进 Creature 配置：

```yaml
# 一只 creature 的 config.yaml
plugins:
  - name: goal          # 解析到内置的 GoalPlugin
```

或者在 Python 里显式地两者都做，完全不用设置文件：

```python
from kohakuterrarium import Terrarium
from kohakuterrarium.builtins.plugins.goal import GoalPlugin
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.drive.goal import GoalDriveRegistration

async with Terrarium(
    drive_config=DriveRuntimeConfig(enabled=True),
    drive_registrations=[GoalDriveRegistration()],   # 注册项开关
) as engine:
    creature = await engine.add_creature(
        "@kt-biome/creatures/general",
        plugins=[GoalPlugin()],                       # 插件开关
        start=True,
    )
```

## `/goal` 命令集

```text
/goal set [autonomy=continue_when_ready] [policy=user_confirm] [criteria=a;b] <objective>
/goal show [id]
/goal list
/goal pause  [id]
/goal resume [id]
/goal cancel [id]
/goal complete [id]         # 用户权威的完成
/goal assign <id> <creature>
```

`/goal` 总是以 **已认证用户** 这个 actor 行动——绝不以插件或 Creature
的身份——并从受信的命令上下文解析出 `TerrariumService` 和聚焦的
Creature。它从不从命令文本接受一个 actor 字符串，也不存自己的任何状态：
每次 `show` / `list` 都读取实时 Drive 状态。`/goal set` 不带显式 id 时
作用于该 Creature 最近一个活跃的 Goal；给一个 id 来消歧。

`/goal set` 默认 `autonomy=manual`：Creature 现在推进一个回合，之后由
`/goal resume` 唤醒下一个回合。传 `autonomy=continue_when_ready` 得到一个
自行持续的 Goal。`/goal list` 列出聚焦 Creature 所在图里的所有存活 Goal
并标出各自的受派者，所以派给其他 Creature 的 Goal 也看得见。
`/goal resume` 重新激活一个 paused / waiting / blocked 的 Goal 然后唤醒它；
对一个活跃的 manual Goal 它就只是唤醒。在仍有其他存活 Goal 时设置新 Goal
会把那些 Goal 报告出来；每个 Creature 的活跃 Goal 上限由运行时强制执行，
到达上限时命令会提示你先暂停或取消一个。

命令只是 UI。它调用 Python、HTTP、web 面板和通用 Drive 工具所调用的同一批
`TerrariumService` 方法，所以一个通过 `/goal` 创建的 Goal 和用任何其他
方式创建的都完全相同。

## GoalSpec

`goal` 是一个 Drive kind，不是对 Drive 的重新定义。它的 `spec` 是：

```python
{
    "objective": str,                       # 必填
    "success_criteria": list[str],
    "constraints": list[str],
    "completion_policy": "self_propose" | "user_confirm" | "verifier",
    "autonomy": "manual" | "continue_when_ready",
    "budgets": {
        "max_turns": int | None,
        "max_tool_calls": int | None,
        "max_walltime_s": int | None,
    },
}
```

只有 `objective` 是必填的；其他一切都保守地取默认（`manual` autonomy、
`self_propose` 完成、无预算）。Drive 核心从不解析目标或判断标准——那是
Creature 的活。Creature 每回合收到的投影告诉它这是一份*持续的承诺，而不是
让它发明一个新目标*，要带证据报告实质进度，并带证据*提议*完成而不是断言
它。

### autonomy 驱动续跑（并没有 GoalRunner）

- `manual` —— Goal 每次唤醒被推进一次然后等待；一个被授权的 actor 必须
  再次唤醒它（`/goal resume`，或某个依赖变就绪）。
- `continue_when_ready` —— 每次回合落定后注册项的就绪重新武装，于是通用
  Drive 分发器发出下一个普通 Drive 事件。续跑是分发器对就绪的反应，
  **而不是** 一个特殊的 agent 循环。

## 所有权

谁拥有一个 Goal（以及谁可以完全管理它）取决于创建路径。所有权不是指派：
受指派者推进工作；owner 控制记录。

| 创建路径 | 默认 owner | 默认受指派者 | 谁可以完全管理它 |
|---|---|---|---|
| 人类 `/goal set ...` | 已认证用户 | 聚焦的 creature | 用户 / 管理员；受指派者可报告 + 提议 |
| Web / TUI Goal 表单 | 已认证用户 | 所选 creature | 用户 / 管理员；受指派者可报告 + 提议 |
| Creature 调用 `drive_create(kind="goal")` | 那只 creature | 那只 creature | 那只 creature / 管理员 |
| 特权 `group_drive` 创建 | 图或所选 actor | 所选图成员 | 特权图权限 / 管理员 |
| 应用 Python / API | 提供的 service / user actor | 显式 | owner / 能力策略 |

因为一个用户拥有的 Goal 被指派给*另一个* actor（那只 Creature），
`/goal set` 和 `/goal assign` 是图权限操作。本地 operator 控制台为这两个
动词提供一次显式的、经审计的 operator 提升；其他每个动词（`show` /
`list` / `pause` / `resume` / `cancel` / `complete`）都以普通用户 owner
身份运行，不需要提升。

## 完成是权威的，依策略而定

`/goal complete`（以及任何完成）走一个 **提议**，而 `goal` 注册项的
`completion_policy` 决定什么使它定案：

- **`self_propose`** —— 一个被授权的提议被直接接受。Creature 判断目标达成
  时可以完成它自己的 Goal。
- **`user_confirm`** —— 只有一个 **user actor** 的提议才能定案。一只
  Creature 提议完成 *不* 被接受；完成留在人类 `/goal complete` 路径上。
  这就是你如何把人类留在环里。
- **`verifier`** —— 提议必须携带非空证据；一个无证据的完成被拒绝。

Terrarium 从不判断目标是否真的达成。它只应用一个被授权、满足策略的提议
所赢得的转换。

## 预算会暂停，绝不完成

一个 Goal 的 `budgets` 限定 `continue_when_ready` autonomy 在必须停下
报到之前跑多远。当一个预算耗尽时：

- 就绪停止重新武装，带一个可观察的原因，如 `turn budget exhausted (3/3)`；
- Creature 被引导去 **提议一次 pause 或 block**；
- Goal **绝不** 因为某个预算跑光而被标为 `completed`。

预算耗尽是「停下来问」，不是成功。这是贯穿整个 Drive 运行时的硬规则，
不只 Goal。

## 中断会暂停 Goal

停就是停。当用户中断一个由 Goal 投递启动的回合时，运行时以
`user_interrupted` 为原因确认该投递，并把 Goal 移到 `paused`，状态原因
相同。因此一个 `continue_when_ready` Goal **不会** 在下一次就绪扫描时
重新武装，而排在被中断回合后面、尚未执行的投递也以同样方式落定而不是
重试。Goal 只会通过显式的 `/goal resume`（或通用 Drive 界面上的唤醒）
恢复。每个界面都会在对话记录里标出由 Drive 触发的回合的开始，带上
Goal id 和投递原因，所以一个并非由用户输入开始的回合从不会悄无声息。

## 恢复是诚实的

一个 Goal 是持久的，所以一只 Creature 可能在追求途中被打断（一次停止、
一次崩溃）。重启后，在通过 [恢复屏障](../concepts/multi-agent/drive.md)
之后，仍然活跃的 Goal 会作为一个恢复事件回来，其指导是明确的：

> 先前的一次尝试可能已经执行了副作用。在重复任何副作用之前检视当前的
> 世界。

框架从不告诉 Creature 盲目重放，也从不软化这个警告。如果一个 Goal 步骤有
绝不能重复施加的外部效果，做它的工具应当用它自己的幂等键——投递上下文为此
暴露了 `delivery_id`。投递是
[至少一次，不是恰好一次](../concepts/multi-agent/drive.md)。

## 没有 GoalPlugin 时

因为 `/goal` 只是一个便利，一个已启用 `goal` 注册项但没启用插件的用户
仍然可以：

- 通过 Python / API / CLI / web 创建一个 `goal`（或 `generic`）Drive；
- 让 Creature 自己调用 `drive_create(kind="goal")`；
- 让 Creature 通过通用 `drive_*` 工具管理它自己拥有的 Goal；
- 通过通用 Drive 界面检视和管理被授权的 Goal。

`/goal` 是叠在上面的选用语法和 UX——不是能力边界。

## 延伸阅读

- [Drive 概念](../concepts/multi-agent/drive.md)：`/goal` 组合于其上的
  运行时。
- [Programmatic Drive](programmatic-drive.md)：`/goal` 在底层调用的通用
  工具和 service API。
- 内置实现：`kohakuterrarium.terrarium.drive.goal` 里的
  `GoalDriveRegistration` 与 GoalSpec 辅助函数；
  `kohakuterrarium.builtins.plugins.goal` 里的 `GoalPlugin` 与 `/goal` 命令。
- [配置参考](../reference/configuration.md)：在 `drive-settings.yaml` 里
  启用注册项。
