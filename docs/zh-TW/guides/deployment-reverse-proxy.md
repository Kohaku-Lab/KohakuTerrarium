---
title: 部署 — 反向代理與 TLS
summary: 為 `kt serve --mode lab-host` 前置 nginx 與 Cloudflare Tunnel 的設定。
tags:
  - guides
  - deployment
  - nginx
  - tls
  - cloudflare
---

# 部署 — 反向代理與 TLS

KohakuTerrarium host 預設綁定明文 HTTP 與明文 WebSocket。除非完全在受信 LAN 中使用,否則都應在前面放反向代理：用於 TLS 終止、按 hostname 路由,以及 HTTP/2 多工。

本指南涵蓋兩種生產級方案：

1. **nginx** — 由運維持有 TLS 憑證（Let's Encrypt + `certbot`）。
2. **Cloudflare Tunnel** — Cloudflare 終止 TLS;origin 不開放任何公網連接埠。

兩者等效,依運維抉擇。

## 你要代理的內容

host 的兩個上游連接埠：

| 連接埠 | 提供的服務 | URL 路徑 |
|---|---|---|
| `8001` | Studio Web UI + REST + chat WebSocket | `/`、`/api/*`、`/ws/*`、`/healthz`、`/readyz` |
| `8100` | Lab WebSocket — worker 控制平面 | `/lab`（所有請求皆升級為 WebSocket） |

每個 worker 與 host 的 Lab 連線是 **一條長生命週期 WebSocket**。代理必須：

- 升級 `Connection` + `Upgrade` 標頭（標準 WebSocket 流程）。
- 關閉回應緩衝 — Lab 訊框到達瞬間就要傳遞給 worker。
- 不對 Lab 路徑設置請求逾時 — 連線合理地存活數日。

## 方案 1 — nginx

內附範本位於 `examples/deployment/nginx-host.conf`,是完整可用的起點;節錄：

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
        # WebSocket 升級 — Studio chat WS 需要
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        # 串流回應不能緩衝
        proxy_buffering off;
        proxy_read_timeout 1h;
    }

    # Lab — worker 控制平面,每個 worker 一條長生命週期 WS
    location /lab {
        proxy_pass http://kt_lab;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        # 不要逾時長生命週期 WS
        proxy_read_timeout 24h;
        proxy_send_timeout 24h;
    }

    # 用 /readyz 觸發 LB 撤流(滾動重啟時)
    location = /healthz { proxy_pass http://kt_http/healthz; access_log off; }
    location = /readyz  { proxy_pass http://kt_http/readyz;  access_log off; }
}
```

然後在 worker 端把 `KT_HOST_URL` 指向公網 TLS URL：

```
KT_HOST_URL=wss://kt.example.com/lab
```

### 取得憑證

```bash
sudo certbot --nginx -d kt.example.com --redirect --hsts
```

certbot 會自動寫入續期 cron / systemd-timer。

### 限制 Studio 僅 VPN 存取

Studio 本身沒有內建驗證 — 任何能存取連接埠 `8001` 的人都能看到 UI。推薦做法：將 `/` 與 `/api/*` 限制為只能透過 WireGuard / Tailscale 存取(或固定 IP 的 `allow … deny all;`),但 `/lab` 對外開放以便 worker 接入。兩者可共用一個 `server { }` 區塊,只需切分 `location` 規則：

```nginx
location / {
    allow 10.0.0.0/8;     # WireGuard 子網
    allow 100.64.0.0/10;  # Tailscale 子網
    deny all;
    proxy_pass http://kt_http;
    # ... 標頭同上
}
```

## 方案 2 — Cloudflare Tunnel

Cloudflare Tunnel 讓你無需對外開放任何連接埠即可暴露服務。`cloudflared` 守護行程從 origin 撥號到 Cloudflare 邊緣,請求經由這條預建隧道送達。

```bash
# 1. 驗證
cloudflared tunnel login
cloudflared tunnel create kt-host
# 2. DNS — 把 hostname 指向 tunnel
cloudflared tunnel route dns kt-host kt.example.com
```

tunnel 設定（`/etc/cloudflared/config.yml`）：

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

作為服務執行：

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

### 注意事項

- Cloudflare 的 WebSocket 支援需要 DNS 紀錄開啟橘色雲朵(proxied)。灰色雲朵(DNS-only)會繞過 tunnel。
- 免費方案對 WebSocket 強制 100s 閒置逾時;worker 會自動重連,但 worker log 中會偶發 `connection reset by peer`。付費方案放寬該限制;若對持久度要求嚴格,請選方案 1。

## 驗證

從外網執行：

```bash
# 透過 TLS 測試 Studio UI + /readyz
curl -fsS https://kt.example.com/readyz

# 測試 worker WS — 應回傳升級回應
curl -fsS -I -H "Connection: Upgrade" -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: $(openssl rand -base64 16)" \
     https://kt.example.com/lab
# 預期：HTTP/2 101 Switching Protocols(或帶明確原因的 4xx)
```

## 強化清單

- [x] 僅 HTTPS — HTTP listener `301` 跳轉到 HTTPS。
- [x] TLS 1.2+(優先 1.3);停用過時 cipher。
- [x] 待確認無混合內容呼叫者後再啟用 HSTS(`Strict-Transport-Security`)— 直接由 certbot 的 `--hsts` 提供。
- [x] Studio(連接埠 `8001` 的 `/` 與 `/api/*`)限制在可信網路中或前置 auth proxy。
- [x] Lab 路徑(`/lab`)僅 worker 機器可達 — 若所有 worker 都在 NAT 後,依來源 IP 限制。
- [x] Cloudflare：Lab 路徑關閉「Bot Fight Mode」,否則 Python 客戶端的 WS handshake 會被啟發式攔截。

## 另請參閱

- [部署 — Docker](deployment-docker.md) — 反向代理後面跑的是什麼。
- [部署 — systemd](deployment-systemd.md) — 原生安裝替代方案。
- [Laboratory](laboratory.md) — Lab WebSocket 承載的內容。
