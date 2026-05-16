# `kohakuterrarium.laboratory`

The Laboratory layer — cross-node coordination for KohakuTerrarium.

Sits between **Studio** (managing framework) and **Terrarium**
(multi-agent runtime), enabling creatures to live on multiple machines
coordinated by a single host. Channels and broadcasts span the network
transparently; creature code doesn't change.

This README documents the Laboratory transport and its Terrarium/Studio
adapters as they exist today.

User-facing docs:

- Concept (4-layer wire stack, L4 verbs, session sync, resume,
  identity, cluster fold): `docs/en/concepts/laboratory.md`.
- Operator guide (`kt serve --mode lab-host`, `kt lab-client`,
  programmatic embedding): `docs/en/guides/laboratory.md`.

## Public API surface (L4)

Everything user code needs lives at the top level of this package:

```python
from kohakuterrarium.laboratory import (
    HostConfig,
    ClientConfig,
    Channel,
    Topic,
    LabNode,
    AckTimeoutError,
)
```

Plus the host/client engines themselves, which are framework-side
infrastructure but currently live in `_internal/` until the wiring
phase exposes them more formally:

```python
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.client import (
    ClientConnector,
    AuthFailedError,
    ProtocolMismatchError,
    NameConflictError,
)
from kohakuterrarium.laboratory._internal.transport_ws import (
    WebSocketTransport,
)
from kohakuterrarium.laboratory._internal.transport_inproc import (
    InProcTransport,  # for tests / embedded use
)
```

`_internal` modules below L4 (envelope, streams, membership,
addressing, backpressure, auth, protocol, control) are framework-owned
and not part of the user contract.

## Quickstart — host + two clients in one process

```python
import asyncio
from kohakuterrarium.laboratory import Channel, ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.client import ClientConnector
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.transport_ws import (
    WebSocketTransport,
)


async def main():
    # Start the host on an ephemeral loopback port.
    host_transport = WebSocketTransport()
    host = HostEngine(
        HostConfig(
            bind_host="127.0.0.1",
            bind_port=0,
            token="shared-secret",
        ),
        host_transport,
    )
    await host.start()

    # Read the actual bound port.
    sock_host, sock_port = host._server.local_addr  # type: ignore[union-attr]
    url = f"ws://{sock_host}:{sock_port}/"

    # Bring up two clients.
    def cfg(name):
        return ClientConfig(
            client_name=name, host_url=url, token="shared-secret",
        )

    client_a = ClientConnector(cfg("agent-a"), WebSocketTransport())
    client_b = ClientConnector(cfg("agent-b"), WebSocketTransport())
    await client_a.start()
    await client_b.start()

    # Point-to-point Channel (SEND).
    ch_a = Channel("work", client_a)
    ch_b = Channel("work", client_b)
    await ch_b.subscribe()
    await ch_a.send(b"hello b")
    print(await ch_b.recv())  # → b"hello b"

    await client_a.stop()
    await client_b.stop()
    await host.stop()


asyncio.run(main())
```

## L4 surface — three verb-shapes for app traffic

### `Channel` — point-to-point Send

One sender, one receiver. When multiple subscribers exist for the same
channel name, the host load-balances (round-robin); one envelope lands
on exactly one subscriber.

```python
ch = Channel("task-queue", node)
await ch.subscribe()            # register interest with the host
await ch.send(payload)          # fire-and-forget delivery
await ch.send(payload, ack=True, timeout=5.0)
                                # await ack from receiver (raises
                                # AckTimeoutError on expiry)
msg = await ch.recv()           # block for next inbound payload
async for msg in ch.messages(): # iterator form; auto-subscribes
    ...
```

### `Topic` — pub-sub Broadcast

Multiple senders, multiple subscribers. Every published payload lands
on every subscriber.

```python
t = Topic("alerts", node)
await t.subscribe()
await t.publish(payload)
msg = await t.recv()
async for msg in t.messages():
    ...
```

### `APP` envelope + extensions — structured application messages

For richer application protocols than opaque-byte channels can carry
(Studio session management, metrics, custom RPC, …), use the
``APP`` envelope kind via the extension dispatch system:

```python
from kohakuterrarium.laboratory import AppMessage


async def studio_handler(msg: AppMessage):
    """Handle inbound messages for the 'studio' namespace.

    Return value (if not None and request_id is set) is sent back as
    the response body for request/response RPC.
    """
    match msg.type:
        case "list_sessions":
            return {"sessions": [...]}
        case "session_started":
            log_session(msg.body)
            return None   # fire-and-forget; no response
        case _:
            return None


# Host side: register the namespace handler
host.register_app_extension("studio", studio_handler)

# Client side: send a request and await the response
response = await client.request(
    to_node=HOST_NODE_ID,
    namespace="studio",
    type="list_sessions",
    body={"filter": "active"},
    timeout=5.0,
)

# Or fire-and-forget
await client.notify(
    to_node=HOST_NODE_ID,
    namespace="studio",
    type="session_started",
    body={"id": "abc123"},
)
```

**Routing model** (host decides based on ``to_node``):
- ``HOST_NODE_ID`` → dispatched to the host's registered extension.
- A specific client id → forwarded to that client (its extension fires).
- A ``channel://`` name → load-balanced across listeners (like Send).

**Namespaces** isolate extension protocols. Studio uses ``"studio"``,
metrics ``"metrics"``, your app whatever you pick. Each namespace has
at most one handler per node.

**Request/response correlation** uses the envelope-level
``request_id`` / ``in_reply_to`` fields. The framework matches them
automatically; user code only sees the awaitable result from
``request()`` and the return value from handlers.

### Custom `CONTROL` types

For framework-adjacent traffic that isn't user-facing protocol but
needs host-side handling (cluster-wide membership flags, deployment
hooks, …), register a ``CONTROL`` handler instead:

```python
async def my_directive(sender, env, fields):
    ...

host.register_control_handler("my_directive", my_directive)
```

Built-in CONTROL types (``subscribe``, ``unsubscribe``,
``register_creature``, ``unregister_creature``) can't be overridden.
CONTROL envelopes addressed to a specific node (not the host) are
forwarded like SEND, so peer-to-peer CONTROL works too.

### `Replicate` — not in 1.5.0

The third verb in the canonical design — **Replicate** (consensus log
+ universal state system) — is **not in 1.5.0**. It ships with the
state system in a later release; see design.md §6.4 and §7.

## Configuration

### `HostConfig`

```python
HostConfig(
    bind_host="127.0.0.1",          # "0.0.0.0" to expose externally
    bind_port=8100,                 # 0 to use an ephemeral port
    token="",                       # shared cluster token (empty = auth off)
    heartbeat_interval_seconds=5.0,
    heartbeat_timeout_seconds=15.0,
    backpressure_buffer_size=1000,
)
```

### `ClientConfig`

```python
ClientConfig(
    client_name="my-client",        # must be unique within the cluster
    host_url="ws://host:8100/_lab",
    token="shared-secret",
    capabilities=(),                # advertised to host for placement
    heartbeat_interval_seconds=5.0,
    reconnect_initial_delay_seconds=1.0,
    reconnect_max_delay_seconds=30.0,
    backpressure_buffer_size=1000,
)
```

## Locked design decisions

- **WebSocket only.** No QUIC, no TCP+TLS planned.
- **Shared token auth.** No mTLS, no per-user tokens.
- **Static config discovery.** No mDNS, no service registry.
- **Per-channel FIFO**, at-most-once by default, at-least-once with
  `ack=True` (5-minute dedupe window).
- **Mirrored session storage** (host + each client maintain their own
  KohakuVault; sync via the Lab).
- **Protocol-version handshake.** Lab declares its own semver
  (`LAB_PROTOCOL_VERSION = "1.0"`) independent of the KT framework
  version.
- **Naive bounded-buffer backpressure** (default 1000 envelopes).

## Error types

```python
from kohakuterrarium.laboratory import (
    AckTimeoutError,            # Channel.send(ack=True) timed out
    AppMessageError,            # malformed APP envelope payload
    ExtensionNotFoundError,     # APP for an unregistered namespace
)
from kohakuterrarium.laboratory._internal.client import (
    AuthFailedError,            # bad token; permanent
    ProtocolMismatchError,      # incompatible Lab versions; permanent
    NameConflictError,          # client_name already in use
    RequestTimeoutError,        # client.request(...) timed out
)
from kohakuterrarium.laboratory._internal.backpressure import (
    BackpressureError,          # send buffer full with wait=False
)
from kohakuterrarium.laboratory._internal.envelope import (
    EnvelopeDecodeError,
)
from kohakuterrarium.laboratory._internal.protocol import (
    ProtocolError,
)
```

## Wire format

Envelope = `[4-byte big-endian uint32 header_len][msgpack header][raw payload][raw sig]`.

The msgpack header (via `kohakuvault.DataPacker('msgpack')`) carries
routing metadata + `payload_len` + `sig_len`. Payload and signature
ride on the wire raw — no base64, no string escaping for binary data.

Header fields:
- **Required**: `from`, `to`, `kind`, `stream_id`, `seq`
- **Always present**: `flags`, `payload_len`, `sig_len`
- **Optional**: `request_id`, `in_reply_to` (omitted when unset to
  keep the wire compact)

Envelope kinds (extensible — peers running newer versions may add more
kinds without breaking older receivers, which log unsupported kinds
and ignore them):
- `SEND`, `BROADCAST`, `APP`, `LOG` — L4 verbs
- `ACK` — ack-required SEND response
- `HELLO`, `WELCOME` — handshake
- `HEARTBEAT` — liveness
- `CONTROL` — framework-internal directives

For APP envelopes the payload is a msgpack-encoded
`{namespace, type, body}` dict; see the extension section above.

Unknown fields in the header are tolerated; missing required fields
are rejected with `EnvelopeDecodeError`.

## Testing

```bash
pytest tests/unit/laboratory/
```

290 tests cover envelope framing (including the optional correlation
fields), protocol handshake, auth, both transports, L3 streams /
membership / addressing / backpressure, host engine, client connector,
the L4 verbs (Send + Broadcast), the APP extension dispatch system
(request/response, fire-and-forget, peer-to-peer routing), pluggable
CONTROL handlers, end-to-end over real WebSocket, and reconnection /
failure modes. Coverage is ≥ 92% on the laboratory module.

## What's NOT here yet

- **Replicate verb + state system** — future work.
- **Additional L4 paradigms** from design.md §8 (Agentic Raft, worker
  pool, sharded actors, hot standby, federation, …).

The transport, Terrarium adapters, Studio routing, and `kt lab-client`
worker command are implemented; the remaining items build on the same
wire layer without changing the public L4 primitives documented here.
