---
title: 部署 — Docker
summary: KohakuTerrarium 的三种 Docker compose 部署模式 — AIO、host + 同机 worker、分布式 host / worker。
tags:
  - guides
  - deployment
  - docker
---

# 部署 — Docker

KohakuTerrarium 在 GHCR 上提供三个官方 Docker 镜像：

| 镜像 | 用途 | 端口 |
|---|---|---|
| `ghcr.io/kohaku-lab/kohakuterrarium` | **AIO** — lab-host + 内嵌 worker 在同一容器 | `8001` (HTTP)，`8100` (Lab WS) |
| `ghcr.io/kohaku-lab/kohakuterrarium-host` | 仅 lab-host（Studio + Web UI） | `8001`，`8100` |
| `ghcr.io/kohaku-lab/kohakuterrarium-client` | 仅 worker（向外连接到 host） | （仅出站） |

三个镜像均通过 release 工作流的 OIDC 身份用 cosign keyless 签名。验证：

```bash
cosign verify ghcr.io/kohaku-lab/kohakuterrarium:1.5.0 \
  --certificate-identity-regexp 'https://github.com/Kohaku-Lab/KohakuTerrarium/.github/workflows/release.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

下文引用的 compose 文件位于源码 `examples/deployment/` 下 — 选取最接近你需求的模板并修改即可。

## 模式 1 — AIO（单容器）

最小可部署单元。一个容器；host + 一个 worker 共享同一 Python 解释器。所有状态持久化到单一卷。

```yaml
# examples/deployment/compose-all.yml
services:
  kt:
    image: ghcr.io/kohaku-lab/kohakuterrarium:1.5.0
    ports:
      - "8001:8001"   # Studio web UI + API
      - "8100:8100"   # Lab — 可选，只有需要外部 worker 才需要
    environment:
      # 若未设置，entrypoint 会自动生成并打印到 stderr。
      KT_HOST_TOKEN: "${KT_HOST_TOKEN:-}"
    volumes:
      - kt-data:/home/kt/.kohakuterrarium

volumes:
  kt-data: {}
```

启动：

```bash
docker compose -f examples/deployment/compose-all.yml up -d
docker compose -f examples/deployment/compose-all.yml logs -f kt
```

启动后第一行日志会显示自动生成的 token（或回显你设置的环境变量）：

```
[kt-aio] Lab token: 8a3f7c…
[kt-aio] To attach external workers: KT_HOST_URL=ws://<this-host>:8100 KT_HOST_TOKEN=8a3f7c…
```

打开 `http://localhost:8001` — Studio UI 会显示一个名为 `local-1` 的已连接 worker。

### 何时使用 AIO

- 单机开发 / staging — 无需集群规划。
- 作为单镜像出货的自包含产品。
- 演示：一条 `docker run` 即可。

### 局限

- 无法超出单容器 CPU / 内存。
- 内嵌 worker 与 host 容器共享文件系统与凭证 — 没有 per-worker 隔离。

## 模式 2 — host + N 个同机 worker

适用于你希望 worker 进程相互隔离（per-worker LLM 凭证 / per-worker 计算），但又不需要跨机器部署的情形。使用 Docker secret 让共享 token 永远不出现在 `docker inspect` 中。

```yaml
# examples/deployment/compose-host-clients.yml — 节选
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

生成 token 后启动：

```bash
mkdir -p secrets
openssl rand -hex 24 > secrets/kt_host_token
chmod 600 secrets/kt_host_token
docker compose -f examples/deployment/compose-host-clients.yml up -d
```

### 为何两个 worker 能找到 host

两个 worker 的 `KT_HOST_URL` 指向 **服务名** `host`，由 compose 网络的内置 DNS 解析。host 服务无需对外暴露 — 只有 Studio UI 需要发布端口 `8001`。

### 扩容

添加 `worker-c` / `worker-d` 块，给它们独立的 `KT_CLIENT_NAME` 与命名卷。每个 worker 拥有自己的 `~/.kohakuterrarium/`，因此 LLM 配置与 API key 可按 worker 区分。

## 模式 3 — 分布式：host 在边缘 VPS，worker 在家中机器

将 host 部署在拥有稳定公网地址的小型 VPS 上；worker 部署在拥有 GPU / API quota 的机器上。Worker 唯一所需的网络链路是 Lab WebSocket。

### Host 端（边缘 VPS）

```yaml
# examples/deployment/compose-distributed-host.yml
services:
  host:
    image: ghcr.io/kohaku-lab/kohakuterrarium-host:1.5.0
    ports:
      - "127.0.0.1:8001:8001"  # 绑定 loopback；由 nginx 前置
    environment:
      # Lab WS 绑定所有接口以便外部 worker 接入；nginx 前置 TLS 终止
      # （详见 deployment-reverse-proxy 指南）。
      KT_LAB_BIND: 0.0.0.0:8100
      KT_HOST_TOKEN_FILE: /run/secrets/kt_host_token
    secrets: [kt_host_token]
    volumes:
      - kt-host-data:/home/kt/.kohakuterrarium
secrets:
  kt_host_token:
    file: ./secrets/kt_host_token
```

在端口 `8001` / `8100` 前部署 nginx（或 Cloudflare Tunnel）以提供 TLS。完整 nginx 配置见[反向代理指南](deployment-reverse-proxy.md)。

### Worker 端（家中机器）

```yaml
# examples/deployment/compose-distributed-client.yml
services:
  worker:
    image: ghcr.io/kohaku-lab/kohakuterrarium-client:1.5.0
    environment:
      KT_HOST_URL: wss://kt-host.example.com/lab
      KT_HOST_TOKEN: "${KT_HOST_TOKEN}"  # 粘贴 host 的 token
      KT_CLIENT_NAME: home-gpu-1
    volumes:
      - kt-worker-home-data:/home/kt/.kohakuterrarium
```

跨公网必须使用 `wss://`（TLS-WebSocket） — 共享 token 仅验证 worker 身份，不加密信道。

## 健康检测

每个镜像都在 HTTP 端口上提供 `/healthz` 与 `/readyz`，可用于：

- Docker `HEALTHCHECK`（官方 Dockerfile 已经接好）
- Kubernetes liveness / readiness 探针
- 反向代理上游健康检查（滚动重启时 nginx 会自动剔除该后端）

`/healthz` 表示「进程存活」。`/readyz` 表示「Lab 传输已绑定并接受 worker」 — 用它作为负载均衡轮转的依据。

## 持久化状态

每个容器需要可写的 `/home/kt/.kohakuterrarium` 卷，其中包含：

- LLM 配置 + API key（`api_keys.yaml`、`llm_profiles.json`）
- MCP server 注册表（`mcp_servers.yaml`）
- Codex OAuth token（`codex-auth.json`）
- Session store（`sessions/*.kohakutr`）
- Host 自动生成的 token（`host-token`）

按数据库目录的级别对待该卷：备份、视威胁模型决定是否落盘加密，且不要在 host 之间共享。

## 升级

生产环境请固定版本号（`:1.5.0`），不要用 `:latest`。阅读 release notes 后：

```bash
docker compose pull
docker compose up -d
```

Compose 会逐个重建服务；只要 readiness healthcheck 已接好，host 会先完全启动再让 worker 重连。

## 锁定 API — `[auth]` 配置

任何 compose 形态都可以通过 Docker secrets 添加四层认证配置，
成为多用户 / 加锁主机。可直接套用的示例：

```yaml
# examples/deployment/compose-host-auth.yml — 节选
services:
  kohakuterrarium:
    image: ghcr.io/kohaku-lab/kohakuterrarium:1.5.0
    environment:
      KT_AUTH_HOST_TOKEN_FILE: /run/secrets/host_token
      KT_AUTH_ADMIN_TOKEN_FILE: /run/secrets/admin_token
      KT_AUTH_MULTI_USER: "required"
      KT_AUTH_REGISTRATION: "invite_only"
      KT_AUTH_LOOPBACK_BYPASS: "0"   # 前面有代理；loopback ≠ 信任
    secrets:
      - host_token
      - admin_token
    volumes:
      - kt-config:/root/.kohakuterrarium
    ports:
      - "127.0.0.1:8001:8001"        # 绑到 localhost；Caddy 在前
    restart: unless-stopped

secrets:
  host_token:  { file: ./secrets/host_token }
  admin_token: { file: ./secrets/admin_token }

volumes:
  kt-config:
```

启动栈之前在主机上生成 secret 文件：

```bash
mkdir -p secrets
python -c "import secrets;print(secrets.token_hex(32))" > secrets/host_token
python -c "import secrets;print(secrets.token_hex(32))" > secrets/admin_token
chmod 600 secrets/*
```

容器内 ``kt admin users add operator --role admin`` 会在
``kt-config`` volume 上的共享 ``auth.db`` 中创建第一个用户。

完整的四层模型、所有 CLI 动词（``kt admin set-host-token`` /
``set-admin-token`` / ``invitations create`` 等）以及前端连接状态
机如何发现已启用的层级，参见[身份验证](authentication.md)。

## 故障排查

- **worker 在 `/api/nodes` 中始终 `unreachable`** → 检查 worker 容器日志，是否出现 `lab-client: missing required configuration`（缺 token）或 `connection refused`（`KT_HOST_URL` 错）。
- **host 日志反复出现 `auth_failed`** → worker 端 token 与 host 不一致；重新生成并重新分发文件 secret。
- **`/healthz` 返回 200 但 `/readyz` 返回 503** → host 还在启动 Lab 传输；等待或查看 host 日志。
- **浏览器每个请求都返回 401** → ``host_token`` 已设置但 ``loopback_bypass = false`` 且浏览器在非 loopback 来源。需通过 ``Authorization: Bearer`` 提供 token（L2 启用时前端连接状态机会自动提示）。

## 另请参阅

- [身份验证](authentication.md) — 四层认证模型 + ``kt admin`` 运营者命令。
- [部署 — systemd](deployment-systemd.md) — 等价的非容器化方案。
- [部署 — 反向代理](deployment-reverse-proxy.md) — 分布式模式的 TLS 终止。
- [Laboratory](laboratory.md) — lab-host / lab-client 概念详解。
