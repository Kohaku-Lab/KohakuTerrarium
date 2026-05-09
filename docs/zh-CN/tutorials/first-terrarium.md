---
title: 第一个生态瓶（Terrarium）
summary: 用频道和输出连线组合两个 Creature，再加入 root 提供交互入口。
tags:
  - tutorials
  - terrarium
  - multi-agent
---
# 第一个生态瓶（Terrarium）

你想让两个 Creature 协同工作：writer 先写，reviewer 再给出意见。你还想亲眼看到消息如何在它们之间来回流动。

完成这篇教程后，你会得到一个 terrarium 配置：其中包含两个 creatures、多个 channels，可以在 TUI 中运行，消息会从一个传到另一个。

**前置条件** ： 先看过 [第一个 Creature](first-creature.md)。你还需要安装好 `kt-biome`，并且已经能用 `kt run` 运行单个 Creature。

terrarium 是**运行时引擎**：它持有频道图、creature 生命周期、输出接线、以及在图变化时跟着走的拓扑 + session 记账。它本身不执行 LLM、也没有推理回圈 —— LLM 与推理都活在它内部的 creature 里。完整契约请参阅 [Terrarium 概念](../concepts/multi-agent/terrarium.md)。

## 第 1 步：创建文件夹

```bash
mkdir -p terrariums
```

terrarium 配置实际可以放在任意位置。通常会放在与 creatures 并列的 `terrariums/` 目录中。

## 第 2 步：编写 terrarium 配置

`terrariums/writer-team.yaml`：

```yaml
# Writer + reviewer team.
#   tasks    -> writer  -> review  -> reviewer
#                       <- feedback <- reviewer

terrarium:
  name: writer_team

  creatures:
    - name: writer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You are a concise writer. When you receive a message on
        `tasks`, write a short draft and send it to `review` using
        send_message. When you receive feedback, revise and resend.
      channels:
        listen:    [tasks, feedback]
        can_send:  [review]

    - name: reviewer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You critique drafts. When you receive a message on `review`,
        reply with one or two concrete improvement suggestions on
        `feedback` using send_message. If the draft is good, say so.
      channels:
        listen:    [review]
        can_send:  [feedback]

  channels:
    tasks:    "送给作者的待办工作"
    review:   "发给审阅者的草稿"
    feedback: "送回作者的审阅意见"
```

这套接线的作用如下：

- `listen` 会为 creature 挂上 `ChannelTrigger`。消息一旦到达这些 channel，creature 就会被唤醒并看到这条消息。
- `can_send` 列出了该 creature 的 `send_message` 工具允许写入哪些 channel。未列出的 channel 不能发送。
- Channels 只需要在 `channels:` 中定义一次（带可选的一行描述）。所有频道都是广播：每个 listener 都会收到每一次 send。

这里直接把 `system_prompt:` 写在配置中，是为了让教程能在一页内看完。实际长期使用时，更推荐使用 `system_prompt_file:`。

## 第 3 步：查看拓扑图（可选）

```bash
kt terrarium info terrariums/writer-team.yaml
```

这条命令会打印出有哪些 creatures、它们分别监听和发送哪些 channels，以及 channel 的定义。正式运行前先检查一遍会更稳妥。

## 第 4 步：运行起来

```bash
kt terrarium run terrariums/writer-team.yaml --mode tui --seed "write a one-paragraph product description for a smart kettle" --seed-channel tasks
```

TUI 打开后，每个 creature 都有一个标签页，每个 channel 也有一个标签页。`--seed` 会在启动时把提示词写入 `--seed-channel` 指定的 channel；默认是 `seed`，这里改成了 `tasks`。接下来 writer 会被唤醒，写出草稿并发送到 `review`；reviewer 随后被唤醒，给出修改意见并发到 `feedback`；writer 再次被唤醒并继续修改。

你可以在 channel 标签页中查看原始消息流，也可以在 creature 标签页中查看各自的推理过程。

## 第 5 步：把交接改成更稳妥的输出路由

channel 很适合处理条件式、可选式或广播式消息流。比如 reviewer 最终是“通过”还是“打回重写”，这种分支判断就很适合留在 channel 中。但 writer → reviewer 这条边其实是 **确定性的**：writer 每次一轮结束后，reviewer 都应该看到它刚写出的内容。继续依赖 writer 的 LLM 记得调用 `send_message("review", ...)`，就是这类拓扑最常见的故障点。

框架现在提供了一个更直接的方式：**输出路由**。你在 creature 配置中声明这条边，runtime 就会在回合结束时，直接向目标 creature 的事件队列写入一个 `creature_output` 事件，两边都不需要自己调用 `send_message`。

把 `terrariums/writer-team.yaml` 改成这样：

```yaml
terrarium:
  name: writer_team
  creatures:
    - name: writer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You write short product copy. You receive a brief on `tasks`
        and a critique on `feedback`. When you receive feedback, revise
        your draft based on it.
      output_wiring:
        - reviewer                # 每次 writer 回合结束 -> reviewer
      channels:
        listen: [tasks, feedback]
        can_send: []              # 不再需要自己往 `review` 发
    - name: reviewer
      base_config: "@kt-biome/creatures/general"
      system_prompt: |
        You are a strict reviewer. The writer's draft will arrive as a
        creature_output event. If the draft is good, send "APPROVED:
        <draft>" on `feedback`. If not, send specific revision requests
        on `feedback`.
      channels:
        listen: []                # writer 的输出通过 wiring 收到
        can_send: [feedback]      # reviewer 的决定是条件式的，继续使用 channel
  channels:
    tasks:    "送给作者的待办工作"
    feedback: "送回作者的审阅意见"
```

这次改动中最关键的点：

- writer 的 `output_wiring: [reviewer]` 取代了原先由 writer 主动向 `review` channel 发送消息的做法。
- `review` channel 这一整条边被删除，因为这段交接现在由框架自动完成。
- reviewer 仍然通过 `feedback` 这个 channel 回给 writer，因为“通过还是修改”本身就是条件分支，输出路由不会自动分支。

现在再运行一次，这个来回会稳定得多：即使 writer 忘记调用 `send_message`，输出路由也会在每轮结束时自动触发。

## 第 6 步：如果需要交互入口，再加 root（可选）

有了 channel 和输出路由，你已经拥有了一个可以自行协作的无头小团队。如果你还想要一个统一的对话入口——用户只和一个 Agent 对话，再由它驱动整个团队——那就再加一个 **root**：

```yaml
terrarium:
  name: writer_team
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md   # 这个团队专用的委派 prompt
  creatures:
    - ...
```

在 terrarium yaml 旁边新建一份 `prompts/root.md`。这里主要写委派风格和团队口吻即可；框架会自动补上一段团队拓扑说明，把有哪些 creatures、有哪些 channels 写进去，同时还会强制注入[组工具](../concepts/glossary.md#group-tools--组工具)（`group_add_node`、`group_status`、`group_channel`、`group_wire` 等），让 root 可以从图内管理团队。

这样一来，TUI 主标签页挂载的就是 root。你直接和 root 对话，再由 root 去驱动 writer 和 reviewer。更完整的模式说明请参阅 [特权节点概念](../concepts/multi-agent/privileged-node.md)。

## 你学到了什么

- terrarium 只负责接线，不负责思考。
- creatures 仍然各自独立；terrarium 只是规定谁能听见什么、谁能往哪里发送消息，以及谁在回合结束后把输出自动流向谁。
- 横向协作现在有两种机制，而且可以混合使用：
  - **channel** —— 适合条件分支、可选消息和广播。
  - **输出路由** —— 适合确定性的流水线边；每轮结束自动触发，不依赖 creature 自己记得发送。
- root 是可选的。做无头工作流时可以不要；如果想给用户一个统一入口，就加上它。

## 接下来可以看什么

- [Terrarium 概念](../concepts/multi-agent/terrarium.md) —— 介绍 terrarium 的边界与约定。
- [特权节点概念](../concepts/multi-agent/privileged-node.md) —— 由 `root:` 指定、面向用户的那个特权 creature。
- [Terrariums 指南](../guides/terrariums.md) —— 更偏实操的参考文档。
- [Channel 概念](../concepts/modules/channel.md) —— 广播语意、observers，以及 channel 如何跨模块工作。
