# Serving Layer

The serving layer (`src/kohakuterrarium/serving/`) provides a transport-agnostic Python API for hosting and managing creatures and terrariums. It sits between the core framework and interface layers such as the HTTP API, web UI, desktop app, and CLI-managed service surfaces.

## Key Difference: Serving vs HTTP API

| Layer | Location | Purpose |
|-------|----------|---------|
| **Serving** | `src/kohakuterrarium/serving/` | Python API for runtime management. Part of the core package. |
| **HTTP API** | `kohakuterrarium/api/` | FastAPI application exposing the serving layer over REST + WebSocket. Not part of the library. |

The serving layer is the single source of truth for all runtime operations. The HTTP API (and any future interface) delegates to it.

## KohakuManager

`KohakuManager` is the central service manager. All runtime operations go through it. It optionally creates a `SessionStore` for each terrarium, enabling persistent session recording.

```python
from kohakuterrarium.serving import KohakuManager

manager = KohakuManager(session_dir="~/.kohakuterrarium/sessions")
```

### Standalone Agent Operations

```python
# Create and start an agent from a config path
agent_id = await manager.create_agent(config_path="examples/agent-apps/swe_agent")

# Get status
status = manager.get_agent_status(agent_id)
# {"agent_id": "agent_abc123", "name": "swe_agent", "running": True, "tools": [...]}

# Stream a chat response
async for chunk in manager.chat(agent_id, "Fix the bug in main.py"):
    print(chunk, end="")

# List all agents
agents = manager.list_agents()

# Stop
await manager.stop_agent(agent_id)
```

### Terrarium Operations

```python
# Create and start a terrarium (with auto session recording)
tid = await manager.create_terrarium(config_path="examples/terrariums/novel_terrarium")

# Mount an individual creature as an agent on the terrarium
await manager.terrarium_mount(tid, "swe", "creatures/swe")

# Chat with a specific target (root agent or creature)
async for chunk in manager.terrarium_chat(tid, "root", "Fix the auth bug"):
    print(chunk, end="")

# Get status (creatures, channels, running state)
status = manager.get_terrarium_status(tid)

# Send a message to a channel
msg_id = await manager.send_to_channel(tid, "ideas", "Write about space", sender="human")

# Hot-plug: add a creature at runtime
from kohakuterrarium.terrarium.config import CreatureConfig

config = CreatureConfig(
    name="editor",
    config_path="examples/agent-apps/editor_agent",
    listen_channels=["draft"],
    send_channels=["feedback"],
)
await manager.add_creature(tid, config)

# Hot-plug: add a channel and wire it
await manager.add_channel(tid, "alerts", "broadcast", "System alerts")
await manager.wire_channel(tid, "editor", "alerts", "listen")

# Stream channel events (transport-agnostic async iterator)
async for event in manager.stream_channel_events(tid, channels=["ideas"]):
    print(f"[{event.channel}] {event.sender}: {event.content}")

# Stop
await manager.stop_terrarium(tid)

# Shutdown everything
await manager.shutdown()
```

## AgentSession

`AgentSession` wraps an `Agent` with streaming chat. It manages the output queue so callers get an async iterator of text chunks.

```python
from kohakuterrarium.serving import AgentSession

# Create from config path (starts the agent automatically)
session = await AgentSession.from_path("examples/agent-apps/swe_agent")

# Stream chat
async for chunk in session.chat("What files are in this directory?"):
    print(chunk, end="")

# Check status
status = session.get_status()

# Stop
await session.stop()
```

### How Streaming Works

When `chat(message)` is called:

1. Any stale output in the queue is cleared
2. `agent.inject_input(message)` runs as a background task
3. Output chunks arrive via the agent's output handler callback
4. The caller yields chunks as they arrive
5. After injection completes, remaining output is drained

## Event Types

The serving layer defines two transport-agnostic event dataclasses:

### ChannelEvent

Represents a channel message observed in a terrarium.

| Field | Type | Description |
|-------|------|-------------|
| `terrarium_id` | `str` | Which terrarium |
| `channel` | `str` | Channel name |
| `sender` | `str` | Who sent it |
| `content` | `str` | Message body |
| `message_id` | `str` | Unique ID |
| `timestamp` | `datetime` | When created |
| `metadata` | `dict` | Extra data |

### OutputEvent

Represents an agent output event (text chunk, tool activity).

| Field | Type | Description |
|-------|------|-------------|
| `agent_id` | `str` | Which agent |
| `event_type` | `str` | `"text"`, `"tool_start"`, `"tool_done"`, `"tool_error"`, etc. |
| `content` | `str` | Event content |
| `timestamp` | `datetime` | When created |
| `metadata` | `dict` | Extra data |

## Session persistence

When `session_dir` is provided, `KohakuManager` automatically creates a `SessionStore` for each terrarium. The store records:

- all creature events (text, tool calls, tool results, trigger fires)
- conversation snapshots
- creature state such as scratchpad and related runtime metadata
- channel messages via `on_send` callbacks
- sub-agent conversations and metadata

That stored history is useful not only for resume. It also becomes searchable session history that can later be queried through FTS or vector-based memory search.

The recording uses `SessionOutput`, an `OutputModule` added as a secondary output on each creature's output router. This captures events without modifying the processing loop, following the same pattern as the WebSocket `StreamOutput`. See [Execution Model](execution.md) for more on secondary outputs.

## Unified WebSocket Architecture

Each terrarium instance uses a single WebSocket connection that carries ALL creature output and ALL channel messages. The shared event log and `StreamOutput` class live in `kohakuterrarium/api/events.py`, which both REST routes and WebSocket handlers depend on. The WebSocket transport is implemented in `kohakuterrarium/api/ws/chat.py`:

- `/ws/terrariums/{id}`: single WS carrying all events for a terrarium
- `/ws/creatures/{id}`: per-creature WS for standalone agents

Events are multiplexed with a `source` field identifying the creature or channel. Channel messages are captured via `on_send` callbacks on `BaseChannel`, which fire on every send (both queue and broadcast channels). See [Channels](channels.md) for details on `on_send` callbacks.
