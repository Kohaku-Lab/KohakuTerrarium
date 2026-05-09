---
title: 词汇表
summary: 文件里用到的术语的白话解释。
tags:
  - concepts
  - glossary
  - reference
---

# 词汇表

这一页是给你在文件中间看到某个词卡住时的查找表。每一条都指向完整的概念文件。

## Creature / Creature

一个独立的 Agent。KohakuTerrarium 的第一等抽象。一个 Creature有控制器、工具、触发器、(通常有的) 子 Agent、输入、输出、会话、以及选用的插件。它可以单独执行，也可以放进Terrarium里。完整说明：[什么是 Agent](foundations/what-is-an-agent.md)。

## Controller / 控制器

Creature内部的推理回圈。从事件队列取事件、请 LLM 响应、派发返回的工具与子 Agent调用、把它们的结果当成新事件喂回去、决定是否继续。它不是「大脑」 — LLM 才是大脑；控制器是让 LLM 在时间轴上运作的那层回圈。完整说明：[控制器](modules/controller.md)。

## Input / 输入

外界把用户消息交给Creature的方式。实际上就是一种特殊的触发器 — 标记为 `user_input` 的那种。内建的有 CLI、TUI、以及 `none` (纯触发器驱动的 Creature)；音频/ASR 由 opt-in 的自定义模块提供。完整说明：[输入](modules/input.md)。

## Trigger / 触发器

任何不需要用户输入就可以把控制器叫醒的东西。计时器、idle 侦测、webhook、频道 listener、监控条件都是触发器。每个触发器会把 `TriggerEvent` 推到Creature的事件队列。完整说明：[触发器](modules/trigger.md)。

## Output / 输出

Creature向外界说话的方式。一个路由器接收控制器产生的一切 (文字 chunk、工具活动、token 用量)，然后分发到一个或多个 sink — stdout、TTS、Discord、文件。完整说明：[输出](modules/output.md)。

## Tool / 工具

LLM 可以带参数调用的具名能力。shell 指令、文件编辑、网页搜寻。工具也可以是消息总线、状态 handle、或一个嵌套 Agent — 框架不管调用之后背后做什么。完整说明：[工具](modules/tool.md)。

## Sub-Agent / 子 Agent

由父Creature为某个有界任务派生出来的嵌套Creature。有自己的上下文、(通常) 是父代理工具的子集。概念上也是一种工具 — 从 LLM 的角度看，调用子 Agent和调用任何工具没有两样。完整说明：[子 Agent](modules/sub-agent.md)。

## TriggerEvent

所有外部讯号抵达Creature时共享的那一个信封。用户输入、计时器触发、工具完成、频道消息、子 Agent输出 — 全部都变成 `TriggerEvent(type=..., content=...,...)`。一个信封、一条代码路径。完整说明：[组合一个 Agent](foundations/composing-an-agent.md)。

## Channel / 频道

具名的广播管道。每一个订阅者都会收到任何送出的消息 ——
[图](#graph--图)层级没有 queue / consume 的语意。频道活在 Creature 的
私有 session 或图的共享 environment 里。一个 `send_message` 工具加上
`ChannelTrigger` 就是跨 Creature 通讯的方式。完整说明：[频道](modules/channel.md)。

## Output wiring / 输出接线

框架层级的配置，把Creature回合结束的输出自动送到指定的目标。在Creature配置里用 `output_wiring:` 宣告；每一个回合结束时，框架把一个 `creature_output` TriggerEvent 直接推进指定的目标Creature的事件队列。不需要调用 `send_message`、也不经过频道 — 它走的是和其他触发器一样的事件路径。**确定性的 pipeline 边** 用输出接线；条件性、广播、观察类的流量留给频道。完整说明：[Terrarium使用指南 — 输出接线](../guides/terrariums.md#output-wiring)。

## creature_output (事件型别)

框架在每个 `output_wiring` entry 的回合结束时发出的 TriggerEvent 型别。context 带着 `source`、`target`、`with_content`、`source_event_type`、以及每个来源Creature独立累加的 `turn_index`。目标Creature上注册的插件会透过正常的 `on_event` hook 收到它。

## Session / 会话

每个 Creature的 **私有** 状态：scratchpad、私有频道、TUI 参照、正在跑的 job 的 store。序列化到 `.kohakutr` 文件。一个Creature实例对应一个会话。完整说明：[会话与环境](modules/session-and-environment.md)。

## Environment / 环境

整个Terrarium**共享** 的状态：共享频道 registry 加上选用的共享 context dict。Creature预设私有、共享需明确 opt-in — 它们只看得到自己明确 listen 的共享频道。完整说明：[会话与环境](modules/session-and-environment.md)。

## Scratchpad / 草稿区

Creature session 里的 key-value store。跨回合存活；用 `scratchpad` 工具读写。适合当作工作记忆，或合作中的工具之间的汇合点。

## Plugin / 插件

修改模块之间 **连接方式 ** 的代码 — 不是 fork 某个模块。两种 ** ：prompt 插件 ** (为 system prompt 贡献内容) 与 **lifecycle 插件** (挂在 `pre_llm_call`、`post_tool_execute` 这类 hook)。`pre_*` hook 可以抛 `PluginBlockError` 来终止操作。完整说明：[插件](modules/plugin.md)。

## Skill mode / Skill 模式

配置旋钮 (`skill_mode: dynamic | static`)，决定 system prompt 要不要一开始就放上完整的工具说明 (`static`，比较大) 或只放名字加一行描述、等 Agent 需要时用 `info` 框架指令扩展 (`dynamic`，比较小)。纯粹的取舍；其他行为没变。完整说明：[提示词组合](impl-notes/prompt-aggregation.md)。

## Framework commands / 框架指令

LLM 在一个回合中可以发出的行内指示，用来和框架沟通而不发动一次完整的工具 round-trip。它们和工具调用 **用同一套语法家族 **— Creature配置的 `tool_format` (bracket / XML / native) 是哪一种，它们就长什么样。「指令」这个词指的是 ** 意图** (和框架对话，而不是执行工具)，不是说它有另一套语法。

预设 bracket 格式里：

- `[/info]工具或子 Agent名[info/]` — 按需加载某个工具或子 Agent的完整文件。
- `[/read_job]job_id[read_job/]` — 读取执行中或已完成的背景 job 输出 (body 支持 `--lines N` 与 `--offset M` 旗标)。
- `[/jobs][jobs/]` — 列出目前正在执行的背景 job (附 id)。
- `[/wait]job_id[wait/]` — 阻塞目前回合直到某个背景 job 完成。

指令名和工具名共享命名空间；「读取 job 输出」之所以叫 `read_job` 而不是 `read`，是为了避免和 `read` 文件读取工具撞名。

## Studio

[Terrarium](#terrarium--terrarium) 引擎之上的管理框架。一个 Python 类
（`kohakuterrarium.Studio`），透过六个命名空间 ——`catalog`、`identity`、
`sessions`、`persistence`、`editors`、`attach`—— 暴露每个 UI 与自动化脚
本本来都得自己重做的事：套件搜寻、LLM 配置档与 API key、运行中 session
的生命周期、保存的 session 的 resume / fork / export、工作区生物 / 模块
CRUD、attach policy 公告。网页 dashboard、桌面 app、`kt` CLI 与你自己的
Python 代码全都委派给 Studio，而不是各自重新实作。Studio **不是** UI；
dashboard 是它众多 adapter 之一。完整说明：[Studio](studio.md)。

## Terrarium / Terrarium

托管行程内所有运行中 Creature 的运行时引擎。一只独立的 Agent 就是引擎
里的 1-creature [图](#graph--图)；多创造物团队则是用频道连起来的连通
图。引擎拥有 creature CRUD、channel CRUD、输出接线、[热插拔](#hot-plug--热插拔)、
以及在图变更时跟着走的拓扑 + session 记账（[自动分裂 / 自动合并](#auto-split--auto-merge--自动分裂--自动合并)）。
它**不**执行 LLM、也没有自己的推理回圈 —— 那些都活在 Creature 里。它**真正
决定**的是结构：哪些 Creature 共享一个连通分量、哪个 session store 撑住
哪个图、每个回合结束的输出该送往何处。Creature 不知道自己在 Terrarium
里；同样的配置仍然可以独立执行。完整说明：[Terrarium](multi-agent/terrarium.md)。

## Recipe / 配方

把一个全新的 [Terrarium](#terrarium--terrarium) 引擎填入特定多创造物
设定的 YAML 配置档。引擎本身永远存在；配方只是「加入这些 Creature、
宣告这些频道、接好这些边、可选地把一只提升为 [root](#root--root-关键字)」
的指令序列。配方在 resume 时是真理来源 —— 当一份保存的多创造物 session
被开起来时，引擎会从 session metadata 里记的 recipe 路径重建拓扑，而
**不是**用一份冻结的图快照。

## Graph / 图

[Terrarium](#terrarium--terrarium) 引擎里的一个连通分量：透过频道连起
来的一组 Creature。两只无关的 Creature 各自处于不同的图；在它们之间画
一条频道会把两个图合并（并合并两边的 session 历史）。把两半之间最后
一条频道拔掉则会把图分裂（并把历史复制到两边）。图是 session 的单位
—— 同一个图里的 Creature 共用同一个 `.kohakutr` 文件。完整说明：[Terrarium](multi-agent/terrarium.md)。

## Root / Root 关键字

terrarium 配方里的 `root:` 关键字，用来指明哪一个节点是图中代表用户的
[特权节点](#privileged-node--特权节点)。配方加载器会把它标记为特权、
开启一条 `report_to_root` 频道（其他每只 Creature 都被接线为可送往该
频道）、让它监听其他每一条频道，并把它挂载为面向用户的接口（TUI / CLI /
网页）。「root」是配置上的惯例，不是另一种运行时型别 —— 在运行时它就
是一只带有标准面向用户接线的特权节点。完整说明：[特权节点](multi-agent/privileged-node.md)。

## Privileged node / 特权节点

被授予[组工具](#group-tools--组工具)、可以变更所属图的 Creature：生成
或移除其他 Creature、绘制或删除频道、启动或停止成员。被
[`root:`](#root--root-关键字)指定的节点默认就是特权；配方可以在其他成
员上 inline 标记为特权（`privileged: true`）；引擎也接受在
add_creature 时传入 `is_privileged=True`。透过工具生成的工人 Creature
（经由 `group_add_node`）**不是**特权 —— 工人没被显式提权前不能再分叉
同侪。特权是运行时 Creature handle 的属性，与底层 agent 配置无关 ——
同一份配置可以在某个 terrarium 里以特权身份运行、在另一个里以非特权
身份运行。完整说明：[特权节点](multi-agent/privileged-node.md)。

## Group tools / 组工具

一组内建工具（`group_add_node`、`group_remove_node`、`group_start_node`、
`group_stop_node`、`group_channel`、`group_wire`、`group_status`、
`group_send`），用于从内部变更或检查一个[图](#graph--图)。仅注册在
[特权节点](#privileged-node--特权节点)上。它们合在一起就是运行时的
「图编辑器」，让 LLM 驱动的特权节点在执行中演化团队 —— 每一次变更都
会发出 `EngineEvent`，让 observer 与运行时提示词保持同步。完整说明：
[builtins 参考](../reference/builtins.md)。

## Hot-plug / 热插拔

在运行中的 [Terrarium](#terrarium--terrarium) 里加入或移除 Creature、
频道、接线边，**不需要重启**。引擎处理记账：新成员的触发器注入与持久化
绑定；被移除的成员的触发器拆除以及任何
[自动分裂](#auto-split--auto-merge--自动分裂--自动合并)。可以透过命令式
API（`Terrarium.add_creature`、`connect`、`disconnect`）或由特权节点
呼叫的[组工具](#group-tools--组工具)使用。

## Auto-split / Auto-merge / 自动分裂 / 自动合并

引擎对会影响连通性的拓扑变更的反应。当一次 connect 跨越两个图时，引擎
会合并它们 —— 联集 environment、把两个 session store 复制到一个合并后
的 store（meta 里的 `parent_session_ids` 记下血缘）。当一次 disconnect
或 Creature / 频道的移除切断了两半之间的唯一路径时，引擎会分裂图 ——
为每一边分配新的 environment、把频道触发器对着新的 env 重新注入、把
session store 复制到每一边。所有记账都自动完成；observer 在
`EngineEvent` 里看到新的图 id 出现。

## Package / 套件

一个可安装的数据夹，装着Creature、Terrarium、自订工具、插件、LLM 预设、Python 相依，并以 `kohaku.yaml` manifest 描述。透过 `kt install` 安装到 `~/.kohakuterrarium/packages/`。在配置和 CLI 里用 `@<pkg>/<path>` 语法参照。完整说明：[套件使用指南](../guides/packages.md)。

## kt-biome

官方 out-of-the-box 套件，内含好用的 Creature、Terrarium、范例插件。不是核心框架的一部分 — 是展示 + 起步点。请见 [github.com/Kohaku-Lab/kt-biome](https://github.com/Kohaku-Lab/kt-biome)。

## Compose 代数

一组小运算子 (`>>` sequence、`&` parallel、`|` fallback、`*N` retry、`.iterate` async loop)，用来在 Python 里把 Agent 串成 pipeline。这只是一层易用性糖衣，核心事实是 Agent 本来就是一等公民的 async Python 值。完整说明：[compose 代数](python-native/composition-algebra.md)。

## MCP

Model Context Protocol — 一个把工具暴露给 LLM 的外部协定。KohakuTerrarium 透过 stdio、streamable HTTP 或旧式 HTTP/SSE 连到 MCP 服务器、探索它们的工具、再用 meta 工具 (`mcp_call`、`mcp_list`…) 把它们暴露给 LLM。完整说明：[MCP 使用指南](../guides/mcp.md)。

## Compaction / 压缩

当上下文快满时，把旧的对话回合摘要掉的背景流程。非阻塞：控制器在 summariser 工作时继续执行，切换动作在回合之间原子地完成。完整说明：[非阻塞压缩](impl-notes/non-blocking-compaction.md)。

## 延伸阅读

- [核心概念首页](README.md) — 完整章节地图。
- [什么是 Agent](foundations/what-is-an-agent.md) — 把上面多数术语放在同一个上下文里介绍。
- [边界](boundaries.md) — 上面任何一项何时可以忽略。
