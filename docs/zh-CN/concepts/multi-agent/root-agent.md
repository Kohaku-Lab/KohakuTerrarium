---
title: Root 代理
summary: 位于Terrarium之外、代表用户的 Creature——面向用户的介面、管理工具组、拓扑感知。
tags:
  - concepts
  - multi-agent
  - root
---

# Root 代理

## 它是什么

**Root 代理 (root Agent)** 是一只坐落在Terrarium*外部*、在其中代表用户的 Creature。从结构上说，它其实就只是另一个 Creature：同样的配置、同样的模块、同样的生命周期。让它成为「root」的地方在于：

1. 它被放在团队之外——用户和 root 对话；root 再和Terrarium对话。
2. 它会自动获得 **Terrarium管理工具组**（`terrarium_create`、`terrarium_send`、`creature_start`、`creature_stop`、`creature_status`、`terrarium_status`、…）。
3. 它会自动监听所有共享频道，并接收专用的 `report_to_root` 队列。

## 为什么它存在

裸的Terrarium是无头的——只有多只Creature透过频道协作，没有人在前面驾驶。这对某些环境式工作流是可行的；但若要互动式使用，人类需要一个单一接口。root 就是那个接口。

原则上，你也可以用一只普通Creature再加手动连线来做到这件事；但每次都要把工具组与监听连线配好，实在很麻烦。把「root」做成Terrarium配置里的一等位置，就能消掉这些模板代码。

## 我们怎么定义它

```yaml
terrarium:
  root:
  base_config: "@kt-biome/creatures/general"
  system_prompt_file: prompts/root.md  # 团队专用的委派提示词
  controller:
  reasoning_effort: high
  creatures:
  -...
  channels:
  -...
```

任何在 Agent 配置中合法的东西，放进 `root:` 里也都合法。继承（`base_config`）的运作方式也一样。

撰写上的注意事项：kt-biome**不会** 附带一个通用的 `root` Creature。每个Terrarium都应该有自己的 `root:` 区块，以及放在同一处的 `prompts/root.md`，内容要知道自己面对的是哪个团队——像是「coding → send to `driver`」就会比「coding → send to the swe creature」来得自然。其他事情由框架处理。

无论你在 root 的配置里写了什么，运行时都会对它做三件事：

- 把管理工具组（`terrarium_create`、`terrarium_send`、`creature_start`、`creature_stop`、`creature_status`、`terrarium_status`、…）注入它的 registry。
- 自动监听每个 Creature频道，让它看见整个团队的活动。
- 自动产生一段「Terrarium感知」提示词区块，列出所绑定Terrarium中的 Creature与频道，并附加到 root 的 system prompt。
- 让 root 成为用户直接互动的那一个（TUI / CLI / web）。

你的 `prompts/root.md` 只需要负责委派风格 / 个性——拓扑感知由框架提供。

## 我们怎么实现它

`terrarium/factory.py:build_root_Agent` 会在*所有Creature建立完成之后*被调用。它会用共享环境建立 root（这样管理工具才能看见Creature与频道）、把 `TerrariumToolManager` 注册进它的 registry，并把输出接回用户 transport。

root 会先被建立，但不会立刻启动，直到用户真的开始和Terrarium互动为止——这让 `kt terrarium run` 可以在 root 醒来前先显示团队状态。

## 因此你可以做什么

- **面向用户的指挥者**。 用户对 root 说：「叫 SWE 修 auth bug，然后再叫 reviewer 批准它。」root 会透过频道送消息，并监控 `report_to_root` 以得知完成情况。
- **动态团队建构**。 root 可以根据当前任务 `creature_start` 新的专家，再在完成后 `creature_stop` 它们。
- **Terrarium启动器**。 一个 root Agent 本身也可以透过 `terrarium_create` 建立并管理*其他*Terrarium。
- **可观测性的枢纽**。 因为 root 会自动监听所有东西，它自然就是执行摘要插件、告警规则等工作的最佳位置。

## 不要被它框住

没有 root 的Terrarium完全合理——像是无头 pipeline、cron 驱动的协调、批次作业。root 只是为互动式使用提供的便利。而且 root 依然「只是一个 Creature」——任何能套用在普通Creature上的模式（互动型子 Agent、插件、自订工具），一样都能套用到 root 身上。

## 另见

- [Terrarium](terrarium.md) —— root 所叠加其上的那一层。
- [多 Agent概览](README.md) —— root 在整个模型中的位置。
- [reference/builtins.md — terrarium_* tools 参考](../../reference/builtins.md) —— Terrarium管理工具组。
