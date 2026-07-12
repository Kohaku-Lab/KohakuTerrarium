---
title: Programmatic Drive
summary: 从 Python 直接驱动持久承诺运行时——显式引擎配置、自服务工具、propose/verify 完成、sidecar 持久化文件与 resume，且不依赖 Studio。
tags:
  - guides
  - drive
  - programmatic
---

# Programmatic Drive

面向从 Python 直接驱动 [Drive 运行时](../concepts/multi-agent/drive.md)
的读者。这是低层、不经 Studio 的路径：你用显式的 Drive 参数构建一个
`Terrarium`，并通过引擎的 `TerrariumService` 创建和管理 Drive。这里没有
任何东西会读 `~/.kohakuterrarium` 或向 Studio 要什么——被托管的界面
（[`kt`](../reference/cli.md)、web、TUI）坐在这条路径*之上*，从
[Drive 设置](../reference/configuration.md)解析出同样的显式参数。

如果你只想要概念，先读
[概念 / Drive](../concepts/multi-agent/drive.md)。如果你想要 `/goal`，那是
建在这里一切之上的一个组合——见 [Goal](goal.md)。

## 启用运行时

不传 `drive_config`，一个 Terrarium 就没有任何 Drive 机制。启用是显式的
依赖注入：配置加上一份具体的、非空的注册项列表。

```python
import asyncio

from kohakuterrarium import Terrarium
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)


async def main():
    async with Terrarium(
        session_dir="runs/",                       # autosession：Drive 会持久化
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),  # == [GenericDriveRegistration()]
    ) as engine:
        assert engine.drives is not None           # 运行时已启用
        ...

asyncio.run(main())
```

构造函数强制的规则：

- **`drive_config=None`（默认）意味着没有 Drive 运行时。** 引擎不构建
  manager、tools、prompt 或分发器，`engine.drives` 为 `None`。
- **`enabled=True` 却不给注册项会校验失败。** 低层引擎绝不扫描包，也不
  凭空发明一套启用集。你要么显式传 `default_registrations()`（generic
  kind），要么传你自己的实例。
- 注册项在任何 Creature 启动之前会被冲突检查（`name` 重复或 `kind` 冲突
  都是硬错误）。
- 每个便捷构造函数都转发这同样的三个参数：

```python
engine = await Terrarium.from_recipe(
    "team.yaml",
    drive_config=drive_config,
    drive_registrations=registrations,
)
engine = await Terrarium.resume(
    "run.kohakutr",
    drive_config=drive_config,
    drive_registrations=registrations,
)
```

recipe 对象不变——recipe 从不携带 Drive 字段（见
[recipe 只管图结构](terrariums.md)）。把同一个 recipe 应用到两个引擎上
可能得到不同的 Drive 能力，因为不同的是引擎参数，而不是 recipe。

`DriveRuntimeConfig` 是调度器 / 重试 / 保留 / 载荷限制所在之处；每个字段
及其默认值都在 [配置参考](../reference/configuration.md)里。

## 创建与管理 Drive

记录通过一个 `TerrariumService` 创建与管理。对于嵌入式调用者，那就是
`LocalTerrariumService(engine)`。这个界面就是 Studio 所用的界面，所以你的
代码和被托管的 UI 共享同一套行为和同一套类型化错误。

每次变更都携带一个由你从自己受信上下文提供的 **actor**；manager 在每次
调用上重新检查授权，所以持有一个 service 对象本身从来不是权限。

```python
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest

worker = await engine.add_creature("@kt-biome/creatures/swe", start=True)
service = LocalTerrariumService(engine)

operator = ActorRef("service", "deploy-bot")   # "<kind>:<identity>"

view = await service.create_drive(
    CreateDriveRequest(
        kind="generic",
        title="Watch the deployment",
        scope_type="graph",                     # 或 "creature"
        scope_id=worker.graph_id,
        owner=operator,
        owner_scope="service",
        created_by=operator,
        spec={"instruction": "Monitor until the rollout is stable"},
        assignee_creature_id=worker.creature_id,
    ),
    graph_id=worker.graph_id,
    actor=operator,
    operator=True,          # 经审计的图权限提升（见下）
)
drive_id = view.record.drive_id
```

`create_drive`（以及每次变更）返回一个 **`DriveView`**：`record`、它的
`assignee_creature_id` / `assignment_state`、派生的 `availability` 与
`durability`，以及一个按 actor 限定的 `allowed_actions` 元组，好让 UI
不用猜就能渲染出感知权限的控件。

### actor、所有权与 operator 标志

- 由调用者拥有的 **creature scope** 的 Drive 不需要提升——那是任何
  Creature（以及下文的自服务工具）的基线能力。
- 创建一个 **graph scope** 的 Drive、指派给另一只 Creature、或转移所有权
  都是图权限操作。一个普通的 `user:` actor 不持有它；受信的嵌入式调用者
  传 `operator=True`，manager 把它当作一次显式的、经审计的提升——绝不当
  作 creature 特权。
- 指派 **不是** 所有权。被指派了一个由别人拥有的 Drive 的 Creature 可以
  读它、报告进度、并*提议*转换，但不能改写、取消、重新指派或退休它。

### 读、更新、转换

```python
from kohakuterrarium.terrarium.drive.requests import DrivePatch

# 读
view = await service.get_drive(drive_id, actor=operator)
views = await service.list_drives(
    actor=operator,
    statuses=frozenset({DriveStatus.ACTIVE, DriveStatus.WAITING}),
)

# CAS 更新——expected_revision 必须匹配，否则抛 DriveConflictError
view = await service.update_drive(
    drive_id,
    DrivePatch(priority=5),
    expected_revision=view.record.revision,
    actor=operator,
)

# 非终态的控制转换
await service.transition_drive(
    drive_id, DriveStatus.PAUSED,
    expected_revision=view.record.revision, actor=operator,
)
await service.wake_drive(drive_id, actor=operator)     # 重新武装一个 waiting 的 Drive

# 仅追加的进度（不递增 revision）
await service.report_drive_progress(
    drive_id, summary="rollout at 40%", evidence={"pct": 40}, actor=operator,
)
```

每次正式变更都取 `expected_revision`（乐观并发）和一个可选的
`idempotency_key`。陈旧的 revision 抛 `DriveConflictError`；用同一个幂等键
配不同的载荷抛 `DriveIdempotencyConflictError`。`report_drive_progress`
是仅追加的例外，不取 `expected_revision`。

## Propose / verify 完成

一只 Creature（或一个 operator）**不**直接写终态。完成与失败走一个
**提议**，好让注册项校验器和任何必需的验证器先跑：

```python
result = await service.propose_drive_transition(
    drive_id, DriveStatus.COMPLETED,
    evidence={"tests": "green", "run": "ci#4821"},
    expected_revision=view.record.revision,
    actor=operator,
)

if isinstance(result, dict) and result.get("pending"):
    # 一个必需的验证器 / 两方审批在等待
    final = await service.approve_drive_proposal(
        result["proposal_id"], actor=operator, operator=True,
    )
else:
    final = result          # 一个 DriveView：提议被直接接受了
```

验证器模式是注册项的决定。`generic` kind 直接接受一个被授权的提议。其他
kind 可以要求一个具名验证器、一个特定的审批 actor 类别，或一个不同的
两方审批者——而一个缺失的必需验证器 **fail closed（失败即拒）**，绝不
放行。Terrarium 从不判断目标是否真的达成；它只应用一个被授权、被验证的
提议所赢得的转换。

## 自服务工具（面向 Creature）

一个启用了 Drive 的 Terrarium 向它托管的 **每只** Creature 注入五个通用
Drive 工具，外加一个特权工具。这些是 LLM 调用的；它们从工具上下文解析
actor，并在每次调用上强制 owner / scope / ACL 与注册可用性，所以它们在
非特权 Creature 上也是安全的（工具存在不是授权）。

| 工具 | 作用范围 | 它做什么 |
|---|---|---|
| `drive_create` | 每只 creature | 创建一个 **你拥有的** Drive，scope 到你并指派给你。 |
| `drive_status` | 每只 creature | 列出你拥有 / 被指派的 Drive，或按 `drive_id` 取一个。 |
| `drive_update` | 每只 creature | CAS 更新一个你拥有的 Drive。 |
| `drive_report` | 每只 creature | 向一个拥有或被指派的 Drive 追加进度 / 证据。 |
| `drive_transition` | 每只 creature | 管理一个你拥有的 Drive，或对一个别人拥有、指派给你的 Drive 提议一次被允许的转换。 |
| `group_drive` | 仅特权节点 | 创建图拥有的 Drive；在图内 assign / reassign / unassign、转移所有权、唤醒、退休、修复或重放死信。 |

`group_drive` 是 [特权节点](../concepts/multi-agent/privileged-node.md)的
图管理界面——一个由特权节点生成的 worker 不会收到它。运行时启用时，引擎
还注入一段有界的 prompt（通用 Drive 契约加上每个*已启用*注册项的散文，
按名称排序）；它从不把当前 Drive 记录塞进 prompt——那些通过事件和
`drive_status` 到来。

## 你能倚靠（和不能倚靠）的投递

一个 Drive 作为普通的 `TriggerEvent`（`drive_ready` / `drive_resume` /
`drive_recovery`）成为工作，经由公共 Creature 入口投递，并像任何回合一样
落定。保证是 **至少一次**，在逻辑上按 delivery ID、revision、lifecycle
epoch、assignment ID 和 readiness generation 去重。

**没有恰好一次保证。** 如果一个有副作用的工具绝不能重复施加，就给它自己
的幂等键——投递上下文正是为此暴露了 `delivery_id`。在一次被打断的尝试
之后，Creature 会收到一个恢复事件，说先前的一次尝试*可能*已经跑了它的
副作用，要在重复之前校正；它绝不会被告知盲目重放。见
[概念 / Drive 的投递一节](../concepts/multi-agent/drive.md)。

## 持久化与 sidecar 文件

持久性取决于图的设置，创建结果会用 `view.durability`（`"persistent"` 或
`"ephemeral"`）报告它：

| 设置 | 持久性 |
|---|---|
| `Terrarium(session_dir=...)` / 附着了 session store | **persistent** —— 进程重启后可 resume |
| 无 session 且无 `drive_store` | **ephemeral** —— 挺过 Creature 停止，挺不过引擎关停 |
| `Terrarium(drive_store=...)` | **persistent**，独立于对话 session |

当附着了 session 时，Drive 仓库 **不** 写进 `.kohakutr` 文件。它活在一个
与 session 配对的 **sidecar** 里：

```text
runs/run.kohakutr          <- 对话、事件、scratchpad（KohakuVault）
runs/run.kohakutr.drives   <- Drive 仓库（它自己的 SQLite 数据库）
```

两个要规划的后果：

- **一个持久 Drive 随它的 sidecar 一起走。** 如果你复制或移动一个 session
  并想要它的 Drive，就把 `<name>.kohakutr.drives` 和 `<name>.kohakutr`
  一起复制。裸的 `.kohakutr` 不携带任何 Drive 状态。
- **fork 一个 session 不会 fork 它的 Drive。** fork 只复制 `.kohakutr`，
  所以它在构造上就带着零个 Drive 出生——这避免两个分支变更同一份承诺。
  合并与分裂通过行拷贝钩子显式携带 Drive。

在无法解析出持久后端时请求持久化会在引擎构建时失败
（`DrivePersistenceRequiredError`），而不是在一个 Drive 已经活跃之后。

## Resume 与校正

`Terrarium.resume(...)` 取和构造函数 **相同的** 显式 Drive 参数，打开
已持久化的 Drive 状态（从 sidecar），并校正它。它从不重新应用「recipe
Drive 种子」，因为根本没有那种东西——recipe 从不创建 Drive。

```python
engine = await Terrarium.resume(
    "runs/run.kohakutr",
    drive_config=DriveRuntimeConfig(enabled=True),
    drive_registrations=default_registrations(),
)
```

resume 时，在 Creature 通过 [恢复屏障](../concepts/multi-agent/drive.md)
之前不投递任何 Drive：conversation / scratchpad / 插件 / session 状态
已恢复、Drive 仓库已附着、拓扑已重放、Creature 已启动、其启动 trigger
已落定。只有到那时管理器才校正指派并重新引入仍然当前的 Drive——干净停止
用 `drive_resume`，半途被打断的尝试用 `drive_recovery`（带「可能已执行
副作用」的警告）。

如果一个被 resume 的 Drive 的注册项在这个引擎上未启用，记录 **不** 被
删除或降级：它变为不可投递，带一个派生可用性 `registration_disabled` /
`registration_unavailable` / `registration_incompatible`，保持可检视，并在
一个兼容的注册项被启用的那一刻校正。见
[概念 / Drive：当注册项被禁用](../concepts/multi-agent/drive.md)。

## Terrarium 不会做什么

- **对一个 Drive 推理。** 引擎回答确定性问题（revision 是否当前？受指派者
  运行中吗？`not_before` 过去了吗？）。它从不判断目标是否达成、计划好不好、
  或 Creature 接下来该做什么。
- **自己完成一个 Drive。** 预算和错误可以 pause 或 block 一个 Drive；没有
  东西会悄悄把它标为完成。`completed` 需要一个被授权、被验证的提议。
- **回滚副作用。** 取消一个 Drive 阻止未来的投递；它不会撤销先前回合已
  执行的效果。

## 延伸阅读

- [Drive 概念](../concepts/multi-agent/drive.md)：这套 API 背后的模型。
- [Goal](goal.md)：`/goal` 作为 Drive 之上的选用组合。
- [配置参考](../reference/configuration.md)：`DriveRuntimeConfig` 字段、
  `drive-settings.yaml` 与 `drive_registrations:` manifest 槽位。
- [Terrariums](terrariums.md)：托管 Drive 运行时的引擎。
- 内置的 Goal 组合（`kohakuterrarium.terrarium.drive.goal` +
  `kohakuterrarium.builtins.plugins.goal`）：一个完全建在这套公共界面上的
  Goal 注册项 + 插件。
