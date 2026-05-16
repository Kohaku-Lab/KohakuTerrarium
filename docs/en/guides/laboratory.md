---
title: Laboratory (multi-node)
summary: Run KohakuTerrarium across two or more machines — kt lab-host + kt lab-client, per-worker credentials, programmatic usage, multi-node terrariums, and resume.
tags:
  - guides
  - laboratory
  - multi-node
  - serving
---

# Laboratory (multi-node)

The Laboratory layer lets one **host** process coordinate creatures
running on remote **worker** machines. This guide is the practical
how-to. For the design rationale see
[Concepts → Laboratory](../concepts/laboratory.md).

## When to use it

Use lab-host mode when:

- You want to run creatures on a different machine than the one
  serving the UI (a GPU box, a sandbox VM, a cloud node).
- You need each creature to have its **own** OAuth login (Codex,
  ChatGPT subscription) — OAuth is process-bound so it cannot be
  shared, and the local-first identity model on a worker means each
  worker can hold its own tokens.
- You want a creature's filesystem actions (workspace files,
  subprocess shells, MCP servers) to land on a different host than
  the dashboard.

For everything else (single-user, single-machine), stay on
`kt serve` / `kt web` / `kt app` — they're simpler.

## Boot the host

```bash
# Foreground (recommended while you're setting up)
kt serve start --mode lab-host \
               --foreground \
               --lab-bind 0.0.0.0:8100 \
               --lab-token "$(openssl rand -hex 24)" \
               --home-dir ~/.kohakuterrarium-host

# Daemon (production, exits the terminal)
kt serve start --mode lab-host \
               --lab-bind 0.0.0.0:8100 \
               --lab-token <shared-secret> \
               --home-dir /var/lib/kohakuterrarium/host
```

Flags:

- `--mode lab-host` — accept worker connections in addition to the
  normal web stack. The host runs **no creatures by default** in
  lab-host mode; every spawn must target a worker (or fall back to
  the recipe-only coordination engine).
- `--lab-bind host:port` — the WebSocket endpoint workers connect
  to. Use a bind address reachable from your workers; for
  production put it behind nginx / Cloudflare with TLS termination.
- `--lab-token` — shared secret. Every worker presents this in its
  Hello handshake; mismatched tokens are rejected. **Always set
  this** when binding to a non-loopback address.
- `--home-dir` — re-homes `KT_CONFIG_DIR`. API keys, OAuth tokens,
  LLM profiles, MCP servers, sessions all live under here. Defaults
  to `~/.kohakuterrarium` when omitted.

The web UI still serves on `--host:--port` (default `127.0.0.1:8001`)
exactly as in standalone mode; `--lab-bind` is a *second* listener for
worker connections.

## Connect a worker

On another machine (or another shell on the same machine):

```bash
kt lab-client \
  --host  wss://your-host.example/lab        \
  --token <same-shared-secret>               \
  --name  worker-gpu-1                       \
  --home-dir ~/.kohakuterrarium-workers/gpu1
```

Flags:

- `--host` — `ws://` for plaintext or `wss://` for TLS. If you're
  using Cloudflare or an nginx proxy, this is the public endpoint;
  the Lab protocol traverses WebSocket-aware proxies unchanged.
- `--token` — must match the host's `--lab-token`.
- `--name` — the node id the host knows this worker by. Must be
  unique among connected workers.
- `--home-dir` — **per-worker** config home. Give each worker its
  own directory so their `api_keys.yaml`, Codex OAuth tokens, and
  session files don't collide. This is the only sound way to use
  Codex from a worker.
- `--session-dir` — optional override; defaults to
  `<home-dir>/sessions`.

When the worker connects, the host logs a CONTROL `register_creature`
trace and the dashboard's site picker gains a new entry.

## Per-worker provider credentials

`Settings → Providers` has a **Manage on:** dropdown that picks the
node whose credential store you're editing.

- **Host** — keys + Codex tokens land on the host (the lab-host
  process's `--home-dir`).
- **A worker name** — keys + Codex login route to that worker via
  Lab APP; the worker writes to its OWN `--home-dir/api_keys.yaml`
  and starts its OWN OAuth browser flow.

The local-first lookup means a creature on `worker-gpu-1` looks up
its OpenAI key in:

1. `worker-gpu-1`'s `<--home-dir>/api_keys.yaml`
2. `OPENAI_API_KEY` env on the worker
3. The host's identity store (via the Lab APP `studio.identity`
   namespace) — only if (1) and (2) miss.

For Codex specifically: the OAuth refresh token is process-bound, so
**Codex must be logged in on the worker that uses it**. Hosts cannot
share Codex tokens with workers in a way that survives refresh.

## Spawn a creature on a worker

### From the UI

In the dashboard's "New creature" modal, the **Site** picker shows
every connected worker plus `Host`. Pick a worker, configure as
usual, click Spawn. The frontend posts:

```http
POST /api/sessions/active/creature
{
  "config_path": "/abs/path/to/creature.yaml",
  "on_node": "worker-gpu-1"
}
```

(In lab-host mode, `on_node` is required for `start_creature` —
spawning on the host is rejected because the host runs no agents.)

### From the HTTP API

Every session/topology endpoint accepts `on_node` for new spawns
and routes per-creature ops by the home registry once spawned.

```bash
# Spawn a recipe-defined creature on worker-gpu-1
curl -X POST http://localhost:8001/api/sessions/active/creature \
     -H 'Content-Type: application/json' \
     -d '{"config_path": "/home/user/creatures/researcher",
          "on_node": "worker-gpu-1"}'
```

### Programmatically

There are TWO different programmatic surfaces; pick the one that
matches what you're building:

#### A) You're INSIDE the running `kt serve --mode lab-host`

For example: a custom HTTP route, a plugin, or a background task
spawned from the same process. Use FastAPI's dependency injection
to receive the active service:

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

In lab-host mode the injected `service` is the running
`MultiNodeTerrariumService`. **You cannot just call `get_service()`
at module load** — it's a dependency provider whose result depends
on the API boot path (`api/app.py`'s startup hook calls
`set_service(...)` only when `--mode lab-host` was passed).

#### B) You're EMBEDDING a lab-host inside your own Python program

If you're writing a daemon / Python entry point and want to drive a
multi-node cluster directly (no FastAPI), build the host + service
yourself, the same way `api/app.py` does at boot:

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
    # 1. Lab transport — accept worker WebSocket connections.
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

    # 2. Coordination engine — a bare Terrarium that holds cross-node
    #    channel objects and (optionally) recipe-spawned creatures.
    #    Workers do the real agent work; this engine never receives
    #    add_creature for worker-bound spawns.
    coord = Terrarium(session_dir=str(config_dir() / "sessions"))

    # 3. The Protocol surface Studio / your app consumes.
    service = MultiNodeTerrariumService(host=host, coordination_engine=coord)

    # 4. Host-side adapters workers query (identity, catalog, the
    #    cross-node broadcast / output-wire forwarders, the session
    #    mirror writer that absorbs worker session-event sync).
    StudioIdentityAdapter(host)
    StudioCatalogAdapter(host, is_host=True)
    TerrariumBroadcastAdapter(coord, host)
    TerrariumOutputWireAdapter(coord, host)
    SessionMirrorWriter(host, config_dir() / "sessions" / "mirror")

    # 5. Wait for at least one worker to connect (workers register
    #    themselves on Hello/Welcome handshake).
    while not list(service.connected_nodes()):
        await asyncio.sleep(0.5)
    print("connected nodes:", list(service.connected_nodes()))

    # 6. Now spawn. ``on_node`` MUST name a connected worker —
    #    spawning on the host is rejected by start_creature in
    #    lab-host mode (the coordination engine is recipe-only).
    info = await service.add_creature(
        "/abs/path/to/creature/on/worker/disk",
        on_node="worker-gpu-1",
        is_privileged=True,
    )
    print(info.creature_id, info.graph_id, info.home_node)

    # 7. Drive chat over the Protocol.
    async for token in service.chat(info.creature_id, "hello"):
        print(token, end="", flush=True)

    await host.stop()


asyncio.run(main())
```

Two ways to make the path resolvable on the worker:

1. **Shared filesystem** — host and worker mount the same network
   share; no deployment needed.
2. **`studio.deploy`** — push the creature folder via Lab. The host
   walks the local folder, hashes every file, streams the bytes
   to the worker's `config://recipe/` scope, and returns the
   worker-side absolute path:

```python
from pathlib import Path
from kohakuterrarium.studio.deploy import deploy_creature_to_node

target_path = await deploy_creature_to_node(
    host,                # the HostEngine from step 1
    node_id="worker-gpu-1",
    src=Path("/home/user/creatures/researcher"),
)
info = await service.add_creature(target_path, on_node="worker-gpu-1")
```

For inline `AgentConfig` (no folder on disk anywhere), pass the
config object directly — it crosses the wire as a packed dict:

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

## Multi-node terrariums

A *terrarium* (multi-creature graph) can span workers via cross-node
channels. Recipe files don't (yet) include per-creature node
targeting, so build the topology imperatively:

```python
# Spawn alpha on worker-1, bravo on worker-2
alpha = await service.add_creature(alpha_cfg, on_node="worker-1")
bravo = await service.add_creature(bravo_cfg, on_node="worker-2")

# Connect across nodes — auto-creates the channel on both sides,
# wires send + listen, cross-subscribes via the broadcast adapter,
# records the cluster link.
result = await service.connect(alpha.creature_id, bravo.creature_id)
print(result.channel, result.delta_kind)  # "alpha_to_bravo", "cross_node"
```

After `connect`, `alpha` and `bravo` form a **cluster**. From every
read API (listing, history viewer, runtime graph snapshot, chat WS)
the cluster looks like one logical session with two creatures —
even though each worker still owns its own engine graph + session
file.

## Resume

Single-creature on a worker (resume the same session on the same
worker, or move it to a different one):

```http
POST /api/sessions/{sid}/resume
{"on_node": "worker-1"}
```

Cluster (a graph that spans multiple workers): pass the full member
list so every worker re-adopts its own piece, then the host
re-issues `service.connect` to repopulate `_cluster_links`:

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

The route auto-discovers `members` from the primary's persisted
`cluster_members` meta when you omit it — saved at `stop_session`
time so cluster topology survives a complete restart.

> Resume requires every named worker to be connected. If one is
> offline you get `404 not a connected lab node`; reconnect the
> worker and retry.

What actually happens on the wire (see the [concept doc](../concepts/laboratory.md)
for the full mechanism): the host opens each member's mirror file,
checkpoints in-memory writes, streams the bytes to the target
worker's `config://resume/` scope, then calls the worker's
`terrarium.session.resume` adapter; the worker rebuilds the engine
graph and reattaches the session store. Subsequent events flow
back to the host mirror as normal.

## Common workflows

### Move a creature off your laptop

```bash
# Your dev box keeps the dashboard, code editor, terminal.
kt serve start --mode lab-host --foreground \
               --lab-bind 0.0.0.0:8100 --lab-token T

# A bigger remote box runs the actual agent.
ssh gpu-box "kt lab-client --host wss://laptop.tailnet:8100 \
                           --token T --name gpu-box \
                           --home-dir ~/.kohakuterrarium-gpu"

# In the dashboard pick "gpu-box" in the New-creature site picker.
```

### Codex on a worker

The host can't share Codex tokens with workers. Log in on the worker:

1. Settings → Providers → set **Manage on:** to your worker.
2. Click **Codex login**. The OAuth browser opens *on the worker's
   machine* (or prints a device-code URL if headless).
3. Complete the flow. Token lands in
   `<worker --home-dir>/codex-auth.json`.
4. Spawn any creature on that worker with a Codex-backed model;
   it picks up the local token via the local-first IdentityCache.

### Two laptops, shared session

Both laptops connect to a third box running `kt serve --mode
lab-host`. Either laptop's dashboard sees the same session list,
the same creatures, and chats with them through the host's mirror
of the worker's session file. (The session file lives on whichever
worker hosts the creature; the mirror is on the host.)

### Distributed debugging

Each worker has its own filesystem, terminal (`TerrariumPtyAdapter`),
and process group. A creature on `worker-test` can run `pytest`
without touching the dev box. The host's PTY panel transparently
proxies stdin/stdout/stderr.

## Verify the wiring

```bash
# On the host
curl http://localhost:8001/api/runtime/graph | jq '.nodes'

# Should list both host and every connected worker, each with
# its creature roster.
```

```python
from kohakuterrarium.api.deps import get_service
svc = get_service()
print(list(svc.connected_nodes()))      # ['worker-1', 'worker-2']
print(svc._cluster_links)               # set of frozenset((node, gid)) pairs
```

If a worker shows up in `connected_nodes()` but its creatures
aren't visible: check the worker's stderr — most boot-time
adapter errors are logged at WARNING level on the worker side and
won't appear in the host's logs.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `"on_node" is required` on spawn | You're in lab-host mode and tried to spawn on the host. Pick a worker, or use a recipe (recipes still run on the coordination engine). |
| Worker connects then immediately disconnects | Token mismatch. Hello/Welcome handshake logs the rejection at INFO. |
| `worker 'X' resume failed: Session has no config_path or config_snapshot in metadata` | The mirror file pre-dates 1.5.x meta-sync ordering. Spawn the creature fresh and resume from the new file. |
| Codex `re-login due to process mismatch` errors | You're using the host's Codex token from a worker process. Log Codex in **on the worker** via Settings → Providers (with Manage on: set to that worker). |
| `worker 'X' is not a connected lab node` (resume) | Worker disconnected. Reconnect via `kt lab-client …` and retry. |
| Path-form `add_creature("./creature/")` fails on a remote spawn | The worker can't see that path. Either share the filesystem or `studio.deploy.deploy_creature_to_node(...)` first. |

## Reference

- CLI: see [`kt serve start`](../reference/cli.md) and
  `kt lab-client --help`.
- HTTP API: every existing `/api/sessions/...` endpoint accepts
  `on_node` (POST body field for spawns, `?node=` query for
  identity routes). See [HTTP API reference](../reference/http.md).
- Python: `kohakuterrarium.terrarium.MultiNodeTerrariumService`
  (lab-host mode), `RemoteTerrariumService` (per-worker handle),
  `kohakuterrarium.laboratory.ClientConnector` (the worker's
  client object — drive your own embedded worker).
- Concepts: [Laboratory](../concepts/laboratory.md) — wire format,
  session sync, resume semantics, identity model.
