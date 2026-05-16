---
title: Laboratory（多節點）
summary: 跨兩台以上機器執行 KohakuTerrarium —— kt lab-host + kt lab-client、每個 worker 的憑證、程式化使用、多節點生態瓶、與 resume。
tags:
  - guides
  - laboratory
  - multi-node
  - serving
---

# Laboratory（多節點）

Laboratory 層讓一個**主機**行程協調執行在遠端 **worker** 機器
上的生物。本指南是實用 how-to。設計理由請見
[概念 → Laboratory](../concepts/laboratory.md)。

## 何時使用

在以下情況使用 lab-host 模式：

- 你想讓生物在和 UI 不同的機器上執行（GPU 機器、sandbox VM、
  雲端節點）。
- 你需要每隻生物有**自己的** OAuth 登入（Codex、ChatGPT
  訂閱）—— OAuth 是行程綁定的，不能共享，而 worker 上的
  local-first identity 模型代表每個 worker 可以持有自己的
  token。
- 你想讓生物的檔案系統動作（workspace 檔案、subprocess shell、
  MCP 伺服器）落在和 dashboard 不同的主機上。

其他情境（單使用者、單機）請繼續用 `kt serve` / `kt web` /
`kt app` —— 它們比較簡單。

## 啟動主機

```bash
# 前景（設定時建議用）
kt serve start --mode lab-host \
               --foreground \
               --lab-bind 0.0.0.0:8100 \
               --lab-token "$(openssl rand -hex 24)" \
               --home-dir ~/.kohakuterrarium-host

# Daemon（生產，從 terminal 退出）
kt serve start --mode lab-host \
               --lab-bind 0.0.0.0:8100 \
               --lab-token <shared-secret> \
               --home-dir /var/lib/kohakuterrarium/host
```

旗標：

- `--mode lab-host` —— 在正常 web stack 之外接受 worker 連線。
  主機在 lab-host 模式下**預設不執行任何生物**；每次 spawn
  都必須指定 worker（或回退到 recipe-only 的協調引擎）。
- `--lab-bind host:port` —— worker 連線到的 WebSocket 端點。
  使用 worker 可達的 bind 位址；生產環境請放在 nginx /
  Cloudflare 後面做 TLS 終結。
- `--lab-token` —— shared secret。每個 worker 在 Hello
  handshake 中提示這個；token 不符會被拒絕。在綁定非 loopback
  位址時**永遠要設定這個**。
- `--home-dir` —— 重新指定 `KT_CONFIG_DIR`。API key、OAuth
  token、LLM profile、MCP 伺服器、session 都住在這底下。省略
  時預設 `~/.kohakuterrarium`。

網頁 UI 仍然在 `--host:--port`（預設 `127.0.0.1:8001`）服務，
與單機模式完全一樣；`--lab-bind` 是給 worker 連線的*第二個*
listener。

## 連接一個 worker

在另一台機器上（或同一台的另一個 shell）：

```bash
kt lab-client \
  --host  wss://your-host.example/lab        \
  --token <same-shared-secret>               \
  --name  worker-gpu-1                       \
  --home-dir ~/.kohakuterrarium-workers/gpu1
```

旗標：

- `--host` —— 純文字用 `ws://`、TLS 用 `wss://`。如果你用
  Cloudflare 或 nginx 代理，這裡填公開端點；Lab 協定可以
  不變樣地穿越懂 WebSocket 的代理。
- `--token` —— 必須與主機的 `--lab-token` 相符。
- `--name` —— 主機認識這個 worker 用的 node id。在連線的
  workers 中必須唯一。
- `--home-dir` —— **每個 worker 各自的** 設定 home。給每個
  worker 自己的目錄，這樣它們的 `api_keys.yaml`、Codex OAuth
  token 與 session 檔案才不會撞在一起。從 worker 使用 Codex
  時，這是唯一合理的做法。
- `--session-dir` —— 選用 override；預設 `<home-dir>/sessions`。

worker 連上時，主機會記錄一條 CONTROL `register_creature`
trace，dashboard 的 site 選單會出現新項目。

## 每個 worker 的 provider 憑證

`Settings → Providers` 有一個 **Manage on:** 下拉，用來挑選
你正在編輯哪個節點的憑證儲存。

- **Host** —— key + Codex token 落在主機（lab-host 行程的
  `--home-dir`）。
- **某個 worker 的名字** —— key + Codex 登入透過 Lab APP 路由
  到該 worker；worker 寫到自己 `--home-dir/api_keys.yaml`，
  並啟動自己的 OAuth 瀏覽器流程。

Local-first 的查找代表 `worker-gpu-1` 上的生物會這樣找它的
OpenAI key：

1. `worker-gpu-1` 自己的 `<--home-dir>/api_keys.yaml`
2. worker 上的 `OPENAI_API_KEY` 環境變數
3. 主機的 identity 儲存（透過 Lab APP `studio.identity`
   namespace）—— 只有在 (1) 和 (2) 都 miss 的情況下。

特別是 Codex：OAuth refresh token 是行程綁定的，所以
**Codex 必須在實際用它的 worker 上登入**。主機沒辦法以一種
能撐過 refresh 的方式把 Codex token 分享給 worker。

## 在 worker 上生成生物

### 從 UI

在 dashboard 的「New creature」modal 中，**Site** 選單顯示
每一個連線中的 worker 加上 `Host`。挑選 worker、照常設定、
按下 Spawn。前端會送：

```http
POST /api/sessions/active/creature
{
  "config_path": "/abs/path/to/creature.yaml",
  "on_node": "worker-gpu-1"
}
```

（在 lab-host 模式下，`start_creature` 必須帶 `on_node` ——
在主機上 spawn 會被拒絕，因為主機不執行 agent。）

### 從 HTTP API

每個 session / 拓樸端點都接受 `on_node` 作為新 spawn 的目標，
spawn 之後對單一生物的操作依 home 註冊表路由。

```bash
# 在 worker-gpu-1 上生成 recipe 定義的生物
curl -X POST http://localhost:8001/api/sessions/active/creature \
     -H 'Content-Type: application/json' \
     -d '{"config_path": "/home/user/creatures/researcher",
          "on_node": "worker-gpu-1"}'
```

### 程式化

程式化使用面有兩個不同的入口；依你要建造的東西挑一個：

#### A) 你**身處**執行中的 `kt serve --mode lab-host` 內部

例如：自訂的 HTTP route、外掛、或從同一個行程衍生出的背景
任務。使用 FastAPI 的相依性注入來取得 active service：

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

在 lab-host 模式下，被注入的 `service` 就是執行中的
`MultiNodeTerrariumService`。**你不能在 module load 時就呼叫
`get_service()`** —— 它是一個相依性 provider，結果取決於 API
啟動路徑（`api/app.py` 的 startup hook 只在傳入 `--mode
lab-host` 時才會呼叫 `set_service(...)`）。

#### B) 你正在**把 lab-host 嵌入**自己的 Python 程式

如果你寫的是一個 daemon / Python 進入點，想直接驅動一個多節
點叢集（不走 FastAPI），就像 `api/app.py` 啟動時所做的那樣，
自己把 host + service 建起來：

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
    # 1. Lab 傳輸 —— 接受 worker 的 WebSocket 連線。
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

    # 2. 協調引擎 —— 一個裸的 Terrarium,承載跨節點的頻道物件,
    #    以及(可選地)由 recipe 生成的生物。Worker 才是真正執行
    #    agent 工作的一方;這個引擎永遠不會對綁定 worker 的
    #    spawn 收到 add_creature。
    coord = Terrarium(session_dir=str(config_dir() / "sessions"))

    # 3. Studio / 你的 app 消費的 Protocol 介面。
    service = MultiNodeTerrariumService(host=host, coordination_engine=coord)

    # 4. Worker 會查詢的主機側 adapter(identity、catalog、跨節
    #    點 broadcast / output-wire forwarder、吸收 worker session
    #    事件同步的 session mirror writer)。
    StudioIdentityAdapter(host)
    StudioCatalogAdapter(host, is_host=True)
    TerrariumBroadcastAdapter(coord, host)
    TerrariumOutputWireAdapter(coord, host)
    SessionMirrorWriter(host, config_dir() / "sessions" / "mirror")

    # 5. 等待至少一個 worker 連上來(worker 會在 Hello/Welcome
    #    handshake 時自行註冊)。
    while not list(service.connected_nodes()):
        await asyncio.sleep(0.5)
    print("connected nodes:", list(service.connected_nodes()))

    # 6. 現在 spawn。``on_node`` **必須**指向一個已連線的
    #    worker —— 在 lab-host 模式下,start_creature 會拒絕在主
    #    機上 spawn(協調引擎僅供 recipe 使用)。
    info = await service.add_creature(
        "/abs/path/to/creature/on/worker/disk",
        on_node="worker-gpu-1",
        is_privileged=True,
    )
    print(info.creature_id, info.graph_id, info.home_node)

    # 7. 透過 Protocol 驅動 chat。
    async for token in service.chat(info.creature_id, "hello"):
        print(token, end="", flush=True)

    await host.stop()


asyncio.run(main())
```

兩種讓路徑在 worker 上可解析的方法：

1. **共享檔案系統** —— 主機與 worker 掛載同一個網路 share；
   不需要 deploy。
2. **`studio.deploy`** —— 透過 Lab 推送 creature 資料夾：

```python
from pathlib import Path
from kohakuterrarium.studio.deploy import deploy_creature_to_node

target_path = await deploy_creature_to_node(
    host,                # 來自步驟 1 的 HostEngine
    node_id="worker-gpu-1",
    src=Path("/home/user/creatures/researcher"),
)
info = await service.add_creature(target_path, on_node="worker-gpu-1")
```

對於 inline `AgentConfig`（任何地方都沒有資料夾在磁碟上），
直接傳 config 物件 —— 它會以打包過的 dict 形式跨越線路：

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

## 多節點生態瓶

一個*生態瓶*（多生物圖）可以透過跨節點頻道橫跨多個 worker。
Recipe 檔案（目前）不包含每隻生物的節點指定，所以用命令式
方式建構拓樸：

```python
# 在 worker-1 生 alpha、worker-2 生 bravo
alpha = await service.add_creature(alpha_cfg, on_node="worker-1")
bravo = await service.add_creature(bravo_cfg, on_node="worker-2")

# 跨節點 connect —— 自動在兩側建頻道、接好 send + listen、
# 透過 broadcast adapter 交叉訂閱、記錄 cluster 連結。
result = await service.connect(alpha.creature_id, bravo.creature_id)
print(result.channel, result.delta_kind)  # "alpha_to_bravo", "cross_node"
```

`connect` 之後，`alpha` 與 `bravo` 形成一個**叢集**。從每個讀取
API（列舉、history viewer、執行期圖快照、chat WS）看起來，叢集
看起來像一個有兩隻生物的邏輯 session —— 即使每個 worker 仍然
持有自己的引擎圖 + session 檔案。

## Resume

單一 worker 上的單生物（在同一 worker 上 resume 同一個 session，
或搬到不同的 worker）：

```http
POST /api/sessions/{sid}/resume
{"on_node": "worker-1"}
```

Cluster（橫跨多 worker 的圖）：傳入完整的 member 清單，這樣
每個 worker 才會重新 adopt 自己的那一塊，然後主機重新發出
`service.connect` 把 `_cluster_links` 填回：

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

當你省略 `members` 時，route 會從 primary 持久化的
`cluster_members` meta 自動探索 members —— 這份資料在
`stop_session` 時被存下，所以 cluster 拓樸可以撐過完整的重啟。

> Resume 要求每一個指定的 worker 都已連線。如果有一個離線，
> 你會拿到 `404 not a connected lab node`；重連 worker 後再試。

線路上實際發生什麼（完整機制見[概念文件](../concepts/laboratory.md)）：
主機打開每個 member 的鏡像檔案、checkpoint 記憶體裡的寫入、
把 bytes 串流到目標 worker 的 `config://resume/` scope、然後
呼叫 worker 的 `terrarium.session.resume` adapter；worker 重建
引擎圖並重新附上 session store。後續事件像平常一樣流回主機
鏡像。

## 常見工作流

### 把生物移出你的筆電

```bash
# 你的開發機保有 dashboard、程式碼編輯器、terminal。
kt serve start --mode lab-host --foreground \
               --lab-bind 0.0.0.0:8100 --lab-token T

# 比較大的遠端機器執行真正的 agent。
ssh gpu-box "kt lab-client --host wss://laptop.tailnet:8100 \
                           --token T --name gpu-box \
                           --home-dir ~/.kohakuterrarium-gpu"

# 在 dashboard 的 New-creature site 選單中挑「gpu-box」。
```

### 在 worker 上用 Codex

主機不能把 Codex token 分享給 worker。在 worker 上登入：

1. Settings → Providers → 把 **Manage on:** 設為你的 worker。
2. 按 **Codex login**。OAuth 瀏覽器會*在 worker 的機器上*開啟
   （headless 環境下會印出 device-code URL）。
3. 完成流程。Token 會落在
   `<worker --home-dir>/codex-auth.json`。
4. 在該 worker 上 spawn 任何使用 Codex-backed model 的生物；
   它透過 local-first IdentityCache 取得本地 token。

### 兩台筆電、共享 session

兩台筆電都連到第三台跑 `kt serve --mode lab-host` 的機器。
任一台筆電的 dashboard 都看到同樣的 session 清單、同樣的生物，
並透過主機對 worker session 檔案的鏡像跟它們聊天。（session
檔案住在承載生物的那個 worker；鏡像在主機。）

### 分散式除錯

每個 worker 各有自己的檔案系統、terminal（`TerrariumPtyAdapter`）
與 process group。`worker-test` 上的生物可以跑 `pytest` 而不
碰到開發機。主機的 PTY 面板會透明地代理 stdin/stdout/stderr。

## 驗證連線

```bash
# 在主機上
curl http://localhost:8001/api/runtime/graph | jq '.nodes'

# 應該列出主機與每個連線中的 worker，各自附上 creature roster。
```

```python
from kohakuterrarium.api.deps import get_service
svc = get_service()
print(list(svc.connected_nodes()))      # ['worker-1', 'worker-2']
print(svc._cluster_links)               # set of frozenset((node, gid)) pairs
```

如果一個 worker 出現在 `connected_nodes()` 但它的生物看不到：
看看 worker 的 stderr —— 大部分啟動時的 adapter 錯誤是在
worker 側以 WARNING 等級記錄，不會出現在主機的 log 裡。

## 疑難排解

| 症狀 | 可能原因 |
|---------|--------------|
| spawn 時 `"on_node" is required` | 你在 lab-host 模式下試圖在主機上 spawn。挑一個 worker，或用 recipe（recipe 仍在協調引擎上執行）。 |
| Worker 連上後立刻斷線 | Token 不符。Hello/Welcome handshake 會以 INFO 等級記錄拒絕。 |
| `worker 'X' resume failed: Session has no config_path or config_snapshot in metadata` | 鏡像檔案比 1.5.x 的 meta-sync 順序還舊。重新 spawn 生物並從新檔案 resume。 |
| Codex `re-login due to process mismatch` 錯誤 | 你在 worker 行程裡用主機的 Codex token。在 **worker 上**透過 Settings → Providers（Manage on: 設為該 worker）登入 Codex。 |
| `worker 'X' is not a connected lab node`（resume 時） | Worker 斷線了。用 `kt lab-client …` 重連後重試。 |
| 遠端 spawn 時 `add_creature("./creature/")` 路徑形式失敗 | worker 看不到這個路徑。請共享檔案系統，或先呼叫 `studio.deploy.deploy_creature_to_node(...)`。 |

## 參考

- CLI：見 [`kt serve start`](../reference/cli.md) 與
  `kt lab-client --help`。
- HTTP API：每個現有的 `/api/sessions/...` 端點都接受
  `on_node`（spawn 用 POST body 欄位，identity 路由用
  `?node=` query）。見 [HTTP API 參考](../reference/http.md)。
- Python：`kohakuterrarium.terrarium.MultiNodeTerrariumService`
  （lab-host 模式）、`RemoteTerrariumService`（單 worker handle）、
  `kohakuterrarium.laboratory.ClientConnector`（worker 的 client
  物件 —— 驅動你自己嵌入的 worker）。
- 概念：[Laboratory](../concepts/laboratory.md) —— 線路格式、
  session 同步、resume 語意、identity 模型。
