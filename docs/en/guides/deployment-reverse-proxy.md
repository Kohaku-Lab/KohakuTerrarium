---
title: Deployment — reverse proxy & TLS
summary: nginx and Cloudflare Tunnel configurations for fronting `kt serve --mode lab-host`.
tags:
  - guides
  - deployment
  - nginx
  - tls
  - cloudflare
---

# Deployment — reverse proxy & TLS

The KohakuTerrarium host binds plaintext HTTP and plaintext
WebSocket by default. For anything beyond a single trusted LAN, put
a reverse proxy in front: TLS termination, hostname-based routing,
and HTTP/2 multiplexing.

This guide covers two production patterns:

1. **nginx** — the operator owns the TLS certificate (Let's Encrypt
   via `certbot`).
2. **Cloudflare Tunnel** — Cloudflare terminates TLS; no public
   port on the origin.

Both are equally valid; the choice is operational.

## What you are proxying

Two upstream ports on the host:

| Port | What it serves | URL paths |
|---|---|---|
| `8001` | Studio Web UI + REST + chat WebSockets | `/`, `/api/*`, `/ws/*`, `/healthz`, `/readyz` |
| `8100` | Lab WebSocket — worker control plane | `/lab` (everything is upgraded to WebSocket) |

The Lab connection from each worker is **a single long-lived
WebSocket**. The proxy must:

- Upgrade `Connection` + `Upgrade` headers (default WebSocket flow).
- Disable response buffering — Lab frames must reach the worker
  the instant they arrive.
- Not enforce a request timeout on the Lab path — connections
  legitimately live for days.

## Pattern 1 — nginx

The shipped template at
`examples/deployment/nginx-host.conf` is a complete starting point;
the abridged version:

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

    # Studio UI + REST + chat WebSockets.
    location / {
        proxy_pass http://kt_http;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # WebSocket upgrade headers — Studio's chat WS uses them.
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        # Streaming responses must not be buffered.
        proxy_buffering off;
        proxy_read_timeout 1h;
    }

    # Lab — worker control plane.  Single long-lived WS per worker.
    location /lab {
        proxy_pass http://kt_lab;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        # Don't time out long-lived WSs.
        proxy_read_timeout 24h;
        proxy_send_timeout 24h;
    }

    # Use /readyz so the LB withdraws traffic during a restart.
    location = /healthz { proxy_pass http://kt_http/healthz; access_log off; }
    location = /readyz  { proxy_pass http://kt_http/readyz;  access_log off; }
}
```

Then on the workers, point `KT_HOST_URL` at the public TLS URL:

```
KT_HOST_URL=wss://kt.example.com/lab
```

### Getting the certificate

```bash
sudo certbot --nginx -d kt.example.com --redirect --hsts
```

certbot writes the renewal cron / systemd-timer automatically.

### Restricting Studio to a VPN

Studio has no built-in authentication — anyone reaching port `8001`
sees the UI. The recommended pattern: keep `/` and `/api/*` reachable
only via WireGuard / Tailscale (or `allow … deny all;` for a fixed
IP), but expose `/lab` publicly so workers can reach it. Both can
share the same `server { }` block; just split `location` rules:

```nginx
location / {
    allow 10.0.0.0/8;     # WireGuard subnet
    allow 100.64.0.0/10;  # Tailscale subnet
    deny all;
    proxy_pass http://kt_http;
    # ... headers as above
}
```

## Pattern 2 — Cloudflare Tunnel

Cloudflare Tunnel exposes a service without opening a public port
on the origin. The `cloudflared` daemon dials out to Cloudflare's
edge and answers requests over that pre-established tunnel.

```bash
# 1. Authenticate
cloudflared tunnel login
cloudflared tunnel create kt-host
# 2. DNS — point a hostname at the tunnel
cloudflared tunnel route dns kt-host kt.example.com
```

Then a tunnel config (`/etc/cloudflared/config.yml`):

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

Run as a service:

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

### Caveats

- Cloudflare's WebSocket support requires the orange-cloud (proxied)
  toggle on the DNS record. Grey-cloud (DNS-only) bypasses the
  tunnel.
- Cloudflare imposes a 100s idle timeout on free-tier WebSockets.
  Workers handle this by reconnecting, but expect periodic
  `connection reset by peer` lines in worker logs. Paid plans
  raise the limit; or use Pattern 1 if predictable longevity
  matters.

## Verifying

From outside the network:

```bash
# Studio UI + /readyz over TLS
curl -fsS https://kt.example.com/readyz

# Worker WS — should accept the upgrade
curl -fsS -I -H "Connection: Upgrade" -H "Upgrade: websocket" \
     -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: $(openssl rand -base64 16)" \
     https://kt.example.com/lab
# Expect: HTTP/2 101 Switching Protocols  (or 4xx with an explicit reason)
```

## Hardening checklist

- [x] HTTPS only — the HTTP listener `301`s to HTTPS.
- [x] TLS 1.2+ (1.3 preferred); old ciphers disabled.
- [x] HSTS (`Strict-Transport-Security`) once you are confident no
      mixed-content callers remain — bake it into the certbot
      `--hsts` flag.
- [x] Studio (port `8001` paths `/` and `/api/*`) restricted to a
      trusted network OR fronted by an auth proxy.
- [x] Lab path (`/lab`) reachable from worker boxes only — if all
      workers are behind a NAT, restrict by source IP.
- [x] Cloudflare: enable "Bot Fight Mode" off on the Lab path (the
      WS handshake from a Python client trips heuristics).

## See also

- [Deployment — Docker](deployment-docker.md) — what runs behind the
  proxy.
- [Deployment — systemd](deployment-systemd.md) — the native install
  alternative.
- [Laboratory](laboratory.md) — what the Lab WebSocket carries.
