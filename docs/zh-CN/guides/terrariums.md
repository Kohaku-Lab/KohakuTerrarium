---
title: 生态瓶 （Terrarium）
summary: 用频道、输出接线、root agent、热插拔、观察模式做横向多代理协作。
tags:
 - guides
 - terrarium
 - multi-agent
---

# Terrarium

给想把多只Creature组起来合作的读者。

 **Terrarium** 纯粹是接线：自己没有 LLM、不做决策。它拥有共用频道、管理里面的 Creature 的 lifecycle，并提供框架层级的 ** 输出接线** — 把一只 Creature 轮次结束的输出自动送到指定目标。Creature 本身不知道自己在 Terrarium 里 — 它们只知道自己 listen 哪些频道名字、能送到哪些频道名字，而 Terrarium 让那些名字变成真的。

相关概念：[Terrarium 概念](../concepts/multi-agent/terrarium.md)、[root agent 概念](../concepts/multi-agent/root-agent.md)、[频道](../concepts/modules/channel.md)。

我们把Terrarium当作横向多代理协作的 **提案架构** — 这些零件凑得起来 (接线 + 频道 + 热插拔 + 观察 + 向 root 回报 lifecycle)，kt-biome 的四个Terrarium 把它们完整跑过一轮。还在摸索的是惯用写法；看下面的 [如实定位](#如实定位) 与 [ROADMAP](../../ROADMAP.md)。

## 设置结构

```yaml
terrarium:
  name: swe-team
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md    # 该团队专属的派工 prompt，跟Terrarium放一起
  creatures:
    - name: swe
      base_config: "@kt-biome/creatures/swe"
      output_wiring: [reviewer]            # 决定性边：每次 swe 回合结束 → reviewer
      channels:
        listen:   [tasks, feedback]
        can_send: [status]
    - name: reviewer
      base_config: "@kt-biome/creatures/swe"
      system_prompt_file: prompts/reviewer.md   # reviewer 角色用 prompt 表达，不另开Creature
      channels:
        listen:   [status]
        can_send: [feedback, results, status]  # 条件式：通过 → results、退件 → feedback
  channels:
    tasks:    { type: queue }
    feedback: { type: queue }
    results:  { type: queue }
    status:   { type: broadcast }
```

- **`creatures`** — 跟独立Creature一样的继承与覆盖规则。多出 `channels.listen` / `channels.can_send`，加上选用的 `output_wiring`。
- **`channels`** — `queue` (每则消息一个消费者) 或 `broadcast` (每个订阅者都收到)。
- **`output_wiring`** — 每只 Creature的目标清单，回合结束时自动收到这只Creature的输出。见 [输出接线](#输出接线)。
- **`root`** — 选用的面向用户Creature，坐在 Terrarium 外面；见下。kt-biome 不附通用 `root` Creature — 每个Terrarium自带 `prompts/root.md`。

频道描述的简写：

```yaml
channels:
  tasks: "work items the team pulls from"
```

字段参考：[配置参考](../reference/configuration.md)。

## 自动建立的频道

执行期一定会建：

- 每只 Creature一条以它名字命名的 `queue` 频道，让别人可以 DM 它。
- 设了 `root` 时，多一条 `report_to_root` queue。

这些不用自己宣告。

## 频道怎么接起来

每只 Creature的每个 `listen:` 项目，执行期会注册一个 `ChannelTrigger`，消息到的时候叫醒控制器。System prompt 会收到一小段拓扑描述，告诉Creature自己 listen 哪些频道、可以送到哪些。

`send_message` 工具会自动加上去；Creature用 `channel` 与 `content` 参数调用它。默认 bracket 格式长这样：

```
[/send_message]
@@channel=review
@@content=...
[send_message/]
```

Creature如果用 `tool_format: xml` 或 `native`，调用的样子不一样、语意相同。见 [编写 Creature — 工具格式](creatures.md)。

## 跑Terrarium

```bash
kt terrarium run @kt-biome/terrariums/swe_team
```

旗标：

- `--mode tui|cli|plain` (默认 `tui`)
- `--seed "Fix the auth bug."` — 往 seed 频道注入一则启动消息
- `--seed-channel tasks` — 指定哪条频道收 seed
- `--observe tasks review status` / `--no-observe` — 频道观察
- `--llm <profile>` — 覆盖每只 Creature的 LLM
- `--session <path>` / `--no-session` — 持久化

TUI 模式会有多 tab 介面：root (有的话)、每只 Creature、被观察的频道。CLI 模式会把第一只 Creature (或 root) 挂到 RichCLI 上。

只看Terrarium信息不执行：

```bash
kt terrarium info @kt-biome/terrariums/swe_team
```

## Root agent 模式

Root 是一只独立Creature，挂了Terrarium 管理工具。它坐在Terrarium **外面**、从上面驱动里面：

- 自动 listen 每一条Creature频道。
- 收 `report_to_root`。
- 拿到Terrarium工具 (`terrarium_create`、`terrarium_send`、`creature_start`、`creature_stop`…)。
- 自动收到一段产生的「Terrarium概况」prompt，列出绑定团队的Creature与频道。
- Terrarium跑 TUI/CLI 时，它就是面向用户的介面。

想要一个单一对话介面时用 root；纯背景合作的流程就不用。

```yaml
terrarium:
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md   # 该团队专属的派工 prompt
```

kt-biome 不附通用 `root` Creature。每个Terrarium自己拥有 `root:` 区块与对应的 `prompts/root.md` — prompt 可以直接点名真实的团员 (「写程式 → 送到 `driver`」)，因为它住在它 orchestrate 的团队旁边。框架会自动提供管理工具组与拓扑概况。

设计理由请看 [concepts/multi-agent/root-agent 概念](../concepts/multi-agent/root-agent.md)。

## 执行期热插拔

从 root (通过工具) 或写程式：

```python
await runtime.add_creature("tester", tester_agent,
                           listen=["review"], can_send=["status"])
await runtime.add_channel("hotfix", channel_type="queue")
await runtime.wire_channel("swe", "hotfix", direction="listen")
await runtime.remove_creature("tester")
```

Root 用的对应工具：`creature_start`、`creature_stop`、`terrarium_create`、`terrarium_send`。

热插拔很适合临时补一个专员、又不用重启。既有频道会自动吸收新的 listener；新Creature会在它的 system prompt 看到自己的频道拓扑。

## 观察模式 (debug 用)

`ChannelObserver` 是任何频道上的非破坏性观察点。跟一般消费者不一样，observer 读消息不会跟 queue 消费者竞争。Dashboard 底下用这个；写程式的话：

```python
sub = runtime.observer.observe("tasks")
async for msg in sub:
    print(f"[tasks] {msg.sender}: {msg.content}")
```

`kt terrarium run` 的 `--observe` 会对清单上的频道挂 observer，在 TUI 里串流出来。

## 程序化Terrarium

```python
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.core.channel import ChannelMessage

runtime = TerrariumRuntime(load_terrarium_config("@kt-biome/terrariums/swe_team"))
await runtime.start()

tasks = runtime.environment.shared_channels.get("tasks")
await tasks.send(ChannelMessage(sender="user", content="Fix the auth bug."))

await runtime.run()
await runtime.stop()
```

串流、多租户、长期跑的场景，请用 `KohakuManager` 包一层。见 [程序化使用指南](programmatic-usage.md)。

## 输出接线

频道依赖 Creature **记得 ** 调用 `send_message`。对于那种确定性的 pipeline 边 — 「每次 coder 写完，runner 就要跑它写的东西」 — 框架提供另一条路： ** 输出接线 (output wiring)**。

Creature 在 config 宣告自己回合结束的输出要送去哪。每个回合边界，框架会对每个目标的事件伫列发一个 `creature_output` `TriggerEvent`。不用 `send_message`、不用 `ChannelTrigger`、中间也没频道。

```yaml
# terrarium.yaml 的 creature 区块
- name: coder
  base_config: "@kt-biome/creatures/swe"
  output_wiring:
    - runner                              # 简写 = {to: runner, with_content: true}
    - { to: root, with_content: false }   # lifecycle ping (只带 metadata)
  channels:
    listen: [reverts, team_chat]
    can_send: [team_chat]
```

完整字段结构在 [reference / configuration — output wiring](../reference/configuration.md#output-wiring)。重点属性：

- **`to: <creature-name>`** 指同一个 Terrarium 里的另一只 Creature。
- **`to: root`** 是魔术字符串 — 指向坐在 Terrarium 外面的 根代理。做 lifecycle ping 很好用；就算 root 没在 listen 频道也能看到。
- **`with_content: false`** 送过去的事件 `content` 是空的 — 纯粹是「回合结束了」的 metadata 讯号。
- **`prompt` / `prompt_format`** 客制接收端的 prompt-override 文字。

### 什么时候接线、什么时候用频道

以下情况用 **输出接线**：

- 这条边是决定性的 — 某只Creature的输出永远往下一站。
- 你要 lifecycle 观察，但又不想Creature自己记得调用 `send_message`。
- Pipeline 是线性的 (或是回圈型、但回圈回头仍然无条件)。

以下情况留在 **频道**：

- 这条边是条件式的。Reviewer 通过或退件；analyzer 保留或丢弃。接线不能分支，频道可以。
- 流量是广播 / status / team-chat — 选择性、多人观察。
- 你要的是 group-chat 形状：多人可送、多人可听。

同一个 Terrarium 里两种机制可以自由搭配。kt-biome 的 `auto_research` 在线性边 (ideator → coder → runner → analyzer) 用接线，在 analyzer 的保留/丢弃决定与 team-chat status 用频道。

### 接收端看到接线事件时会怎样

事件会落进目标Creature的事件伫列，走跟其他触发器一样的 `_process_event` 路径。TUI 上接收端的 tab 会照一般回合的样子渲染 (prompt 注入、LLM 文字、工具)。注册在接收端的插件通过既有的 `on_event` hook 看得到这个事件 — 没有新的插件 API。

## 如实定位

两种合作机制已经能涵盖今天大多数团队：频道 (工具 + 触发器，自愿) 与输出接线 (框架层、自动)。kt-biome 的Terrarium 把两个都跑过 — 确定性 pipeline 边用接线，条件式分支与 team-chat 流量用频道。

还在摸索的是惯用写法。Observer 面板与 TUI 对接线事件的呈现，比对频道流量薄。条件式边还是得走频道，因为接线不能分支 — 要不要加个小小的 `when:` filter，我们想通过实际使用慢慢弄清楚，而不是先设计出来。内容模式 (`last_round` 与 `all_rounds` 与 summary) 之后或许对想把草稿推理一起带著走的 pipeline 有用；目前不确定。开放问题整组在 [ROADMAP](../../ROADMAP.md)。

当一个 parent 可以自己拆解的时候， **子代理** (单一Creature内的垂直派工) 更单纯 — 对大多数「我只是想要 context 隔离」的直觉来说，这才是比较简单的答案。只有当你真的想要不同Creature各自合作、而且希望这些Creature还能保持可以独立执行的 config 时，才伸手去碰Terrarium。

## 疑难排解

- **团队卡住、没人传消息**。 最常见原因：寄件方靠 `send_message`，但 LLM 忘记调用。两种解：
 - 对确定性 pipeline 边加 `output_wiring:` — 框架不会忘。
 - 条件式边必须留在频道的话，就加强寄件方 prompt 对该频道的提醒。
 用 `--observe` 即时看频道流量。
- **Creature 没有对频道消息做出反应**。 确认 `listen` 有这个频道名字、`ChannelTrigger` 有注册 (`kt terrarium info` 会打印接线)。
- **Root 看不到 Creature 在做什么**。 两条路：把 `report_to_root` 加进该 Creature 的 `can_send` (走频道)；或把 `{to: root, with_content: false}` 加进它的 `output_wiring` (走框架层 lifecycle ping；就算 Creature 不调用 `send_message` 也会触发)。
- **接线目标没有收到内容**。 确认目标 Creature 在同一个 Terrarium、且正在跑。接线以 Creature 名称 (或魔术字符串 `root`) 解析；不存在或停掉的目标会被 log 下来然后跳过。
- **Creature 很多的时候启动很慢**。 每只 Creature 各自起自己的 LLM provider 与 trigger manager；启动时间大致随 Creature 数线性增加。

## 延伸阅读

- [Creatures 指南](creatures.md) — 每一条Terrarium entry 都是一只 Creature。
- [组合代数开发指南](composition.md) — 只需要小回圈、不需要整个Terrarium的时候，Python 端的替代方案。
- [程序化使用指南](programmatic-usage.md) — `TerrariumRuntime` + `KohakuManager`。
- [概念 / Terrarium](../concepts/multi-agent/terrarium.md) — Terrarium为什么长这样。
