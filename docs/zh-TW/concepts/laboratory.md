---
title: Laboratory 層
summary: KohakuTerrarium 如何跨越多台機器 — 基於 WebSocket 的傳輸層、自訂封包系統，以及讓 Studio 與 Terrarium 把遠端節點當成本地節點看待的透明化技巧。
tags:
  - concepts
  - laboratory
  - multi-node
---

# Laboratory 層

**Laboratory**（程式碼裡是 `kohakuterrarium.laboratory`，
口語上叫「Lab」）是一個網路層，讓一個 KohakuTerrarium
主機可以協調執行在遠端機器上的生物。它位於管理層（Studio）
與執行期引擎（Terrarium）之間，整個工作就是讓框架的其他
部分*感覺不到*網路的存在。

本文涵蓋端到端的設計。日常的操作任務（執行
`kt serve --mode lab-host`、新增 worker、挑選針對節點的憑證）
請見 [Laboratory 使用指南](../guides/laboratory.md)。

## 兩種階層，一個心智模型

KohakuTerrarium 有兩種執行模式；層級結構完全相同，差別只在
有沒有網路跳躍。

### 單機模式

```
user-facing UI (web / CLI / TUI / desktop)
      │
      ▼
    Studio              ← 管理框架
   (catalog · identity · sessions · persistence · editors · attach)
      │
      ▼
    Terrarium           ← 執行期引擎：圖、頻道、熱插拔
      │
      ▼
    Creatures           ← 真正的代理（LLM + 工具 + …）
```

一個行程、沒有網路、Lab 模組不會被 import。這就是
`kt serve` 預設啟動的樣子。

### 多節點模式（lab-host + lab-clients）

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

Studio 仍然只呼叫單一的 `TerrariumService` Protocol。在這個
Protocol 後面，`MultiNodeTerrariumService` 會把對單一生物的
操作分流路由到對應的節點。Studio 從來不需要 import Lab。

主機行程也可以執行 agent —— 在一個「協調引擎」裡 —— 也就是
一個本地 Terrarium，用於跨節點頻道路由，以及在沒有指定 worker
時承載 recipe 生成的生物。Worker 是完全相同的行程（同一個
`Terrarium` class、同一組 adapter、同樣的 session store 佈局）；
它們設定上唯一的差別是 worker 是**主動往外連**，而不是接受連線。

## 為什麼用 WebSocket

Lab 的傳輸層是純粹的 WebSocket（生產環境用 `wss://`），不是
gRPC、不是原始 TCP、也不是 QUIC。三個原因：

1. **它能不變樣地穿越 Cloudflare / nginx / 公司代理。**
   單一一個 TCP/443 跳躍就承載整個協定。不必設防火牆規則、
   也不必另開 signalling 通道。
2. **瀏覽器會講這個。** Studio 的網頁 UI 與 worker client
   使用同一套線路格式與同一套 envelope codec —— 未來瀏覽器
   dashboard 自己也可以當成 Lab client 出現，完全不需要
   重新實作任何東西。
3. **它是雙向且訊息成幀的。** L2 envelope 一對一地坐在 WebSocket
   的二進位 frame 裡；我們不需要在 byte stream 上重新發明
   訊息邊界。

WebSocket 對設計來說並非 load-bearing —— [傳輸層](#l1-transport)
是一個小 Protocol，`InProcTransport` 也實作它（每個測試都會用）。
換成 QUIC 或 Unix socket 只需要寫一份新的 `_internal/transport_*.py`。

## 封包系統

兩個 Lab 節點之間的每一個 byte 都被框成一個 **envelope**（信封）。
envelope 是自訂的（不是 protobuf、不是 gRPC），原因很具體：我們
需要承載原始二進位 payload（檔案 bundle、session 事件 blob、
tokenizer 狀態），而不要被 flat msgpack 設計強制做的 base64 膨脹。

### 線路格式（L2）

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

Header 是 msgpack（小、schema 彈性、快）。Payload 是任意 bytes ——
由 L4 codec 決定怎麼解讀。

實作請看 `src/kohakuterrarium/laboratory/_internal/envelope.py`。

### 四個層級

| 層級 | 關注點 | 關鍵檔案 |
|------|---------|-----------|
| **L1** Transport | 節點之間的 byte stream（WebSocket 或行程內） | `_internal/transport_ws.py`, `_internal/transport_inproc.py` |
| **L2** Envelope | 成幀、路由 metadata、簽章 | `_internal/envelope.py` |
| **L3** Connection | handshake、heartbeat、定址、成員管理 | `_internal/host.py`, `_internal/client.py`, `_internal/protocol.py` |
| **L4** Verbs | 使用者可見的傳遞原語 + APP 命名空間 | `verbs.py` (`Channel`, `Topic`), `_internal/app.py` (`AppMessage`) |

### Envelope 種類

| Kind | 用途 |
|------|---------|
| `SEND` | 點對點傳遞（L4 `Channel.send`）—— 在訂閱者之間做負載平衡 |
| `BROADCAST` | pub-sub 扇出（L4 `Topic.publish`）—— 每個訂閱者都收到一份 |
| `APP` | 結構化的應用訊息：`{namespace, type, body}`，可選擇做 request/response 配對 |
| `ACK` | 對需要 ack 的 `SEND` 的確認 |
| `HELLO` / `WELCOME` / `HEARTBEAT` | 連線生命週期 |
| `CONTROL` | 框架內部（subscribe、register_creature、…） |

Studio 與 Terrarium 透過線路做的事情幾乎都是 **APP** envelope。
APP 帶著 namespace（例如 `terrarium.runtime`、`studio.identity`、
`terrarium.session.sync`）、type（namespace 裡的動詞）、以及一個
msgpack body。兩端各自註冊 namespace 的*擴充 handler*；雙方唯一
要達成共識的就是 dispatch 表。

## 透明化目標 1：Studio 看到單一系統

Studio 永遠不需要知道一隻生物住在行程內還是遠端機器上。
`TerrariumService` Protocol
（`src/kohakuterrarium/terrarium/service.py`）有 `add_creature`、
`list_creatures`、`chat`、`connect` 之類的方法。三個實作滿足它：

- `LocalTerrariumService` —— 直接呼叫行程內的 Terrarium。
- `RemoteTerrariumService` —— 把參數封裝成 `terrarium.runtime`
  上的 APP 請求送出去、解開回應。每個已連線 worker 對應一個
  實例。
- `MultiNodeTerrariumService` —— 同時持有一個 `LocalTerrariumService`
  與每個 worker 一份的 `RemoteTerrariumService`，依
  `creature_id → home_node` 註冊表路由單一生物操作、扇出全域操作。

在 lab-host 模式下，Studio 持有的是 composite。每一個過去呼叫
`engine.add_creature(...)` 的 Studio 方法現在改呼叫
`service.add_creature(..., on_node="worker-1")`。網路跳躍消失了。

## 透明化目標 2：Terrarium 看到單一引擎

頻道與圖拓樸也是單一命名空間。worker-1 上呼叫
`send_channel("ch1", "hello")` 的生物應該要送達每個 listener ——
包括 worker-2 上的 listener —— 就好像兩隻生物住在同一個行程裡
一樣。Lab 用兩個機制達成這點：

- **跨節點 connect**（`terrarium/multi_node_replication.py`）。
  當使用者呼叫 `service.connect(creatureA, creatureB)` 且兩隻
  生物住在不同 worker 時，主機會：
  1. 在兩個 worker 的圖上各加一個頻道物件。
  2. 在 sender 所在的 worker 上接 sender 的 send-side。
  3. 在 receiver 所在的 worker 上接 receiver 的 listen-side。
  4. 透過 `terrarium.broadcast` 做交叉訂閱，這樣 sender 所在
     worker 上的一次本地 send 會扇出到 receiver 所在的 worker，
     在那裡 inject 路徑會 replay 進本地頻道 registry。
  5. 把這條連結記在 `service._cluster_links`（一個
     `set[frozenset]`，元素為 `(node_id, graph_id)` 對）。
- **Output-wiring 轉發器**（`TerrariumOutputWireAdapter`）。
  指向其他 worker 上生物的 output-wire 目標也是經由相同的
  broadcast adapter 解析。

跨節點 connect 之後，兩個 worker 的圖形成一個*叢集*（cluster）——
一個橫跨機器的邏輯多生物圖。主機的 `MultiNodeTerrariumService`
利用 cluster 集合把列舉做摺疊（`list_creatures` 顯示聯集；
`list_channels` 按名稱去重），所以前端看到的是一個連通分量，
即使每個 worker 各自保留自己的引擎圖。

熱插拔的運作方式相同：`group_add_node`（特權生物可以呼叫的
工具）打到 runtime adapter，依照 recipe 有沒有指定目標節點，
adapter 要嘛在本地生成，要嘛透過 service 路由到另一個 worker。

## Session：以事件同步做鏡像

Lab 中最獨特的設計選擇是持久化的運作方式。

### 權威寫入者 + 讀取側鏡像

每一隻執行中的生物有恰好一個**權威的** `SessionStore`
（透過 KohakuVault 開的 SQLite 檔案）—— 在承載它的 worker 上。
每個 session 檔案恰好只有一個 writer。生物產生的每一個事件
都先落在那個檔案。

worker 在掛上 store 的當下，也會安裝一個
**`SessionEventTee`**（`session/sync.py`）。Tee 會：

1. 同步快照 store 的 `meta` 並把它當成第一則線路訊息排入佇列。
2. 訂閱 store 的 append callback。
3. 把每一個事件透過 namespace 為 `terrarium.session.sync`
   的 APP 訊息（snapshot 是 type `meta`、每次 append 是
   type `event`）泵送給主機。

主機執行一個 **`SessionMirrorWriter`**，接收這些訊息並寫進
自己的 session 目錄下的**鏡像 store**
（`<KT_CONFIG_DIR>/sessions/mirror/<graph_id>.kohakutr`）。
鏡像是一個真實的 `SessionStore`，跟 worker 的完全一樣，只是
以追加方式對著線路驅動的串流開啟。

Studio 的讀取 API（history、viewer、search、fork）都從鏡像讀，
絕不從 worker 讀。鏡像是本地 SQLite，所以翻一萬筆事件不會
每頁都做一次來回。

### 順序與耐久性

- Tee 使用每 session 一個的外送 asyncio queue。事件以 append
  順序送達主機。如果連線斷了，pump 會用有界 backoff 重試 ——
  事件會被緩衝，不會遺失。
- 鏡像 writer 先寫 meta key（這樣 `config_path` /
  `config_snapshot` 會在任何事件之前落地），然後依到達順序
  追加事件。每個 key 的寫入是隔離的：單一 key 失敗不會中止
  其他 key。
- 主機的鏡像檔案是 best-effort。Worker 的本地檔案永遠是 resume
  的真理來源。

### 為什麼這樣設計

兩個理由說明為什麼採用事件鏡像而不是快照鏡像：

1. **即時讀取。** Studio 的歷史檢視器可以在事件抵達的那一刻
   顯示；不需要 polling、也不會有秒等級的最終一致性意外。
2. **斷線存活。** 如果一個 worker 在對話進行到一半時掉線，
   主機仍然擁有截至斷線為止的每一個事件 —— Studio 持續回應
   歷史查詢 —— 而當 worker 重新連線時，鏡像已經是最新狀態；
   Tee 從下一個事件接著上，不需要做 resync RPC。

取捨是一個 session 同時存在於兩個地方。我們永遠把 worker 的
檔案當成權威；鏡像存在是為了讀取方便，並作為我們在 resume
時推回給 worker 的磁碟映像（見下）。

### 為什麼不分片 session

每隻生物/每個圖恰好一個檔案。我們考慮過把事件扇出到多個鏡像，
但是否決了，因為：

- KohakuVault 的 SQLite append 已經很快（每事件約 ~50 µs）。
- 單一檔案簡化了 fork / search / viewer 的程式路徑。
- 鏡像是忠實複本；你可以 `cp` 出來在任何節點上 resume。

## Resume：把磁碟映像推回去

Resume 跑的還是同一個 `engine.adopt_session(path)` —— 只是在
多節點模式下，path 在主機上、引擎在 worker 上。主機橋接這個
落差：

1. 使用者在「已儲存」分頁挑一個 session 並按下 **Resume on
   worker-1**。前端送出
   `POST /api/sessions/{sid}/resume {"on_node": "worker-1"}`。
2. Route 打開鏡像檔案、checkpoint 任何在記憶體裡的寫入
   （`mirror.checkpoint(sid)` flush SQLite WAL）、讀出原始 bytes，
   並串流到 worker-1 的 `terrarium.files` adapter，scope 為
   `config://resume/`。
3. 當 bytes 落到 worker 的磁碟之後，route 用剛推上去的檔案絕對
   路徑呼叫 worker-1 的 `terrarium.session.resume` adapter。
4. Worker 的 adapter 呼叫 `engine.adopt_session(local_path)`，
   它讀取 meta、分派到單生物（`_resume_agent_into_engine`）
   或多生物（`_resume_terrarium_into_engine`）重建、附上 store，
   並啟動每一隻被 adopt 的生物。
5. Worker 的 `WorkerSessionAttacher` 安裝新的 `SessionEventTee`；
   被 resume 的生物產生的後續事件會流回主機的鏡像，就好像
   它們本來就是在那裡生成的一樣。

### 為什麼 meta 裡要存一份 config 快照

對 recipe 定義的生物，worker 可以呼叫 `Agent.from_path(...)`，
因為 recipe 資料夾在 worker 的檔案系統上是存在的。但是 inline
生成的生物（SDK 情境，以及使用者用 `--home-dir` 隔離 worker
磁碟的 recipe）通常在這台機器上沒有資料夾可以載入。為了讓那些
生物可以在任何節點 resume，worker 的 `_ensure_store_meta`
（[`_worker_session.py`](_worker_session.py-line)）透過
`pack_agent_config` 擷取完整的 `AgentConfig` 並存在
`meta["config_snapshot"]` 下。Resume 路徑
（`session/resume.py::_rebuild_agent`）有 folder 存在時優先用
`config_path`，否則回退到 `unpack_agent_config(snapshot)`。

### 單生物 resume（CF-6 基準）

對單一 worker 上的一隻生物：

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

### 多生物圖 resume（CF-6 cluster）

對一個橫跨多個 worker 的 cluster —— 每個 worker 承載 cluster 連
通分量的一部分 —— 使用者傳入一份 `members` 清單：

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

Route：

1. 驗證每一個指定的 worker 都已連線（避免推到某些 worker 後
   在其他 worker 上失敗，留下半個 resume 完成的 cluster）。
2. 對每個 member：對該 member 的 `(sid, on_node)` 跑單生物
   resume 流程。
3. 在每個 member 都恢復之後，**對 cluster meta 編碼的每一條
   跨節點連結重新發出 `service.connect`**，讓 `_cluster_links`
   被重新填回，頻道送出的扇出與關閉前完全一樣。
4. 回傳帶有 `cluster_members` 的合併 `Session` handle。

Cluster member 清單在 `stop_session` 時被持久化到每個 member
的鏡像 meta，所以 cluster 可以從任何一個 member 的存檔自動
被探索出來（route 在省略 `members` 時就會這麼做）。

**這個流程被 `tests/e2e/test_multinode_journey.py` 的步驟
*32g CF-6 cluster resume* 做端到端測試**：它在 w1 生 alpha、
w2 生 bravo、在兩者之間形成一條跨節點頻道、把 chat 流量推過
這座橋、刪掉兩個 active session、然後透過 members API resume，
驗證 `_cluster_links` 被重新填回、chat 仍然正確路由。

### 執行期拓樸變更：快照 + replay

Recipe（`terrarium.yaml`）描述圖一開始的拓樸。recipe 載入
**之後**由使用者（或特權工具）追加的所有東西 —— 透過
`service.add_channel` 加的頻道、透過 `service.connect` 加的
接線、透過 `disconnect` / `unwire` 做的移除 —— 只存在於引
擎記憶體中的 `GraphTopology`。沒有持久化的話，每一次 close
+ resume 都會遺失。

引擎在每一次變更（`add_channel`、`remove_channel`、
`connect`、`disconnect`、`wire_creature`、`unwire_creature`）
之後，把目前拓樸的*完整快照*寫進
`store.meta["runtime_topology"]`。形狀是：

```
{
    "channels":     [{"name": str, "description": str}, ...],
    "listen_edges": {creature_id: [channel_name, ...]},
    "send_edges":   {creature_id: [channel_name, ...]}
}
```

Resume 時，`_resume_terrarium_into_engine` 先重建 recipe 描
述的拓樸，再呼叫 `topology_snapshot.replay(engine, sid)`，
把儲存的快照中尚未出現在圖裡的每一個頻道 + 接線都加進去。
因為快照是*完整*的（不是 delta log），使用者的移除也會被
反映出來 —— 任何被使用者移除的東西就是不在快照裡。

實作：`src/kohakuterrarium/terrarium/topology_snapshot.py`。
測試：`tests/integration/test_runtime_topology_resume.py`。

### 目前的已知限制

| 情境 | 狀態 |
|----------|--------|
| 1 隻生物在 1 個 worker | ✅ 已測試（journey 32d） |
| Recipe 生成的多生物圖在協調引擎上 | ✅ 用標準的 `_resume_terrarium_into_engine` |
| N=2 worker 的 cluster，各 1 隻生物，跨節點橋接 | ✅ 已測試（journey 32g / CF-6） |
| 3+ worker 的 cluster | ⚠ 未測試（機制相同，只是 member 多） |
| 每個 worker 有多隻生物的 cluster | ⚠ 未測試但應該可動 —— 每個 worker 的 resume 各自獨立重建自己的圖 |
| Recipe 檔案內每隻生物標 `on_node` | ❌ 不支援 —— recipe schema 沒有 node 欄位。請透過個別的 `add_creature(on_node=…)` + `service.connect` 手動組裝 |
| 目標 worker 離線時 resume | ❌ 回 404 並附上缺席 worker 的名稱 —— 操作員必須先重連 |

## Identity：local-first

LLM 憑證是 per-process，不是 per-cluster。1.5.x 的預設是
**local-first**：

1. Worker 的 `IdentityCache.sync_api_key(provider)` 首先讀
   worker 自己的 `<KT_CONFIG_DIR>/api_keys.yaml` 與 provider
   環境變數。
2. 只在 miss 時才回退到主機最近透過 `studio.identity` 推下來
   的內容。
3. Codex OAuth token（`<KT_CONFIG_DIR>/codex-auth.json`）也一樣 ——
   先 local、後 host。**Codex token 必須在本地**，因為 OAuth
   refresh 是行程綁定的：試圖從一個 worker 行程使用主機的
   token，永遠會再次向使用者要登入。

`--home-dir` 旗標（`kt serve`、`kt lab-client`）會設定
`KT_CONFIG_DIR`，所以每個 worker 都可以在磁碟上攜帶自己獨立
的憑證儲存。

在 Settings → Providers 裡，使用者透過 **Manage on:** 選擇正在
編輯哪一個節點的憑證儲存。在選定 worker 的情況下儲存一把 key
會透過 Lab APP 把寫入動作送到該 worker 的 `StudioIdentityAdapter`，
adapter 會把它持久化到 worker 本地的檔案。Codex 登入也一樣 ——
在選定 worker 的情況下點 **Codex login** 會*在該 worker 上*跑
OAuth 流程，所以瀏覽器是在 worker 的機器上開啟、產生的 token
住在 worker 的磁碟上。

## Files、deployment 與 sandboxing

- **`terrarium.files`** —— 透過 Lab APP 做 scope 受限的檔案 IO。
  五個 scope：`workspace://<creature>`、`memory://<creature>`、
  `package://<name>`、`recipe://<id>`、`config://`。對 >512 KB
  的 payload 做串流讀寫；冪等的 atomic commit（被 adopt 的
  SessionStore 持有著開啟的目標檔案不會被重寫 —— 見
  `_op_write_commit`）。
- **`studio.deploy`** —— `push_creature_bundle`：走訪 creature
  資料夾、計算每個檔案的 SHA、透過 `terrarium.files` 推送、
  atomic rename 到 `recipe://<name>/...`。重新推送透過 hash 檢查
  做冪等，所以已經有 recipe 的 worker 不會重新下載。
- **`terrarium.pty`** —— 把 worker 的 shell session 代理到主機側
  的 WebSocket。前端的 terminal 面板對著遠端生物的工作目錄可以
  完全不變地運作。
- 路徑形式的 `add_creature("./my-creature/")` 在 worker 檔案系統
  看不到該路徑時會被拒絕。請先用 `studio.deploy` 推送 bundle，
  然後用 worker 側的 `recipe://` 路徑生成。

## Cluster-wide 摺疊

使用者在 dashboard 開啟一個 session 時，看到的是**一個**對話、
**一份**生物清單，即使 cluster 橫跨三個 worker。摺疊發生在兩個
地方：

- **列舉**（`studio.sessions.cluster_fold`）。已儲存 session 清單、
  active session 清單、執行期圖快照，全都把同一個 cluster 的
  member 聯集到 primary sid 之下（字典序最小的 member sid =
  primary）。前端不會為一個 cluster 看到 N 個分開的 session。
- **操作**（chat WebSocket、頻道、記憶、群組工具）。對單一生物
  的定址是經由 `_home` 註冊表解析，所以即使呼叫者用名字定址，
  `chat` 與 `inject_input` 也能抵達正確的 worker。

## 詞彙速查

| 詞 | 意思 |
|------|---------|
| **Lab / Laboratory** | `kohakuterrarium.laboratory` 套件；網路層。 |
| **Host / 主機** | 執行 `kt serve --mode lab-host` 的行程。擁有 Studio + `HostEngine`。也可能透過協調引擎承載 agent。 |
| **Worker / 工作節點** | 執行 `kt lab-client` 的行程。承載生物，透過 Lab adapter 暴露它們。 |
| **Node / 節點** | 主機或 worker —— 任何說 Lab 協定的人。以 `node_id`（`_host` 或 client 的 `--name`）定址。 |
| **Adapter / 轉接器** | 實作一個或多個 APP namespace 的 class（例如 `TerrariumRuntimeAdapter` 服務 `terrarium.runtime` namespace）。 |
| **`TerrariumService`** | Studio 呼叫的 Protocol。三個實作：`Local`、`Remote`、`MultiNode`。 |
| **Cluster / 叢集** | 一組跨節點連線的圖。記錄在 `MultiNodeTerrariumService._cluster_links`。從使用者觀點看是一個邏輯 session。 |
| **Mirror / 鏡像** | 主機側的 worker session 檔案複本，由 `SessionEventTee` → `SessionMirrorWriter` 填充。所有讀取 API 的來源。 |
| **Cluster fold / Cluster 摺疊** | 對 `_cluster_links` 做 union-find，把每個 member sid 對映到 cluster 的 primary sid；前端列舉或定址 cluster 時到處都用。 |

## 延伸閱讀

- [Laboratory 使用指南](../guides/laboratory.md) —— 怎麼實際跑起來。
- [工作階段與恢復](../../guides/sessions.md) —— 單節點下的持久化基礎。
- [生態瓶](./multi-agent/terrarium.md) —— Lab 包覆的引擎。
