---
title: Laboratory layer
summary: How KohakuTerrarium spans multiple machines — the WebSocket-based transport, custom packet system, and the transparency tricks that let Studio and Terrarium treat a remote node as if it were local.
tags:
  - concepts
  - laboratory
  - multi-node
---

# Laboratory layer

The **Laboratory** (in the codebase: `kohakuterrarium.laboratory`,
informally "Lab") is the network layer that lets one KohakuTerrarium
host coordinate creatures running on remote machines. It sits between
the management tier (Studio) and the runtime engine (Terrarium), and
its entire job is to make the rest of the framework *not notice* that
the network is there.

This document covers the design end-to-end. For day-to-day operator
tasks (running `kt serve --mode lab-host`, adding workers, picking
node-targeted credentials) see the [Laboratory guide](../guides/laboratory.md).

## Two hierarchies, one mental model

KohakuTerrarium runs in two modes; the layering is identical except
for the network hop.

### Standalone

```
user-facing UI (web / CLI / TUI / desktop)
      │
      ▼
    Studio              ← management framework
   (catalog · identity · sessions · persistence · editors · attach)
      │
      ▼
    Terrarium           ← runtime engine: graph, channels, hot-plug
      │
      ▼
    Creatures           ← the actual agents (LLM + tools + …)
```

One process, no network, no Lab module imported. This is what
`kt serve` boots by default.

### Multi-node (lab-host + lab-clients)

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

Studio still calls one `TerrariumService` Protocol. Behind the
Protocol, `MultiNodeTerrariumService` fans out and routes per-creature
operations to the right node. Studio never imports the Lab.

The host process can also run agents in a "coordination engine" — a
local Terrarium kept for cross-node channel routing and for hosting
recipe-spawned creatures when no worker is targeted. Workers are
identical processes (same `Terrarium` class, same adapters, same
session store layout); their only configuration difference is that
they connect outward instead of accepting connections.

## Why WebSocket

The Lab transport is plain WebSocket (`wss://` in production), not
gRPC, raw TCP, or QUIC. Three reasons:

1. **It traverses Cloudflare / nginx / corporate proxies** unchanged.
   A single TCP/443 hop carries the entire protocol. No firewall
   rules, no separate signalling channel.
2. **The browser can speak it.** Studio's web UI and the worker
   client use the same wire format and the same envelope codec — the
   browser dashboard could itself appear as a Lab client in a future
   release without re-implementing anything.
3. **It's bidirectional and message-framed.** The L2 envelope sits
   one-to-one inside a WebSocket binary frame; we never have to
   reinvent message boundaries on top of a byte stream.

WebSocket is not load-bearing on the design — the
[transport layer](#l1-transport) is a small Protocol that
`InProcTransport` also implements (used by every test). Swapping in
QUIC or a Unix socket would mean writing a new `_internal/transport_*.py`.

## The packet system

Every byte between two Lab nodes is framed as an **envelope**. The
envelope is custom (not protobuf, not gRPC) for one concrete reason:
we need to ride raw binary payloads (file bundles, session-event
blobs, tokenizer state) without paying the base64 inflation a flat
msgpack design would force.

### Wire format (L2)

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

The header is msgpack (small, schema-flexible, fast). The payload is
arbitrary bytes — the L4 codec decides how to interpret them.

See `src/kohakuterrarium/laboratory/_internal/envelope.py` for the
implementation.

### Four layers

| Layer | Concern | Key files |
|-------|---------|-----------|
| **L1** Transport | byte stream between nodes (WebSocket or in-proc) | `_internal/transport_ws.py`, `_internal/transport_inproc.py` |
| **L2** Envelope | framing, routing metadata, signatures | `_internal/envelope.py` |
| **L3** Connection | handshake, heartbeat, addressing, membership | `_internal/host.py`, `_internal/client.py`, `_internal/protocol.py` |
| **L4** Verbs | user-visible delivery primitives + APP namespacing | `verbs.py` (`Channel`, `Topic`), `_internal/app.py` (`AppMessage`) |

### Envelope kinds

| Kind | Purpose |
|------|---------|
| `SEND` | point-to-point delivery (L4 `Channel.send`) — load-balanced across subscribers |
| `BROADCAST` | pub-sub fan-out (L4 `Topic.publish`) — every subscriber receives a copy |
| `APP` | structured application message: `{namespace, type, body}` with optional request/response correlation |
| `ACK` | acknowledgement for ack-required `SEND` |
| `HELLO` / `WELCOME` / `HEARTBEAT` | connection lifecycle |
| `CONTROL` | framework-internal (subscribe, register_creature, …) |

Almost everything Studio and Terrarium do over the wire is an
**APP** envelope. APP carries the namespace (e.g. `terrarium.runtime`,
`studio.identity`, `terrarium.session.sync`), a type (the verb within
the namespace), and a msgpack body. Both ends register *extension
handlers* per namespace; the dispatch table is the only thing they
need to agree on.

## Transparency goal 1: Studio sees one system

Studio never knows whether a creature lives in-process or on a remote
machine. The `TerrariumService` Protocol
(`src/kohakuterrarium/terrarium/service.py`) has methods like
`add_creature`, `list_creatures`, `chat`, `connect`. Three
implementations satisfy it:

- `LocalTerrariumService` — calls the in-process Terrarium directly.
- `RemoteTerrariumService` — packs the arguments into an APP request
  on `terrarium.runtime`, sends it, unpacks the response. One
  instance per connected worker.
- `MultiNodeTerrariumService` — owns a `LocalTerrariumService` plus
  one `RemoteTerrariumService` per worker, routes per-creature ops by
  a `creature_id → home_node` registry, fans out global ops.

In lab-host mode Studio holds the composite. Every Studio method that
once called `engine.add_creature(...)` now calls
`service.add_creature(..., on_node="worker-1")`. The hop disappears.

## Transparency goal 2: Terrarium sees one engine

Channels and graph topology are also single-namespace. A creature
calling `send_channel("ch1", "hello")` on worker-1 should deliver to
every listener — including listeners on worker-2 — exactly as if both
creatures lived in the same process. The Lab achieves this with two
mechanisms:

- **Cross-node connect** (`terrarium/multi_node_replication.py`).
  When the user calls `service.connect(creatureA, creatureB)` and the
  two creatures live on different workers, the host:
  1. Adds the channel object on both workers' graphs.
  2. Wires sender's send-side on sender's worker.
  3. Wires receiver's listen-side on receiver's worker.
  4. Cross-subscribes via `terrarium.broadcast` so a local send on
     sender's worker fans out to receiver's worker, where the inject
     path replays into the local channel registry.
  5. Records the link in `service._cluster_links` (a `set[frozenset]`
     of `(node_id, graph_id)` pairs).
- **Output-wiring forwarder** (`TerrariumOutputWireAdapter`).
  Output-wire targets that point at creatures on other workers are
  resolved through the broadcast adapter the same way.

After cross-node connect, the two workers' graphs form a *cluster* —
a logical multi-creature graph spread across machines. The host's
`MultiNodeTerrariumService` uses the cluster set to fold listings
(`list_creatures` shows the union; `list_channels` deduplicates by
name), so the frontend sees one connected component even though
each worker keeps its own engine graph.

Hot-plug works the same way: `group_add_node` (a tool a privileged
creature can call) hits the runtime adapter, which either spawns
locally or routes via the service to another worker depending on
whether the recipe specified a target node.

## Sessions: mirrored by syncing events

The most unique design choice in the Lab is how persistence works.

### Authoritative writer + read-side mirror

Every running creature has exactly one **authoritative** `SessionStore`
(a SQLite file via KohakuVault) — on the worker that hosts it. There
is exactly one writer per session file. All events the creature
generates land in that file first.

At the moment the worker attaches the store, it also installs a
**`SessionEventTee`** (`session/sync.py`). The tee:

1. Synchronously snapshots the store's `meta` and enqueues it as the
   first wire message.
2. Subscribes to the store's append callback.
3. Pumps every event over an APP message on namespace
   `terrarium.session.sync` (type `meta` for the snapshot, type
   `event` for each append), addressed to the host.

The host runs a **`SessionMirrorWriter`** that receives those
messages and writes them into a **mirror store** under its own session
directory (`<KT_CONFIG_DIR>/sessions/mirror/<graph_id>.kohakutr`). The
mirror is a real `SessionStore`, identical to the worker's, just
opened in append-only fashion against the wire-driven stream.

Studio's read APIs (history, viewer, search, fork) read from the
mirror, never from the worker. The mirror is local SQLite, so paging
through ten thousand events doesn't round-trip per page.

### Ordering and durability

- The tee uses a per-session outbound asyncio queue. Events are
  delivered in append order to the host. If the link drops, the
  pump retries with bounded backoff — events are buffered, not
  lost.
- The mirror writer applies meta keys first (so `config_path` /
  `config_snapshot` land before any event), then appends events as
  they arrive. Per-key writes are isolated: a single failing key
  doesn't abort the rest.
- The host's mirror file is best-effort. The worker's local file is
  always the source of truth for resume.

### Why this design

Two reasons it's mirror-by-event rather than mirror-by-snapshot:

1. **Live reads.** Studio's history viewer can show events the
   moment they arrive; no polling, no eventual-consistency
   surprises on the order of seconds.
2. **Disconnection survival.** If a worker drops mid-conversation,
   the host still has every event up to the disconnect — Studio
   keeps responding to history queries — and when the worker
   reconnects, the mirror is already current; the tee picks up
   from the next event with no resync RPC.

The trade-off is that a session lives in two places. We always treat
the worker's file as authoritative; the mirror exists for read
convenience and as the disk image we push BACK to the worker on
resume (see below).

### Why we don't shard the session

Each creature/graph has exactly one file. We considered per-event
fan-out to multiple mirrors and rejected it because:

- KohakuVault's SQLite append is already fast (~50 µs per event).
- One file simplifies fork / search / viewer code paths.
- The mirror is a faithful replica; you can `cp` it off and resume
  on any node.

## Resume: pushing the disk image back

Resume runs the same `engine.adopt_session(path)` it always has —
but in multi-node mode, the path lives on the host and the engine
lives on a worker. The host bridges that gap:

1. The user picks a session in the "Saved" tab and clicks **Resume on
   worker-1**. The frontend sends
   `POST /api/sessions/{sid}/resume {"on_node": "worker-1"}`.
2. The route opens the mirror file, checkpoints any in-memory writes
   (`mirror.checkpoint(sid)` flushes the SQLite WAL), reads the raw
   bytes, and streams them to worker-1's `terrarium.files` adapter
   under the `config://resume/` scope.
3. Once the bytes are on the worker's disk, the route calls
   worker-1's `terrarium.session.resume` adapter with the absolute
   path of the just-pushed file.
4. The worker's adapter calls `engine.adopt_session(local_path)`
   which reads meta, dispatches to either single-creature
   (`_resume_agent_into_engine`) or multi-creature
   (`_resume_terrarium_into_engine`) rebuild, attaches the store,
   and starts every adopted creature.
5. The worker's `WorkerSessionAttacher` installs a new
   `SessionEventTee`; subsequent events from the resumed creature(s)
   flow back to the host mirror as if they had been spawned there
   in the first place.

### Why a config snapshot lives in the meta

For a recipe-defined creature, the worker can `Agent.from_path(...)`
because the recipe folder exists on the worker's filesystem. But
inline-spawned creatures (the SDK case, and recipes where the user
typed `--home-dir` to isolate worker disks) often have no folder to
load from on this machine. To make those resumable on any node, the
worker's `_ensure_store_meta`
(`src/kohakuterrarium/laboratory/adapters/_worker_session.py`)
captures the full `AgentConfig` via `pack_agent_config` and stores it
under `meta["config_snapshot"]`. The resume path
(`session/resume.py::_rebuild_agent`) prefers `config_path` when the
folder exists, and falls back to `unpack_agent_config(snapshot)`
otherwise.

### Single-creature resume (CF-6 baseline)

For one creature on one worker:

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

### Multi-creature graph resume (CF-6 cluster)

For a cluster spanning multiple workers — each worker hosts part of
the cluster's connected component — the user passes a `members`
list:

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

The route:

1. Validates every named worker is connected (so we don't push to
   some and fail on others, leaving a half-resumed cluster).
2. For each member: runs the single-creature resume flow against the
   member's `(sid, on_node)`.
3. After every member is back up, **re-issues `service.connect`** for
   each cross-node link encoded in the cluster meta, so
   `_cluster_links` is repopulated and channel sends fan out the same
   way they did before close.
4. Returns the combined `Session` handle with `cluster_members`.

The cluster member list is persisted at `stop_session` time into
each member's mirror meta, so a cluster can be auto-discovered from
any member's saved file (the route does this when `members` is
omitted).

**This is tested end-to-end** by `tests/e2e/test_multinode_journey.py`
step *32g CF-6 cluster resume*: it spawns alpha on w1 + bravo on w2,
forms a cross-node channel between them, drives chat traffic across
the bridge, deletes both active sessions, then resumes via the
members API and verifies `_cluster_links` is repopulated and chat
still routes correctly.

### Runtime topology mutations: snapshot + replay

A recipe (`terrarium.yaml`) describes the topology a graph starts
with. Everything the user (or a privileged tool) adds AFTER the
recipe loads — extra channels via `service.add_channel`, extra wires
via `service.connect`, removals via `disconnect` / `unwire` — lives
only in the engine's in-memory `GraphTopology`. Without persistence
it would be lost on every close + resume.

The engine writes a *full snapshot* of the current topology into
`store.meta["runtime_topology"]` after every mutation
(`add_channel`, `remove_channel`, `connect`, `disconnect`,
`wire_creature`, `unwire_creature`). The shape:

```
{
    "channels":     [{"name": str, "description": str}, ...],
    "listen_edges": {creature_id: [channel_name, ...]},
    "send_edges":   {creature_id: [channel_name, ...]}
}
```

At resume time, `_resume_terrarium_into_engine` rebuilds the
recipe-described topology first, then calls
`topology_snapshot.replay(engine, sid)` which adds every channel +
wire from the saved snapshot that isn't already in the graph.
Because the snapshot is *full* (not a delta log), user removals are
also reflected — anything the user removed simply isn't in the
snapshot.

Implementation: `src/kohakuterrarium/terrarium/topology_snapshot.py`.
Tested by `tests/integration/test_runtime_topology_resume.py`.

### Known limits today

| Scenario | Status |
|----------|--------|
| 1 creature on 1 worker | ✅ tested (journey 32d) |
| Recipe-spawned multi-creature graph on coordination engine | ✅ uses standard `_resume_terrarium_into_engine` |
| Cluster of N=2 workers, 1 creature each, cross-node bridged | ✅ tested (journey 32g / CF-6) |
| Cluster of 3+ workers | ⚠ untested (same mechanism, just more members) |
| Cluster with multiple creatures per worker | ⚠ untested but should work — each worker's resume rebuilds its own graph independently |
| Per-creature `on_node` inside a recipe file | ❌ not supported — recipe schema has no node field. Compose manually via individual `add_creature(on_node=…)` + `service.connect` |
| Resume while a target worker is offline | ❌ returns 404 with the missing worker's name — operator must reconnect first |

## Identity: local-first

LLM credentials are per-process, not per-cluster. The 1.5.x default
is **local-first**:

1. The worker's `IdentityCache.sync_api_key(provider)` first reads
   the worker's own `<KT_CONFIG_DIR>/api_keys.yaml` and provider
   env vars.
2. Only on miss does it fall back to whatever the host most recently
   pushed via `studio.identity`.
3. Codex OAuth tokens (`<KT_CONFIG_DIR>/codex-auth.json`) are the
   same — local first, host second. **Codex tokens MUST be local**
   because OAuth refresh is process-bound: trying to use the host's
   token from a worker process always re-prompts the user.

The `--home-dir` flag (`kt serve`, `kt lab-client`) sets
`KT_CONFIG_DIR`, so each worker can carry its own independent
credential store on disk.

In Settings → Providers, the user picks **Manage on:** to choose
which node's credential store they're editing. Saving a key with a
worker selected sends the write via Lab APP to that worker's
`StudioIdentityAdapter`, which persists into the worker's local
file. Codex login is the same — clicking **Codex login** while a
worker is selected runs the OAuth flow *on that worker*, so the
browser opens on the worker's machine and the resulting token lives
on the worker's disk.

## Files, deployment, and sandboxing

- **`terrarium.files`** — scope-bounded file IO over Lab APP. Five
  scopes: `workspace://<creature>`, `memory://<creature>`,
  `package://<name>`, `recipe://<id>`, `config://`. Streamed
  read/write for >512 KB payloads; idempotent atomic commit (target
  files held open by an adopted SessionStore aren't rewritten — see
  `_op_write_commit`).
- **`studio.deploy`** — `push_creature_bundle`: walks a creature
  folder, computes per-file SHA, pushes via `terrarium.files`,
  atomic rename into `recipe://<name>/...`. Re-pushes are
  idempotent via the hash check, so a worker that already has a
  recipe doesn't redownload it.
- **`terrarium.pty`** — proxy a worker shell session to a host-side
  WebSocket. Frontend's terminal panel works against a remote
  creature's working directory unchanged.
- Path-form `add_creature("./my-creature/")` is REJECTED if the
  worker's filesystem can't see the path. Use `studio.deploy` to
  push the bundle first, then spawn with the worker-side
  `recipe://` path.

## Cluster-wide fold

A user opens a session in the dashboard and sees **one** chat with
**one** creature list, even if the cluster spans three workers. The
fold happens in two places:

- **Listings** (`studio.sessions.cluster_fold`). Saved-sessions list,
  active-sessions list, runtime-graph snapshot all union members of
  the same cluster under the primary sid (lex-smallest member sid =
  primary). The frontend never sees N separate sessions for one
  cluster.
- **Operational** (chat WebSocket, channels, memory, group tools).
  Per-creature targeting is resolved through the `_home` registry,
  so `chat` and `inject_input` reach the right worker even when the
  caller addresses the creature by name.

## Glossary at a glance

| Term | Meaning |
|------|---------|
| **Lab / Laboratory** | the `kohakuterrarium.laboratory` package; the network layer. |
| **Host** | the process running `kt serve --mode lab-host`. Owns Studio + `HostEngine`. May also host agents via a coordination engine. |
| **Worker** | a process running `kt lab-client`. Hosts creatures, exposes them over Lab adapters. |
| **Node** | a host or a worker — anyone speaking the Lab protocol. Addressed by `node_id` (`_host` or the client's `--name`). |
| **Adapter** | a class implementing one or more APP namespaces (e.g. `TerrariumRuntimeAdapter` serves the `terrarium.runtime` namespace). |
| **`TerrariumService`** | the Protocol Studio calls. Three impls: `Local`, `Remote`, `MultiNode`. |
| **Cluster** | a set of cross-node-connected graphs. Tracked in `MultiNodeTerrariumService._cluster_links`. One logical session from the user's perspective. |
| **Mirror** | the host-side replica of a worker's session file, populated by `SessionEventTee` → `SessionMirrorWriter`. Source of all read APIs. |
| **Cluster fold** | union-find over `_cluster_links` to map every member sid to the cluster's primary sid; used everywhere the frontend lists or addresses a cluster. |

## Further reading

- [Laboratory guide](../guides/laboratory.md) — how to actually run
  it.
- [Sessions](../guides/sessions.md) — persistence basics
  (single-node).
- [Terrarium](./multi-agent/terrarium.md) — the engine the Lab wraps.
