---
title: 部署 — 反向代理与 TLS
summary: 为 `kt serve --mode lab-host` 前置 nginx 与 Cloudflare Tunnel 的配置。
tags:
  - guides
  - deployment
  - nginx
  - tls
  - cloudflare
---

# 部署 — 反向代理与 TLS

KohakuTerrarium host 默认绑定明文 HTTP 与明文 WebSocket。除非完全在受信 LAN 中使用，否则都应在前面放一个反向代理：用于 TLS 终止、按 hostname 路由，以及 HTTP/2 复用。

本指南覆盖两种生产级方案：

1. **nginx** — 由运维持有 TLS 证书（Let's Encrypt + `certbot`）。
2. **Cloudflare Tunnel** — Cloudflare 终止 TLS；origin 不暴露任何公网端口。

两者等效，按运维取舍。

## 你要代理的内容

host 的两个上游端口：

| 端口 | 提供的服务 | URL 路径 |
|---|---|---|
| `8001` | Studio Web UI + REST + chat WebSocket | `/`、`/api/*`、`/ws/*`、`/healthz`、`/readyz` |
| `8100` | Lab WebSocket — worker 控制平面 | `/lab`（所有请求都升级为 WebSocket） |

每个 worker 与 host 的 Lab 连接是 **一条长生命周期 WebSocket**。代理必须：

- 升级 `Connection` + `Upgrade` 头（标准 WebSocket 流程）。
- 关闭响应缓冲 — Lab 帧到达瞬间就要传递给 worker。
- 不对 Lab 路径设置请求超时 — 连接合理地存活数日。

## 方案 1 — nginx

随附模板位于 `examples/deployment/nginx-host.conf`，是完整可用的起点；节选：

```nginx
# /etc/nginx/sites-available/kohakuterrarium.conf
upstream kt_http {
    server 127.0.0.1:8001;
    keepalive 32;
}

upstream kt_lab {
    server 127.0.0.1:8100;
    keepalive 32;
}

map $http_upgrade $connection_upgrade {
    default upgrade;
    "" close;
}

server {
    listen 80;
    listen [::]:80;
    server_name kt.example.com;
    location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name kt.example.com;

    ssl_certificate     /etc/letsencrypt/live/kt.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/kt.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;

    # Studio UI + REST + chat WebSocket
    location / {
        proxy_pass http://kt_http;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # WebSocket 升级 — Studio chat WS 需要
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        # 流式响应不能缓冲
        proxy_buffering off;
        proxy_read_timeout 1h;
    }

    # Lab — worker 控制平面，每个 worker 一条长生命周期 WS
    location /lab {
        proxy_pass http://kt_lab;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        # 不要超时长生命周期 WS
        proxy_read_timeout 24h;
        proxy_send_timeout 24h;
    }

    # 用 /readyz 触发 LB 撤流（滚动重启时）
    location = /healthz { proxy_pass http://kt_http/healthz; access_log off; }
    location = /readyz  { proxy_pass http://kt_http/readyz;  access_log off; }
}
```

然后在 worker 端把 `KT_HOST_URL` 指向公网 TLS URL：

```
KT_HOST_URL=wss://kt.example.com/lab
```

### 取得证书

```bash
sudo certbot --nginx -d kt.example.com --redirect --hsts
```

certbot 会自动写入续期 cron / systemd-timer。

### 限制 Studio 仅 VPN 访问

Studio 本身没有内置认证 — 任何能访问端口 `8001` 的人都能看到 UI。推荐做法：将 `/` 与 `/api/*` 限制为只能通过 WireGuard / Tailscale 访问（或固定 IP 的 `allow … deny all;`），但 `/lab` 对外开放以便 worker 接入。两者可共用一个 `server { }` 块，只需切分 `location` 规则：

```nginx
location / {
    allow 10.0.0.0/8;     # WireGuard 子网
    allow 100.64.0.0/10;  # Tailscale 子网
    deny all;
    proxy_pass http://kt_http;
    # ... 头部同上
}
```

## 方案 2 — Cloudflare Tunnel

Cloudflare Tunnel 让你无需对外开放任何端口即可暴露服务。`cloudflared` 守护进程从 origin 拨号到 Cloudflare 边缘，请求经由这条预建隧道送达。

```bash
# 1. 认证
cloudflared tunnel login
cloudflared tunnel create kt-host
# 2. DNS — 把 hostname 指向 tunnel
cloudflared tunnel route dns kt-host kt.example.com
```

tunnel 配置（`/etc/cloudflared/config.yml`）：

```yaml
tunnel: kt-host
credentials-file: /etc/cloudflared/<tunnel-uuid>.json

ingress:
  - hostname: kt.example.com
    path: /lab*
    service: ws://127.0.0.1:8100
  - hostname: kt.example.com
    service: http://127.0.0.1:8001
  - service: http_status:404
```

作为服务运行：

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

### 注意事项

- Cloudflare 的 WebSocket 支持需要 DNS 记录开启橙色云朵（proxied）。灰色云朵（DNS-only）会绕过 tunnel。
- 免费套餐对 WebSocket 强制 100s 空闲超时；worker 会自动重连，但 worker 日志中会偶发 `connection reset by peer`。付费套餐放宽该限制；如对持久度要求严格，请选方案 1。

## 验证

从外网执行：

```bash
# 通过 TLS 测试 Studio UI + /readyz
curl -fsS https://kt.example.com/readyz

# 测试 worker WS — 应返回升级响应
curl -fsS -I -H "Connection: Upgrade" -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: $(openssl rand -base64 16)" \
     https://kt.example.com/lab
# 期望：HTTP/2 101 Switching Protocols（或带明确原因的 4xx）
```

## 加固清单

- [x] 仅 HTTPS — HTTP listener `301` 跳转到 HTTPS。
- [x] TLS 1.2+（优先 1.3）；禁用过时密码套件。
- [x] 待确认无混合内容回调后再启用 HSTS（`Strict-Transport-Security`） — 直接由 certbot 的 `--hsts` 提供。
- [x] Studio（端口 `8001` 的 `/` 与 `/api/*`）限制在可信网络中或前置 auth proxy。
- [x] Lab 路径（`/lab`）仅 worker 机器可达 — 若所有 worker 都在 NAT 后，按源 IP 限制。
- [x] Cloudflare：Lab 路径关闭「Bot Fight Mode」，否则 Python 客户端的 WS 握手会被启发式拦截。

## 另请参阅

- [部署 — Docker](deployment-docker.md) — 反向代理后面跑的是什么。
- [部署 — systemd](deployment-systemd.md) — 原生安装替代方案。
- [Laboratory](laboratory.md) — Lab WebSocket 承载的内容。
