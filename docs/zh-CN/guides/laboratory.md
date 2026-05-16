---
title: Laboratory（多节点）
summary: 让 KohakuTerrarium 跨两台或更多机器运行 —— kt lab-host + kt lab-client、每工作节点凭据、程序化用法、多节点 terrarium 与 resume。
tags:
  - guides
  - laboratory
  - multi-node
  - serving
---

# Laboratory（多节点）

Laboratory 层让一个 **主机**（host）进程协调跑在远端 **工作节
点**（worker）机器上的生物。本指南是实战 how-to。设计上的来
龙去脉请见 [概念 → Laboratory](../concepts/laboratory.md)。

## 何时使用

在以下场景使用 lab-host 模式：

- 你希望生物跑在和 UI 不同的机器上（GPU 服务器、沙箱 VM、云
  节点）。
- 你需要每个生物拥有 **自己的** OAuth 登录（Codex、ChatGPT 订
  阅）—— OAuth 是进程绑定的，因此无法共享；工作节点上的本地
  优先 identity 模型意味着每个工作节点都能持有自己的 token。
- 你希望某个生物的文件系统操作（工作区文件、子进程 shell、
  MCP 服务器）落在和 dashboard 不同的主机上。

其他情况（单用户、单机器）请继续用 `kt serve` / `kt web` /
`kt app` —— 它们更简单。

## 启动主机

```bash
# 前台运行（推荐在配置阶段使用）
kt serve start --mode lab-host \
               --foreground \
               --lab-bind 0.0.0.0:8100 \
               --lab-token "$(openssl rand -hex 24)" \
               --home-dir ~/.kohakuterrarium-host

# 守护进程（生产环境，退出终端）
kt serve start --mode lab-host \
               --lab-bind 0.0.0.0:8100 \
               --lab-token <shared-secret> \
               --home-dir /var/lib/kohakuterrarium/host
```

旗标：

- `--mode lab-host` —— 在普通 web 栈之外，还接受工作节点连
  接。主机在 lab-host 模式下 **默认不运行任何生物**；每一次
  生成必须指定工作节点（否则 fallback 到仅 recipe 的
  coordination engine）。
- `--lab-bind host:port` —— 工作节点连过来的 WebSocket
  endpoint。请使用工作节点能访问到的 bind 地址；生产环境请
  放在带 TLS 卸载的 nginx / Cloudflare 后面。
- `--lab-token` —— 共享密钥。每个工作节点在 Hello 握手中出示
  这个；token 不匹配会被拒绝。绑定到非 loopback 地址时 **务
  必设置**。
- `--home-dir` —— 重新指定 `KT_CONFIG_DIR`。API key、OAuth
  token、LLM profile、MCP server、session 全部存在这里。省略
  时默认为 `~/.kohakuterrarium`。

Web UI 仍然在 `--host:--port`（默认 `127.0.0.1:8001`）上提供
服务，与独立模式完全一致；`--lab-bind` 是用于工作节点连接的
*第二个* 监听端口。

## 连接一个工作节点

在另一台机器上（或同一台机器的另一个 shell 里）：

```bash
kt lab-client \
  --host  wss://your-host.example/lab        \
  --token <same-shared-secret>               \
  --name  worker-gpu-1                       \
  --home-dir ~/.kohakuterrarium-workers/gpu1
```

旗标：

- `--host` —— `ws://` 表示明文、`wss://` 表示 TLS。如果你用
  Cloudflare 或 nginx 代理，这就是公共 endpoint；Lab 协议可
  以原样穿越支持 WebSocket 的代理。
- `--token` —— 必须与主机的 `--lab-token` 匹配。
- `--name` —— 主机用来识别这个工作节点的 node id。在已连接的
  工作节点中必须唯一。
- `--home-dir` —— **每工作节点的** 配置 home。给每个工作节点
  自己的目录，避免它们的 `api_keys.yaml`、Codex OAuth token、
  session 文件相互冲突。这是从工作节点使用 Codex 的唯一可靠
  方式。
- `--session-dir` —— 可选覆盖；默认为 `<home-dir>/sessions`。

工作节点连上后，主机会记录一条 CONTROL `register_creature`
trace，dashboard 的 site 选择器会出现一个新条目。

## 每工作节点的 provider 凭据

`Settings → Providers` 有一个 **Manage on:** 下拉框，用来选
择正在编辑哪个节点的凭据存储。

- **Host** —— key + Codex token 落在主机（lab-host 进程的
  `--home-dir`）。
- **某个工作节点名** —— key + Codex login 通过 Lab APP 路由
  到该工作节点；工作节点写到自己的
  `--home-dir/api_keys.yaml`，并启动自己的 OAuth 浏览器流程。

本地优先的查找意味着 `worker-gpu-1` 上的生物查找它的 OpenAI
key 时按这个顺序：

1. `worker-gpu-1` 的 `<--home-dir>/api_keys.yaml`
2. 工作节点上的 `OPENAI_API_KEY` 环境变量
3. 主机的 identity 存储（通过 Lab APP 的 `studio.identity` 命
   名空间）—— 仅在 (1) 和 (2) 都 miss 时。

特别地对 Codex 而言：OAuth refresh token 是进程绑定的，所以
**Codex 必须在使用它的那个工作节点上登录**。主机无法以可在
refresh 后继续生效的方式与工作节点共享 Codex token。

## 在工作节点上生成生物

### 从 UI

在 dashboard 的 "New creature" 模态框中，**Site** 选择器会显
示每个已连接的工作节点以及 `Host`。选择一个工作节点，正常配
置，点 Spawn。前端会发出：

```http
POST /api/sessions/active/creature
{
  "config_path": "/abs/path/to/creature.yaml",
  "on_node": "worker-gpu-1"
}
```

（在 lab-host 模式下，`start_creature` 要求 `on_node` ——
在主机上生成会被拒绝，因为主机不运行 Agent。）

### 从 HTTP API

每个 session / 拓扑 endpoint 都接受 `on_node` 用于新生成，并
在生成后通过 home 注册表路由每生物粒度的操作。

```bash
# 在 worker-gpu-1 上生成一个 recipe 定义的生物
curl -X POST http://localhost:8001/api/sessions/active/creature \
     -H 'Content-Type: application/json' \
     -d '{"config_path": "/home/user/creatures/researcher",
          "on_node": "worker-gpu-1"}'
```

### 程序化

程序化使用面有两个不同的入口；按照你要构建的东西挑一个：

#### A) 你**身处**运行中的 `kt serve --mode lab-host` 内部

例如：自定义的 HTTP 路由、插件、或者从同一进程派生出的后台任
务。使用 FastAPI 的依赖注入来取得 active service：

```python
from fastapi import Depends, APIRouter

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()

@router.post("/my/spawn")
async def my_spawn(service: TerrariumService = Depends(get_service)):
    info = await service.add_creature(
        "/home/user/creatures/researcher",
        on_node="worker-gpu-1",
        is_privileged=True,
    )
    return {"creature_id": info.creature_id}
```

lab-host 模式下注入的 `service` 是正在运行的
`MultiNodeTerrariumService`。**你不能在 module load 时就调用
`get_service()`** —— 它是一个 dependency provider，结果取决
于 API 启动路径（`api/app.py` 的 startup hook 仅当传入
`--mode lab-host` 时才会调用 `set_service(...)`）。

#### B) 你正在**把 lab-host 嵌入**自己的 Python 程序

如果你写的是一个 daemon / Python 入口点，想直接驱动一个多节
点集群（不走 FastAPI），就像 `api/app.py` 启动时那样自己把
host + service 搭起来：

```python
import asyncio

from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.transport_ws import WebSocketTransport
from kohakuterrarium.laboratory.config import HostConfig
from kohakuterrarium.laboratory.adapters import (
    StudioCatalogAdapter,
    StudioIdentityAdapter,
    TerrariumBroadcastAdapter,
    TerrariumOutputWireAdapter,
)
from kohakuterrarium.session.sync import SessionMirrorWriter
from kohakuterrarium.terrarium import (
    MultiNodeTerrariumService,
    Terrarium,
)
from kohakuterrarium.utils.config_dir import config_dir


async def main():
    # 1. Lab 传输 —— 接受 worker 的 WebSocket 连接。
    host = HostEngine(
        HostConfig(
            bind_host="0.0.0.0",
            bind_port=8100,
            token="shared-secret",
            heartbeat_timeout_seconds=30.0,
        ),
        WebSocketTransport(),
    )
    await host.start()

    # 2. 协调引擎 —— 一个裸的 Terrarium，承载跨节点的通道对象，
    #    以及（可选地）由 recipe 生成的生物。Worker 才是真正做
    #    Agent 工作的一方；这个引擎永远不会对绑定 worker 的
    #    spawn 收到 add_creature。
    coord = Terrarium(session_dir=str(config_dir() / "sessions"))

    # 3. Studio / 你的 app 消费的 Protocol 面。
    service = MultiNodeTerrariumService(host=host, coordination_engine=coord)

    # 4. Worker 会查询的主机侧 adapter（identity、catalog、跨节
    #    点 broadcast / output-wire forwarder、吸纳 worker session
    #    事件同步的 session mirror writer）。
    StudioIdentityAdapter(host)
    StudioCatalogAdapter(host, is_host=True)
    TerrariumBroadcastAdapter(coord, host)
    TerrariumOutputWireAdapter(coord, host)
    SessionMirrorWriter(host, config_dir() / "sessions" / "mirror")

    # 5. 等待至少一个 worker 连上来（worker 在 Hello/Welcome
    #    握手中会自己注册）。
    while not list(service.connected_nodes()):
        await asyncio.sleep(0.5)
    print("connected nodes:", list(service.connected_nodes()))

    # 6. 现在 spawn。``on_node`` **必须**指向一个已连接的
    #    worker —— 在 lab-host 模式下 start_creature 拒绝在主机
    #    上 spawn（协调引擎仅供 recipe 使用）。
    info = await service.add_creature(
        "/abs/path/to/creature/on/worker/disk",
        on_node="worker-gpu-1",
        is_privileged=True,
    )
    print(info.creature_id, info.graph_id, info.home_node)

    # 7. 通过 Protocol 驱动 chat。
    async for token in service.chat(info.creature_id, "hello"):
        print(token, end="", flush=True)

    await host.stop()


asyncio.run(main())
```

让该路径在工作节点上可解析的两种方式：

1. **共享文件系统** —— 主机和工作节点挂载同一个网络共享；不
   需要部署。
2. **`studio.deploy`** —— 通过 Lab 推送生物文件夹：

```python
from pathlib import Path
from kohakuterrarium.studio.deploy import deploy_creature_to_node

target_path = await deploy_creature_to_node(
    host,                # 来自第 1 步的 HostEngine
    node_id="worker-gpu-1",
    src=Path("/home/user/creatures/researcher"),
)
info = await service.add_creature(target_path, on_node="worker-gpu-1")
```

对于 inline 的 `AgentConfig`（任何地方磁盘上都没有文件夹），
直接把 config 对象传过去 —— 它会以 packed dict 的形式过 wire：

```python
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig

cfg = AgentConfig(
    name="ephemeral",
    system_prompt="You are a tiny SWE agent.",
    input=InputConfig(type="cli"),
    output=OutputConfig(type="stdout"),
    llm_profile="openai/gpt-4o-mini",
)
info = await service.add_creature(cfg, on_node="worker-gpu-1")
```

## 多节点 terrarium

一个 *terrarium*（多生物图）可以通过跨节点通道横跨多个工作节
点。Recipe 文件目前还不支持每生物的节点指定，所以请用命令式
的方式搭建拓扑：

```python
# 在 worker-1 上生成 alpha、在 worker-2 上生成 bravo
alpha = await service.add_creature(alpha_cfg, on_node="worker-1")
bravo = await service.add_creature(bravo_cfg, on_node="worker-2")

# 跨节点连接 —— 在两边自动创建通道、接好 send + listen、通过
# broadcast 适配器交叉订阅、记录集群链接。
result = await service.connect(alpha.creature_id, bravo.creature_id)
print(result.channel, result.delta_kind)  # "alpha_to_bravo", "cross_node"
```

`connect` 之后，`alpha` 与 `bravo` 组成一个 **集群**。从所有
读 API（列表、history viewer、运行时图快照、聊天 WS）看，这
个集群就像一个带有两个生物的逻辑 session —— 即使每个工作节
点仍然各自拥有自己的引擎图 + session 文件。

## Resume

某个工作节点上的单生物（在同一个工作节点上 resume 同一个
session，或者把它搬到另一个工作节点）：

```http
POST /api/sessions/{sid}/resume
{"on_node": "worker-1"}
```

集群（跨多个工作节点的图）：传入完整的成员列表，让每个工作
节点重新采纳自己的那部分，然后主机重新发起 `service.connect`
来重新填充 `_cluster_links`：

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

当你省略 `members` 时，该路由会从主成员持久化的
`cluster_members` meta 自动发现成员 —— 它在 `stop_session`
时保存，因此集群拓扑能在彻底重启后仍然保留。

> Resume 要求每个被指名的工作节点都已连接。如果某个掉线了，
> 你会得到 `404 not a connected lab node`；重新连接工作节点
> 后重试即可。

实际在 wire 上发生的事（完整机制见
[概念文档](../concepts/laboratory.md)）：主机打开每个成员的
镜像文件、checkpoint 内存中的写入、把字节流式传到目标工作节
点的 `config://resume/` scope，然后调用工作节点的
`terrarium.session.resume` 适配器；工作节点重建引擎图并重新
attach session store。之后的事件像往常一样流回主机镜像。

## 常见工作流

### 把生物从你的笔记本搬走

```bash
# 你的开发机保留 dashboard、代码编辑器、终端。
kt serve start --mode lab-host --foreground \
               --lab-bind 0.0.0.0:8100 --lab-token T

# 远端更强的机器运行真正的 Agent。
ssh gpu-box "kt lab-client --host wss://laptop.tailnet:8100 \
                           --token T --name gpu-box \
                           --home-dir ~/.kohakuterrarium-gpu"

# 在 dashboard 的 New-creature site 选择器里选 "gpu-box"。
```

### 在工作节点上使用 Codex

主机不能与工作节点共享 Codex token。请在工作节点上登录：

1. Settings → Providers → 把 **Manage on:** 设为你的工作节
   点。
2. 点击 **Codex login**。OAuth 浏览器会 *在工作节点机器上*
   打开（无头时打印 device-code URL）。
3. 完成流程。Token 落在
   `<worker --home-dir>/codex-auth.json`。
4. 在那个工作节点上以 Codex 支持的模型生成任何生物；它会通
   过本地优先的 IdentityCache 拿到本地 token。

### 两台笔记本，共享 session

两台笔记本都连到第三台跑 `kt serve --mode lab-host` 的机器。
任一笔记本的 dashboard 都能看到相同的 session 列表、相同的生
物，并且通过主机对工作节点 session 文件的镜像与它们交谈。
（Session 文件在托管该生物的工作节点上；镜像在主机上。）

### 分布式调试

每个工作节点有自己的文件系统、终端（`TerrariumPtyAdapter`）
和进程组。`worker-test` 上的生物可以跑 `pytest` 而不碰开发
机。主机的 PTY 面板透明地代理 stdin/stdout/stderr。

## 验证接线

```bash
# 在主机上
curl http://localhost:8001/api/runtime/graph | jq '.nodes'

# 应该列出主机以及每个已连接的工作节点，并附上各自的生物
# 名单。
```

```python
from kohakuterrarium.api.deps import get_service
svc = get_service()
print(list(svc.connected_nodes()))      # ['worker-1', 'worker-2']
print(svc._cluster_links)               # frozenset((node, gid)) 对的集合
```

如果工作节点出现在 `connected_nodes()` 里但它的生物不可见：
请查看工作节点的 stderr —— 大部分启动时的适配器错误以
WARNING 级别记录在工作节点侧，不会出现在主机的 log 里。

## 故障排查

| 现象 | 可能原因 |
|------|----------|
| 生成时 `"on_node" is required` | 你处于 lab-host 模式但尝试在主机上生成。选一个工作节点，或者用 recipe（recipe 仍然在 coordination engine 上运行）。 |
| 工作节点连上后立刻断开 | Token 不匹配。Hello/Welcome 握手会在 INFO 级别记录拒绝。 |
| `worker 'X' resume failed: Session has no config_path or config_snapshot in metadata` | 镜像文件早于 1.5.x 的 meta-sync 顺序。请重新生成该生物，并从新文件 resume。 |
| Codex `re-login due to process mismatch` 错误 | 你在工作节点进程里使用主机的 Codex token。请通过 Settings → Providers（把 Manage on: 设为该工作节点）**在工作节点上** 登录 Codex。 |
| `worker 'X' is not a connected lab node`（resume） | 工作节点已断开。通过 `kt lab-client …` 重新连接后重试。 |
| 远端生成时路径形式 `add_creature("./creature/")` 失败 | 工作节点看不到该路径。请共享文件系统，或者先 `studio.deploy.deploy_creature_to_node(...)`。 |

## 参考

- CLI：见 [`kt serve start`](../reference/cli.md) 以及
  `kt lab-client --help`。
- HTTP API：每个现有的 `/api/sessions/...` endpoint 都接受
  `on_node`（生成时是 POST body 字段，identity 路由是
  `?node=` 查询参数）。请见 [HTTP API 参考](../reference/http.md)。
- Python：`kohakuterrarium.terrarium.MultiNodeTerrariumService`
  （lab-host 模式）、`RemoteTerrariumService`（每工作节点的
  handle）、`kohakuterrarium.laboratory.ClientConnector`（工
  作节点的 client 对象 —— 驱动你自己内嵌的工作节点）。
- 概念：[Laboratory](../concepts/laboratory.md) —— wire 格
  式、session 同步、resume 语义、identity 模型。
