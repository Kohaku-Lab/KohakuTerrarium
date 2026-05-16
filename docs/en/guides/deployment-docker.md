---
title: Deployment — Docker
summary: Three Docker compose patterns for running KohakuTerrarium — AIO, host + same-box workers, and distributed host / worker.
tags:
  - guides
  - deployment
  - docker
---

# Deployment — Docker

KohakuTerrarium ships three first-party Docker images on GHCR:

| Image | Purpose | Ports |
|---|---|---|
| `ghcr.io/kohaku-lab/kohakuterrarium` | **AIO** — lab-host + embedded worker in one container | `8001` (HTTP), `8100` (Lab WS) |
| `ghcr.io/kohaku-lab/kohakuterrarium-host` | lab-host only (Studio + Web UI) | `8001`, `8100` |
| `ghcr.io/kohaku-lab/kohakuterrarium-client` | worker only (connects out to a host) | (outbound only) |

All three are signed via cosign keyless using the release workflow's OIDC
identity. Verify with:

```bash
cosign verify ghcr.io/kohaku-lab/kohakuterrarium:1.5.0 \
  --certificate-identity-regexp 'https://github.com/Kohaku-Lab/KohakuTerrarium/.github/workflows/release.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The compose files referenced below ship under `examples/deployment/`
in the source tree — copy the one closest to your shape and edit.

## Shape 1 — AIO (single container)

The smallest possible deployment. One container; host + one worker
share the same Python interpreter. Everything persists on a single
volume.

```yaml
# examples/deployment/compose-all.yml
services:
  kt:
    image: ghcr.io/kohaku-lab/kohakuterrarium:1.5.0
    ports:
      - "8001:8001"   # Studio web UI + API
      - "8100:8100"   # Lab — optional, only needed for external workers
    environment:
      # If omitted, the entrypoint generates one and logs it to stderr.
      KT_HOST_TOKEN: "${KT_HOST_TOKEN:-}"
    volumes:
      - kt-data:/home/kt/.kohakuterrarium

volumes:
  kt-data: {}
```

Start it:

```bash
docker compose -f examples/deployment/compose-all.yml up -d
docker compose -f examples/deployment/compose-all.yml logs -f kt
```

The first log line shows the generated token (or echoes your env var):

```
[kt-aio] Lab token: 8a3f7c…
[kt-aio] To attach external workers: KT_HOST_URL=ws://<this-host>:8100 KT_HOST_TOKEN=8a3f7c…
```

Visit `http://localhost:8001` — the Studio UI shows one connected
worker named `local-1`.

### When to use AIO

- Single-machine dev / staging — no cluster planning needed.
- A self-contained appliance you can ship as one container.
- Demos: one `docker run` is everything.

### Limits

- You cannot scale beyond what one container's CPU / memory allows.
- The embedded worker shares the host container's filesystem and
  credential store — no per-worker isolation.

## Shape 2 — host + N workers on the same box

When you want isolated worker processes (per-worker LLM credentials,
per-worker compute) but you do not need them on separate machines.
Use Docker secrets so the shared token is never in `docker inspect`.

```yaml
# examples/deployment/compose-host-clients.yml — abridged
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

Generate the token once, then start the stack:

```bash
mkdir -p secrets
openssl rand -hex 24 > secrets/kt_host_token
chmod 600 secrets/kt_host_token
docker compose -f examples/deployment/compose-host-clients.yml up -d
```

### Why two workers can talk

Both workers point `KT_HOST_URL` at the **service name** `host`,
which Docker's embedded DNS resolves inside the compose network.
No port publishing is required on the host service for the workers
themselves — only the Studio UI publishes port `8001`.

### Scaling

Add `worker-c` / `worker-d` blocks with their own `KT_CLIENT_NAME`s
and named volumes. Each worker has its own
`~/.kohakuterrarium/` so LLM profiles and API keys can differ per
worker.

## Shape 3 — distributed: host on edge VPS, workers on home boxes

Run the host on a small VPS that has a stable public address; run
workers on whatever boxes have GPUs / API quota. The Lab WebSocket
is the only link the workers need.

### Host side (edge VPS)

```yaml
# examples/deployment/compose-distributed-host.yml
services:
  host:
    image: ghcr.io/kohaku-lab/kohakuterrarium-host:1.5.0
    ports:
      - "127.0.0.1:8001:8001"  # bind to loopback; nginx fronts it
    environment:
      # Bind the Lab WS to all interfaces so external workers can reach it.
      # nginx terminates TLS in front (see deployment-reverse-proxy guide).
      KT_LAB_BIND: 0.0.0.0:8100
      KT_HOST_TOKEN_FILE: /run/secrets/kt_host_token
    secrets: [kt_host_token]
    volumes:
      - kt-host-data:/home/kt/.kohakuterrarium
secrets:
  kt_host_token:
    file: ./secrets/kt_host_token
```

Put nginx (or Cloudflare Tunnel) in front of port `8001` / `8100`
for TLS. The
[reverse-proxy guide](deployment-reverse-proxy.md) covers the nginx
config in full.

### Worker side (home box)

```yaml
# examples/deployment/compose-distributed-client.yml
services:
  worker:
    image: ghcr.io/kohaku-lab/kohakuterrarium-client:1.5.0
    environment:
      KT_HOST_URL: wss://kt-host.example.com/lab
      KT_HOST_TOKEN: "${KT_HOST_TOKEN}"  # paste the host's token
      KT_CLIENT_NAME: home-gpu-1
    volumes:
      - kt-worker-home-data:/home/kt/.kohakuterrarium
```

`wss://` (TLS-WebSocket) is mandatory across the public internet —
the shared token authenticates the worker but does not encrypt the
channel.

## Health probes

Every image exposes `/healthz` and `/readyz` on the HTTP port. Use
them for:

- Docker `HEALTHCHECK` (already wired in the official Dockerfiles).
- Kubernetes liveness / readiness probes.
- Reverse-proxy upstream health checks (so nginx withdraws traffic
  during a rolling restart).

`/healthz` is "process is up." `/readyz` is "lab transport is bound
and accepting clients" — use it for load-balancer rotation.

## Persistent state

Each container expects a writable
`/home/kt/.kohakuterrarium` volume. That directory holds:

- LLM profiles + API keys (`api_keys.yaml`, `llm_profiles.json`)
- MCP server registrations (`mcp_servers.yaml`)
- Codex OAuth tokens (`codex-auth.json`)
- Session stores (`sessions/*.kohakutr`)
- The host's generated token (`host-token`)

Treat the volume as you would any production database directory:
back it up, encrypt at rest if your threat model requires it, and
do not share it between hosts.

## Upgrading

Pin a version tag in production (`:1.5.0`), not `:latest`. Read the
release notes for breaking changes, then:

```bash
docker compose pull
docker compose up -d
```

Compose recreates each service one at a time; with the readiness
healthcheck wired, the host fully boots before workers reconnect.

## Troubleshooting

- **Workers stay `unreachable`** in `/api/nodes` → check the worker
  container logs for `lab-client: missing required configuration`
  (no token) or `connection refused` (wrong `KT_HOST_URL`).
- **Host logs show repeated `auth_failed`** → worker tokens diverge
  from the host's. Re-generate, re-distribute the file secret.
- **`/healthz` returns 200 but `/readyz` returns 503** → host is
  still booting the Lab transport. Wait or check the host logs.

## See also

- [Deployment — systemd](deployment-systemd.md) — same shapes, no
  containers.
- [Deployment — reverse proxy](deployment-reverse-proxy.md) — TLS
  termination for the distributed shape.
- [Laboratory](laboratory.md) — what the lab-host / lab-client roles
  actually are, conceptually.
