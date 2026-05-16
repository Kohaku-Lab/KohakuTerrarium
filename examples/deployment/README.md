# Deployment examples

Reference Docker compose files and a reverse-proxy template for the
three KohakuTerrarium 1.5 deployment shapes.

## Pick a compose file

| File | When to use |
|---|---|
| [`compose-all.yml`](compose-all.yml) | "I just want it running on my home machine." One container, AIO. |
| [`compose-host-clients.yml`](compose-host-clients.yml) | Same machine, you want separate per-worker config dirs (each worker holds its OWN Codex login / API keys). |
| [`compose-distributed-host.yml`](compose-distributed-host.yml) | Host on an edge VPS, behind nginx/Cloudflare. Run this on the host machine. |
| [`compose-distributed-client.yml`](compose-distributed-client.yml) | Worker on a dedicated box (GPU server etc.). Run this on each worker machine. |

## Reverse proxy

[`nginx-host.conf`](nginx-host.conf) — drop-in template for nginx
with Let's Encrypt TLS, proxying both the frontend (`8001`) and the
lab transport (`8100`) under one TLS endpoint.

Cloudflare alternative: enable WebSockets in the dashboard and use
the same backend container compose.

## Token setup

Every multi-container compose reads `KT_HOST_TOKEN` from
`./secrets/kt_host_token`. Generate one before `docker compose up`:

```bash
mkdir -p secrets
openssl rand -hex 24 > secrets/kt_host_token
chmod 600 secrets/kt_host_token
```

For `compose-distributed-client.yml` on a separate worker box, copy
the same token from the host's `secrets/kt_host_token` into the
worker's local `secrets/kt_host_token`.

## See also

- [Deployment with Docker](../../docs/en/guides/deployment-docker.md) —
  full operator guide.
- [Deployment with systemd](../../docs/en/guides/deployment-systemd.md) —
  bare-metal install via `kt service install`.
- [Reverse proxy](../../docs/en/guides/deployment-reverse-proxy.md) —
  nginx + Cloudflare + TLS notes.
