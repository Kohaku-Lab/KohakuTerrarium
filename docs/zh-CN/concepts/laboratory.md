---
title: Laboratory 层
summary: KohakuTerrarium 如何跨越多台机器 —— 基于 WebSocket 的传输、自定义 packet 系统，以及让 Studio 与 Terrarium 把远端节点当作本地节点对待的透明化技巧。
tags:
  - concepts
  - laboratory
  - multi-node
---

# Laboratory 层

**Laboratory**（在代码库中是 `kohakuterrarium.laboratory`，非
正式名称为 "Lab"）是一个网络层，让一台 KohakuTerrarium 主机能够
协调跑在远端机器上的生物。它位于管理层（Studio）与运行时引擎
（Terrarium）之间，它的全部工作就是让框架的其他部分 *察觉不到*
网络的存在。

本文从端到端介绍其设计。如果你想了解日常的运维任务（运行
`kt serve --mode lab-host`、加入工作节点、选择面向节点的凭据），
请参见 [Laboratory 使用指南](../guides/laboratory.md)。

## 两套层级，一个心智模型

KohakuTerrarium 有两种运行模式；除了网络跳转之外，分层方式完全
相同。

### 独立模式

```
user-facing UI (web / CLI / TUI / desktop)
      │
      ▼
    Studio              ← 管理框架
   (catalog · identity · sessions · persistence · editors · attach)
      │
      ▼
    Terrarium           ← 运行时引擎：图、通道、热插拔
      │
      ▼
    Creatures           ← 真正的 Agent（LLM + 工具 + …）
```

单进程、没有网络、没有 import 任何 Lab 模块。这是 `kt serve`
默认启动的形态。

### 多节点（lab-host + lab-clients）

```
        ┌────────────────── HOST PROCESS ──────────────────┐
        │  user-facing UI                                  │
        │       │                                          │
        │       ▼                                          │
        │     Studio                                       │
        │       │                                          │
        │       ▼                                          │
        │  MultiNodeTerrariumService                       │
        │   (composes LocalTerrariumService + N remotes)   │
        │       │                                          │
        │       ▼                                          │
        │  HostEngine (Lab L1–L4)                          │
        └───────┬───────────────────────────────┬──────────┘
                │ wss://host:8100               │
                │                               │
        ┌───────▼─────── WORKER 1 ──────┐   ┌───▼────── WORKER N ──────┐
        │  ClientConnector              │   │  ClientConnector         │
        │     ├ TerrariumRuntimeAdapter │   │     ├ …adapters…         │
        │     ├ TerrariumEventsAdapter  │   │     │                    │
        │     ├ TerrariumAttachAdapter  │   │     ▼                    │
        │     ├ TerrariumFilesAdapter   │   │  Terrarium               │
        │     ├ TerrariumSessionAdapter │   │     │                    │
        │     ├ StudioDeployAdapter     │   │     ▼                    │
        │     ├ StudioCatalogAdapter    │   │  Creatures               │
        │     ├ StudioIdentityAdapter   │   └──────────────────────────┘
        │     └ IdentityCache           │
        │        │                      │
        │        ▼                      │
        │  Terrarium                    │
        │        │                      │
        │        ▼                      │
        │  Creatures                    │
        └───────────────────────────────┘
```

Studio 仍然只调用一个 `TerrariumService` Protocol。在 Protocol
背后，`MultiNodeTerrariumService` 把每个生物粒度的操作 fan-out
并路由到正确的节点。Studio 永远不会 import Lab。

主机进程也可以运行 Agent —— 一个 "coordination engine"：保留一
个本地 Terrarium，用于跨节点通道路由，以及在没有指定目标工作节
点时托管由 recipe 生成的生物。工作节点是完全相同的进程（同样的
`Terrarium` 类、同样的适配器、同样的 session store 布局）；它们
配置上唯一的差异是主动外连，而不是接受连接。

## 为什么用 WebSocket

Lab 的传输是普通的 WebSocket（生产环境中使用 `wss://`），不是
gRPC、不是裸 TCP，也不是 QUIC。三个原因：

1. **能原样穿越 Cloudflare / nginx / 企业代理。** 整个协议跑
   在单条 TCP/443 上即可，不需要额外的防火墙规则、也不需要单
   独的信令通道。
2. **浏览器原生支持。** Studio 的 web UI 与工作节点客户端使用
   同一份 wire 格式与同一份 envelope codec —— 浏览器 dashboard
   未来某个版本本身就可以充当 Lab 客户端，无需重新实现任何
   东西。
3. **天然双向、消息成帧。** L2 envelope 与一个 WebSocket binary
   frame 一一对应；不需要在字节流上再造一层消息边界。

WebSocket 并不是设计上的承重墙 —— [传输层](#l1-transport) 是一
个小型 Protocol，`InProcTransport` 也实现了它（每个测试都在
用）。换成 QUIC 或 Unix socket 就只是再写一个
`_internal/transport_*.py`。

## Packet 系统

两个 Lab 节点之间的每一个字节都被封装成一个 **envelope**
（信封）。这个 envelope 是自定义的（不是 protobuf、不是 gRPC），
原因很具体：我们需要直接搬运原始二进制 payload（文件包、
session 事件 blob、tokenizer 状态），不希望承受扁平 msgpack 设
计会带来的 base64 膨胀。

### Wire 格式（L2）

```
+------------------ envelope on the wire ------------------+
| 4 bytes  big-endian uint32        header_len             |
+----------------------------------------------------------+
| header_len bytes                  msgpack-encoded header |
|   { from, to, kind, stream_id, seq, flags,               |
|     payload_len, sig_len }                               |
+----------------------------------------------------------+
| header.payload_len bytes          raw payload            |
+----------------------------------------------------------+
| header.sig_len bytes              raw signature          |
+----------------------------------------------------------+
```

Header 是 msgpack（小、schema 灵活、快）。Payload 是任意字节
—— 由 L4 编解码器决定如何解释。

实现见 `src/kohakuterrarium/laboratory/_internal/envelope.py`。

### 四层

| 层 | 关注点 | 关键文件 |
|----|--------|----------|
| **L1** Transport | 节点之间的字节流（WebSocket 或 in-proc） | `_internal/transport_ws.py`、`_internal/transport_inproc.py` |
| **L2** Envelope | framing、路由元信息、签名 | `_internal/envelope.py` |
| **L3** Connection | 握手、心跳、寻址、成员关系 | `_internal/host.py`、`_internal/client.py`、`_internal/protocol.py` |
| **L4** Verbs | 面向用户的投递原语 + APP 命名空间 | `verbs.py`（`Channel`、`Topic`）、`_internal/app.py`（`AppMessage`） |

### Envelope 种类

| Kind | 用途 |
|------|------|
| `SEND` | 点对点投递（L4 `Channel.send`）—— 在订阅者之间负载均衡 |
| `BROADCAST` | pub-sub 扇出（L4 `Topic.publish`）—— 每个订阅者都收到一份副本 |
| `APP` | 结构化的应用消息：`{namespace, type, body}`，可选的 request/response 关联 |
| `ACK` | 对需要 ack 的 `SEND` 的确认 |
| `HELLO` / `WELCOME` / `HEARTBEAT` | 连接生命周期 |
| `CONTROL` | 框架内部（subscribe、register_creature、…） |

Studio 与 Terrarium 在 wire 上做的事几乎都是 **APP** envelope。
APP 携带命名空间（例如 `terrarium.runtime`、`studio.identity`、
`terrarium.session.sync`）、type（命名空间内的动词）以及一个
msgpack body。两端按命名空间注册 *扩展处理器*；分发表是它们唯
一需要达成一致的东西。

## 透明化目标 1：Studio 只看到一套系统

Studio 永远不知道某个生物是跑在进程内、还是远端机器上。
`TerrariumService` Protocol（`src/kohakuterrarium/terrarium/service.py`）
有 `add_creature`、`list_creatures`、`chat`、`connect` 这些方法。
有三个实现：

- `LocalTerrariumService` —— 直接调用进程内的 Terrarium。
- `RemoteTerrariumService` —— 把参数打包成 `terrarium.runtime`
  上的一个 APP 请求、发送、再把响应解包。每个已连接的工作节点
  对应一个实例。
- `MultiNodeTerrariumService` —— 持有一个 `LocalTerrariumService`
  加上每个工作节点一个 `RemoteTerrariumService`，按
  `creature_id → home_node` 注册表路由每个生物粒度的操作、对全
  局操作做 fan-out。

在 lab-host 模式下 Studio 持有那个组合实例。每个原本调用
`engine.add_creature(...)` 的 Studio 方法现在改为调用
`service.add_creature(..., on_node="worker-1")`。跳转消失了。

## 透明化目标 2：Terrarium 只看到一个引擎

通道和图拓扑也是单命名空间。一个 worker-1 上的生物调用
`send_channel("ch1", "hello")` 应该投递给每一个 listener ——
包括 worker-2 上的 listener —— 就像两个生物住在同一个进程里一
样。Lab 通过两个机制实现这一点：

- **跨节点 connect**（`terrarium/multi_node_replication.py`）。
  当用户调用 `service.connect(creatureA, creatureB)`、两个生物
  住在不同的工作节点上时，主机会：
  1. 在两个工作节点的图上都添加该通道对象。
  2. 在发送端工作节点上接好发送端的 send-side。
  3. 在接收端工作节点上接好接收端的 listen-side。
  4. 通过 `terrarium.broadcast` 交叉订阅，让发送端工作节点上
     的本地 send 扇出到接收端工作节点，并在那里 inject 路径
     把消息重放进本地通道注册表。
  5. 把这条链记录在 `service._cluster_links`（一个由
     `(node_id, graph_id)` 对组成的 `set[frozenset]`）里。
- **输出接线 forwarder**（`TerrariumOutputWireAdapter`）。指向
  其他工作节点上生物的输出接线目标，也通过 broadcast 适配器
  以同样的方式解析。

跨节点 connect 之后，两个工作节点的图就组成了一个 *集群* ——
一个跨机器分布的逻辑多生物图。主机的
`MultiNodeTerrariumService` 用集群集合对列表进行折叠（fold）
（`list_creatures` 显示并集；`list_channels` 按名称去重），所
以前端看到的是一个连通分量，即使每个工作节点都保留着自己的引
擎图。

热插拔的工作方式完全相同：`group_add_node`（特权生物可以调用
的工具）打到 runtime adapter，由它根据 recipe 是否指定目标节
点，决定就地生成、还是通过 service 路由到另一个工作节点。

## 会话：通过同步事件来镜像

Lab 中最独特的设计选择就是持久化的实现方式。

### 权威写入端 + 读侧镜像

每个运行中的生物都恰好有一个 **权威的** `SessionStore`（通过
KohakuVault 的 SQLite 文件）—— 就在托管它的那个工作节点上。
每个 session 文件只有一个写入者。生物产生的所有事件先落到那
个文件里。

工作节点 attach store 的同一刻，它还会安装一个
**`SessionEventTee`**（`session/sync.py`）。这个 tee：

1. 同步快照该 store 的 `meta` 并把它作为第一条 wire 消息入队。
2. 订阅该 store 的 append 回调。
3. 把每一个事件作为一条 `terrarium.session.sync` 命名空间下
   的 APP 消息（snapshot 用 type `meta`，每次 append 用 type
   `event`）发送给主机。

主机运行一个 **`SessionMirrorWriter`**，接收这些消息并把它们
写入它自己 session 目录下的一个 **mirror store**
（`<KT_CONFIG_DIR>/sessions/mirror/<graph_id>.kohakutr`）。这
个镜像是一个真正的 `SessionStore`，与工作节点上的那个相同，只
是以 append-only 方式打开，承接 wire 驱动的事件流。

Studio 的读 API（history、viewer、search、fork）都从镜像读，
而不是从工作节点读。镜像是本地 SQLite，所以翻阅一万个事件不
会每页都来一次 round-trip。

### 顺序与持久性

- Tee 使用一个每 session 一份的 outbound asyncio 队列。事件按
  append 顺序投递到主机。如果链接掉了，pump 会有界回退重试
  —— 事件被缓冲，不会丢失。
- 镜像写入器会先 apply meta 键（这样 `config_path` /
  `config_snapshot` 会先于任何事件落地），再随到达顺序追加事
  件。每个 key 的写入是独立的：单个失败的 key 不会中止其他
  写入。
- 主机的镜像文件是 best-effort 的。工作节点本地文件始终是
  resume 的真理来源。

### 为什么这样设计

事件级镜像而不是快照级镜像，有两个原因：

1. **实时读取。** Studio 的历史 viewer 一收到事件就能展示；
   没有 polling、没有秒级的最终一致性意外。
2. **断线生还。** 如果工作节点在对话中途掉线，主机仍然拥有
   到断线为止的每一个事件 —— Studio 继续响应历史查询 —— 而
   当工作节点重连时，镜像已经是最新的；tee 从下一个事件继续
   推送，无需 resync RPC。

代价是一份 session 同时存在于两个地方。我们始终把工作节点的
文件视为权威；镜像存在是为了读取方便，以及作为 resume 时推回
工作节点的磁盘镜像（见下文）。

### 为什么不分片 session

每个生物 / 图都只有一个文件。我们考虑过事件级 fan-out 到多个
镜像，但拒绝了，原因是：

- KohakuVault 的 SQLite append 已经很快（每事件约 50 µs）。
- 单文件简化了 fork / search / viewer 的代码路径。
- 镜像是忠实的副本；你可以直接 `cp` 走然后在任何节点上 resume。

## Resume：把磁盘镜像推回去

Resume 跑的还是它一直跑的那段 `engine.adopt_session(path)`
—— 但在多节点模式下，path 在主机上，而引擎在工作节点上。主
机负责架起这座桥：

1. 用户在 "Saved" 标签页里选一个 session，点击 **Resume on
   worker-1**。前端发送
   `POST /api/sessions/{sid}/resume {"on_node": "worker-1"}`。
2. 该路由打开镜像文件、checkpoint 任何内存中的写入
   （`mirror.checkpoint(sid)` flush SQLite WAL），读取原始字
   节，并把它们流式传给 worker-1 的 `terrarium.files` 适配
   器，scope 为 `config://resume/`。
3. 字节落到工作节点磁盘后，该路由调用 worker-1 的
   `terrarium.session.resume` 适配器，传入刚刚推过去的文件的
   绝对路径。
4. 工作节点的适配器调用 `engine.adopt_session(local_path)`，
   它会读取 meta，分发到单生物
   （`_resume_agent_into_engine`）或多生物
   （`_resume_terrarium_into_engine`）的重建流程，attach
   store，并启动每个被采纳的生物。
5. 工作节点的 `WorkerSessionAttacher` 安装一个新的
   `SessionEventTee`；resume 后的生物之后产生的事件流回主机
   镜像，就像它们一开始就在那里被生成一样。

### 为什么 meta 里要放 config 快照

对于 recipe 定义的生物，工作节点可以 `Agent.from_path(...)`，
因为 recipe 文件夹在工作节点的文件系统上存在。但是 inline 生
成的生物（SDK 用法，以及用户用 `--home-dir` 隔离工作节点磁盘
时的 recipe）经常在这台机器上根本没有可以加载的文件夹。为了
让这些生物能在任何节点上 resume，工作节点的
`_ensure_store_meta`（[`_worker_session.py`](_worker_session.py-line)）
会通过 `pack_agent_config` 捕获完整的 `AgentConfig` 并把它存
在 `meta["config_snapshot"]` 下。Resume 路径
（`session/resume.py::_rebuild_agent`）在文件夹存在时优先用
`config_path`，否则 fallback 到 `unpack_agent_config(snapshot)`。

### 单生物 resume（CF-6 基线）

一个工作节点上的一个生物：

```
host: read mirror bytes
host: terrarium.files.write_stream → worker (config://resume/<sid>.kohakutr)
host: terrarium.session.resume(path) → worker
worker: engine.adopt_session(path)
        → resume_into_engine → _resume_agent_into_engine
        → resume_agent (reads meta, rebuilds Agent, injects saved
          conversation + scratchpad + triggers, attaches store)
        → add_creature(rebuilt_creature)
        → attach_session(graph_id, store)
worker: WorkerSessionAttacher.attach(creature_id)
        → installs SessionEventTee for future events
host: register session in _meta; respond with the synthesized Session handle
```

### 多生物图 resume（CF-6 cluster）

对于跨多个工作节点的集群 —— 每个工作节点托管该集群连通分量
的一部分 —— 用户传入一个 `members` 列表：

```http
POST /api/sessions/{primary_sid}/resume
{
  "on_node": "worker-1",
  "members": [
    {"sid": "graph_abc", "on_node": "worker-1"},
    {"sid": "graph_def", "on_node": "worker-2"}
  ]
}
```

该路由会：

1. 校验每一个指名的工作节点都已连接（避免推到一部分、在另一
   部分失败，留下半 resume 的集群）。
2. 对每一个成员：针对该成员的 `(sid, on_node)` 跑一遍单生物
   resume 流程。
3. 在每个成员都重新跑起来之后，**重新发起
   `service.connect`**，针对集群 meta 中编码的每一条跨节点链
   接，让 `_cluster_links` 被重新填充，通道发送的扇出方式与
   关闭之前完全一致。
4. 返回带 `cluster_members` 的合并后的 `Session` handle。

集群成员列表会在 `stop_session` 时持久化到每个成员的镜像
meta 里，因此一个集群可以从任何成员的保存文件自动发现（当省
略 `members` 时，该路由就会这么做）。

**这一切由 `tests/e2e/test_multinode_journey.py` 中的步骤 *32g
CF-6 cluster resume* 端到端测试**：它在 w1 上生成 alpha、在
w2 上生成 bravo，在它们之间建立一条跨节点通道，驱动跨桥的聊
天流量，删除两个 active session，然后通过 members API resume，
并验证 `_cluster_links` 已被重新填充、聊天仍然正确路由。

### 运行时拓扑变更：快照 + 回放

Recipe（`terrarium.yaml`）描述图启动时的拓扑。在 recipe 加载
**之后**由用户（或特权工具）追加的一切 —— 通过
`service.add_channel` 增加的通道、通过 `service.connect` 增加
的接线、通过 `disconnect` / `unwire` 做的移除 —— 只存在于引
擎内存中的 `GraphTopology`。如果不做持久化，每一次 close +
resume 都会丢失。

引擎在每一次变更（`add_channel`、`remove_channel`、`connect`、
`disconnect`、`wire_creature`、`unwire_creature`）之后，把当
前拓扑的*完整快照*写入 `store.meta["runtime_topology"]`。形
状是：

```
{
    "channels":     [{"name": str, "description": str}, ...],
    "listen_edges": {creature_id: [channel_name, ...]},
    "send_edges":   {creature_id: [channel_name, ...]}
}
```

Resume 时，`_resume_terrarium_into_engine` 先重建 recipe 描述
的拓扑，然后调用 `topology_snapshot.replay(engine, sid)`，把
保存的快照里尚未出现在图中的每一个通道 + 接线都加进去。因
为快照是*完整的*（不是 delta log），用户的移除也被反映出来
—— 任何被用户移除的东西就是不在快照里。

实现：`src/kohakuterrarium/terrarium/topology_snapshot.py`。
测试：`tests/integration/test_runtime_topology_resume.py`。

### 当前已知的限制

| 场景 | 状态 |
|------|------|
| 1 工作节点上 1 生物 | ✅ 已测试（journey 32d） |
| coordination engine 上的 recipe 生成的多生物图 | ✅ 使用标准的 `_resume_terrarium_into_engine` |
| N=2 工作节点、每个 1 生物、跨节点桥接的集群 | ✅ 已测试（journey 32g / CF-6） |
| 3+ 工作节点的集群 | ⚠ 未测试（机制相同，只是成员更多） |
| 每个工作节点带多个生物的集群 | ⚠ 未测试但应该可以工作 —— 每个工作节点的 resume 各自独立地重建自己的图 |
| recipe 文件内的每生物 `on_node` | ❌ 不支持 —— recipe schema 没有 node 字段。请通过逐个 `add_creature(on_node=…)` + `service.connect` 手动组合 |
| 目标工作节点离线时 resume | ❌ 返回 404 并带上缺失的工作节点名 —— 运维需要先重连 |

## Identity：本地优先

LLM 凭据是 per-process 的，而不是 per-cluster 的。1.5.x 的默
认行为是 **本地优先**：

1. 工作节点的 `IdentityCache.sync_api_key(provider)` 先读取
   工作节点自己的 `<KT_CONFIG_DIR>/api_keys.yaml` 与 provider
   的环境变量。
2. 只有 miss 时才会 fallback 到主机最近一次通过
   `studio.identity` 推过来的内容。
3. Codex OAuth token（`<KT_CONFIG_DIR>/codex-auth.json`）也一
   样 —— 本地优先、主机其次。**Codex token 必须在本地**，因
   为 OAuth refresh 是进程绑定的：试图在工作节点进程里使用主
   机的 token 永远会让用户被重新提示。

`--home-dir` 旗标（`kt serve`、`kt lab-client`）会设置
`KT_CONFIG_DIR`，所以每个工作节点都能在磁盘上携带自己独立的
凭据存储。

在 Settings → Providers 里，用户通过 **Manage on:** 选择正在
编辑哪个节点的凭据存储。在选中某个工作节点时保存一个 key，会
通过 Lab APP 把写入路由到该工作节点的
`StudioIdentityAdapter`，由它持久化到工作节点的本地文件。
Codex login 也是一样 —— 在选中工作节点时点击 **Codex
login**，会在 *那个工作节点上* 跑 OAuth 流程，所以浏览器在
工作节点机器上打开，最终的 token 落在工作节点的磁盘上。

## 文件、部署与沙箱

- **`terrarium.files`** —— 通过 Lab APP 的 scope 受限文件
  IO。五个 scope：`workspace://<creature>`、`memory://<creature>`、
  `package://<name>`、`recipe://<id>`、`config://`。>512 KB
  payload 流式读写；幂等原子 commit（被采纳的 SessionStore 持
  开的目标文件不会被重写 —— 见 `_op_write_commit`）。
- **`studio.deploy`** —— `push_creature_bundle`：遍历一个生物
  文件夹，计算每文件 SHA，通过 `terrarium.files` 推送，原子
  rename 到 `recipe://<name>/...`。重复推送通过哈希检查保持
  幂等，所以已经有该 recipe 的工作节点不会再下载一次。
- **`terrarium.pty`** —— 把工作节点的 shell session 代理到主
  机侧的 WebSocket。前端的 terminal 面板针对远端生物的工作目
  录的运作方式毫无变化。
- 路径形式的 `add_creature("./my-creature/")` 如果工作节点的
  文件系统看不到该路径就会被拒绝。请先用 `studio.deploy` 推
  bundle，然后用工作节点侧的 `recipe://` 路径生成。

## 集群范围的折叠

用户在 dashboard 里打开一个 session，看到的是 **一个** 聊天和
**一份** 生物列表，即使该集群横跨三个工作节点。折叠发生在两
个地方：

- **列表**（`studio.sessions.cluster_fold`）。已保存 session
  列表、active session 列表、运行时图快照都会把同一集群的成员
  合并到主 sid 之下（字典序最小的成员 sid = 主 sid）。前端永
  远不会看到一个集群对应 N 个独立的 session。
- **运行**（聊天 WebSocket、通道、记忆、组工具）。每个生物的
  定位都通过 `_home` 注册表解析，因此即使调用者按名字寻址，
  `chat` 与 `inject_input` 也能找到正确的工作节点。

## 一眼速览的术语表

| 术语 | 含义 |
|------|------|
| **Lab / Laboratory** | `kohakuterrarium.laboratory` 包；网络层。 |
| **Host / 主机** | 运行 `kt serve --mode lab-host` 的进程。拥有 Studio + `HostEngine`。也可能通过 coordination engine 托管 Agent。 |
| **Worker / 工作节点** | 运行 `kt lab-client` 的进程。托管生物，通过 Lab 适配器把它们暴露出来。 |
| **Node / 节点** | 主机或工作节点 —— 任何说 Lab 协议的进程。通过 `node_id`（`_host` 或 client 的 `--name`）寻址。 |
| **Adapter / 适配器** | 实现一个或多个 APP 命名空间的类（例如 `TerrariumRuntimeAdapter` 服务 `terrarium.runtime` 命名空间）。 |
| **`TerrariumService`** | Studio 调用的 Protocol。三个实现：`Local`、`Remote`、`MultiNode`。 |
| **Cluster / 集群** | 一组跨节点连接的图。由 `MultiNodeTerrariumService._cluster_links` 跟踪。从用户视角看就是一个逻辑 session。 |
| **Mirror / 镜像** | 主机侧的工作节点 session 文件副本，由 `SessionEventTee` → `SessionMirrorWriter` 填充。所有读 API 的来源。 |
| **Cluster fold / 集群折叠** | 对 `_cluster_links` 做并查集，把每个成员 sid 映射到集群的主 sid；在前端列出或寻址一个集群的所有地方都会用到。 |

## 延伸阅读

- [Laboratory 使用指南](../guides/laboratory.md) —— 如何实际
  运行它。
- [会话](../../guides/sessions.md) —— 持久化的基础知识（单
  节点）。
- [Terrarium](./multi-agent/terrarium.md) —— Lab 包裹的引擎。
