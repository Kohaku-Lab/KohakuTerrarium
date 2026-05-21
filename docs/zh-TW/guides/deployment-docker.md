---
title: 部署 — Docker
summary: KohakuTerrarium 的三種 Docker compose 部署模式 — AIO、host + 同機 worker、分散式 host / worker。
tags:
  - guides
  - deployment
  - docker
---

# 部署 — Docker

KohakuTerrarium 在 GHCR 上提供三個官方 Docker 映像：

| 映像 | 用途 | 連接埠 |
|---|---|---|
| `ghcr.io/kohaku-lab/kohakuterrarium` | **AIO** — lab-host + 內嵌 worker 在同一容器 | `8001` (HTTP)、`8100` (Lab WS) |
| `ghcr.io/kohaku-lab/kohakuterrarium-host` | 僅 lab-host（Studio + Web UI） | `8001`、`8100` |
| `ghcr.io/kohaku-lab/kohakuterrarium-client` | 僅 worker（向外連線到 host） | （僅出站） |

三個映像均透過 release 工作流的 OIDC 身分用 cosign keyless 簽署。驗證：

```bash
cosign verify ghcr.io/kohaku-lab/kohakuterrarium:1.5.0 \
  --certificate-identity-regexp 'https://github.com/Kohaku-Lab/KohakuTerrarium/.github/workflows/release.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

下文引用的 compose 檔位於原始碼 `examples/deployment/` 下 — 選取最接近你需求的範本並修改即可。

## 模式 1 — AIO（單容器）

最小可部署單元。一個容器；host + 一個 worker 共用同一 Python 直譯器。所有狀態持久化到單一磁碟區。

```yaml
# examples/deployment/compose-all.yml
services:
  kt:
    image: ghcr.io/kohaku-lab/kohakuterrarium:1.5.0
    ports:
      - "8001:8001"   # Studio web UI + API
      - "8100:8100"   # Lab — 可選,只有需要外部 worker 才需要
    environment:
      # 若未設定,entrypoint 會自動產生並印到 stderr
      KT_HOST_TOKEN: "${KT_HOST_TOKEN:-}"
    volumes:
      - kt-data:/home/kt/.kohakuterrarium

volumes:
  kt-data: {}
```

啟動：

```bash
docker compose -f examples/deployment/compose-all.yml up -d
docker compose -f examples/deployment/compose-all.yml logs -f kt
```

啟動後第一行 log 會顯示自動產生的 token（或回顯你設定的環境變數）：

```
[kt-aio] Lab token: 8a3f7c…
[kt-aio] To attach external workers: KT_HOST_URL=ws://<this-host>:8100 KT_HOST_TOKEN=8a3f7c…
```

打開 `http://localhost:8001` — Studio UI 會顯示一個名為 `local-1` 的已連線 worker。

### 何時使用 AIO

- 單機開發 / staging — 無需叢集規劃。
- 作為單映像出貨的自包含產品。
- 展示：一條 `docker run` 即可。

### 局限

- 無法超出單容器 CPU / 記憶體。
- 內嵌 worker 與 host 容器共用檔案系統與憑證 — 沒有 per-worker 隔離。

## 模式 2 — host + N 個同機 worker

適用於你希望 worker 行程相互隔離（per-worker LLM 憑證 / per-worker 計算），但又不需要跨機器部署的情形。使用 Docker secret 讓共享 token 永遠不出現在 `docker inspect` 中。

```yaml
# examples/deployment/compose-host-clients.yml — 節錄
services:
  host:
    image: ghcr.io/kohaku-lab/kohakuterrarium-host:1.5.0
    ports: ["8001:8001"]
    secrets: [kt_host_token]
    environment:
      KT_HOST_TOKEN_FILE: /run/secrets/kt_host_token
    volumes:
      - kt-host-data:/home/kt/.kohakuterrarium
    healthcheck:
      test: ["CMD", "wget", "-q", "-O-", "http://127.0.0.1:8001/readyz"]
      interval: 10s
      timeout: 3s
      retries: 5

  worker-a:
    image: ghcr.io/kohaku-lab/kohakuterrarium-client:1.5.0
    depends_on:
      host: { condition: service_healthy }
    secrets: [kt_host_token]
    environment:
      KT_HOST_URL: ws://host:8100
      KT_HOST_TOKEN_FILE: /run/secrets/kt_host_token
      KT_CLIENT_NAME: worker-a
    volumes:
      - kt-worker-a-data:/home/kt/.kohakuterrarium

  worker-b:
    image: ghcr.io/kohaku-lab/kohakuterrarium-client:1.5.0
    depends_on:
      host: { condition: service_healthy }
    secrets: [kt_host_token]
    environment:
      KT_HOST_URL: ws://host:8100
      KT_HOST_TOKEN_FILE: /run/secrets/kt_host_token
      KT_CLIENT_NAME: worker-b
    volumes:
      - kt-worker-b-data:/home/kt/.kohakuterrarium

secrets:
  kt_host_token:
    file: ./secrets/kt_host_token
```

產生 token 後啟動：

```bash
mkdir -p secrets
openssl rand -hex 24 > secrets/kt_host_token
chmod 600 secrets/kt_host_token
docker compose -f examples/deployment/compose-host-clients.yml up -d
```

### 為何兩個 worker 找得到 host

兩個 worker 的 `KT_HOST_URL` 指向 **服務名稱** `host`，由 compose 網路的內建 DNS 解析。host 服務無需對外發布 — 只有 Studio UI 發布連接埠 `8001`。

### 擴充

新增 `worker-c` / `worker-d` 區塊，給它們獨立的 `KT_CLIENT_NAME` 與命名磁碟區。每個 worker 擁有自己的 `~/.kohakuterrarium/`，因此 LLM 設定檔與 API key 可按 worker 區分。

## 模式 3 — 分散式：host 在邊緣 VPS、worker 在家中機器

將 host 部署在擁有穩定公網位址的小型 VPS；worker 部署在擁有 GPU / API quota 的機器上。Worker 唯一需要的網路鏈路是 Lab WebSocket。

### Host 端（邊緣 VPS）

```yaml
# examples/deployment/compose-distributed-host.yml
services:
  host:
    image: ghcr.io/kohaku-lab/kohakuterrarium-host:1.5.0
    ports:
      - "127.0.0.1:8001:8001"  # 綁定 loopback,由 nginx 前置
    environment:
      # Lab WS 綁定所有介面以便外部 worker 接入,nginx 前置 TLS 終止
      # (詳見 deployment-reverse-proxy 指南)
      KT_LAB_BIND: 0.0.0.0:8100
      KT_HOST_TOKEN_FILE: /run/secrets/kt_host_token
    secrets: [kt_host_token]
    volumes:
      - kt-host-data:/home/kt/.kohakuterrarium
secrets:
  kt_host_token:
    file: ./secrets/kt_host_token
```

在連接埠 `8001` / `8100` 前部署 nginx（或 Cloudflare Tunnel）以提供 TLS。完整 nginx 設定見[反向代理指南](deployment-reverse-proxy.md)。

### Worker 端（家中機器）

```yaml
# examples/deployment/compose-distributed-client.yml
services:
  worker:
    image: ghcr.io/kohaku-lab/kohakuterrarium-client:1.5.0
    environment:
      KT_HOST_URL: wss://kt-host.example.com/lab
      KT_HOST_TOKEN: "${KT_HOST_TOKEN}"  # 貼上 host 的 token
      KT_CLIENT_NAME: home-gpu-1
    volumes:
      - kt-worker-home-data:/home/kt/.kohakuterrarium
```

跨公網必須使用 `wss://`（TLS-WebSocket）— 共享 token 僅驗證 worker 身分，不加密通道。

## 健康檢測

每個映像都在 HTTP 連接埠上提供 `/healthz` 與 `/readyz`，可用於：

- Docker `HEALTHCHECK`（官方 Dockerfile 已內建）。
- Kubernetes liveness / readiness 探針。
- 反向代理上游健康檢查（滾動重啟時 nginx 會自動撤流）。

`/healthz` 表示「行程存活」。`/readyz` 表示「Lab 傳輸已綁定並接受 worker」 — 以此作為負載平衡輪轉的依據。

## 持久化狀態

每個容器需要可寫的 `/home/kt/.kohakuterrarium` 磁碟區,其中包含：

- LLM 設定檔 + API key（`api_keys.yaml`、`llm_profiles.json`）
- MCP server 註冊（`mcp_servers.yaml`）
- Codex OAuth token（`codex-auth.json`）
- Session store（`sessions/*.kohakutr`）
- Host 自動產生的 token（`host-token`）

把該磁碟區當作資料庫目錄等級處理：備份、視威脅模型決定是否落盤加密，且不要在 host 間共用。

## 升級

正式環境請固定版本標籤（`:1.5.0`），不要用 `:latest`。閱讀 release notes 後：

```bash
docker compose pull
docker compose up -d
```

Compose 會逐一重建服務；只要 readiness healthcheck 已接好，host 會完全啟動後 worker 再重連。

## 鎖定 API — `[auth]` 設定

任何 compose 形態都可以透過 Docker secrets 加上四層認證設定，
成為多使用者 / 加鎖主機。可直接套用的範例：

```yaml
# examples/deployment/compose-host-auth.yml — 節錄
services:
  kohakuterrarium:
    image: ghcr.io/kohaku-lab/kohakuterrarium:1.5.0
    environment:
      KT_AUTH_HOST_TOKEN_FILE: /run/secrets/host_token
      KT_AUTH_ADMIN_TOKEN_FILE: /run/secrets/admin_token
      KT_AUTH_MULTI_USER: "required"
      KT_AUTH_REGISTRATION: "invite_only"
      KT_AUTH_LOOPBACK_BYPASS: "0"   # 前面有代理;loopback ≠ 信任
    secrets:
      - host_token
      - admin_token
    volumes:
      - kt-config:/root/.kohakuterrarium
    ports:
      - "127.0.0.1:8001:8001"        # 綁到 localhost;Caddy 在前
    restart: unless-stopped

secrets:
  host_token:  { file: ./secrets/host_token }
  admin_token: { file: ./secrets/admin_token }

volumes:
  kt-config:
```

啟動 stack 前在主機產生 secret 檔：

```bash
mkdir -p secrets
python -c "import secrets;print(secrets.token_hex(32))" > secrets/host_token
python -c "import secrets;print(secrets.token_hex(32))" > secrets/admin_token
chmod 600 secrets/*
```

容器內 ``kt admin users add operator --role admin`` 會在
``kt-config`` volume 上的共用 ``auth.db`` 中建立第一個使用者。

完整的四層模型、所有 CLI 動詞（``kt admin set-host-token`` /
``set-admin-token`` / ``invitations create`` 等）以及前端連線狀態
機如何發現已啟用的層級,參見[身份驗證](authentication.md)。

## 故障排除

- **worker 在 `/api/nodes` 始終 `unreachable`** → 查 worker 容器 log,是否出現 `lab-client: missing required configuration`（缺 token）或 `connection refused`（`KT_HOST_URL` 錯）。
- **host log 反覆出現 `auth_failed`** → worker 端 token 與 host 不一致;重新產生並重新分發 file secret。
- **`/healthz` 回 200 但 `/readyz` 回 503** → host 還在啟動 Lab 傳輸,等待或檢視 host log。
- **瀏覽器每個請求都回 401** → ``host_token`` 已設定但 ``loopback_bypass = false`` 且瀏覽器在非 loopback 來源。需透過 ``Authorization: Bearer`` 提供 token（L2 啟用時前端連線狀態機會自動提示）。

## 另請參閱

- [身份驗證](authentication.md) — 四層認證模型 + ``kt admin`` 營運者命令。
- [部署 — systemd](deployment-systemd.md) — 等價的非容器化方案。
- [部署 — 反向代理](deployment-reverse-proxy.md) — 分散式模式的 TLS 終止。
- [Laboratory](laboratory.md) — lab-host / lab-client 概念詳解。
