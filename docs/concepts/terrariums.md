# Terrariums

## The Idea

Most multi-agent frameworks force you to choose: either everything is hierarchical (one boss, many workers) or everything is peer-to-peer (everyone equal). Both break down at scale.

KohakuTerrarium says: **use both, at different levels.**

```
    Vertical (inside a creature)       Horizontal (between creatures)

         Controller                  brainstorm <---> planner
         /       \                        |              |
    sub-agent  sub-agent              channels        channels
                                          |              |
    Hierarchical delegation           writer <-----> reviewer
    for task decomposition
                                     Peer collaboration
                                     for multi-role teams
```

The **creature** handles the vertical. The **terrarium** handles the horizontal. The boundary is clean: a creature does not know it is in a terrarium.

## What a Terrarium Does

A terrarium is **not** an agent. It has no LLM, no decision-making, no intelligence. It is pure wiring:

1. **Loads** standalone creature configs (unchanged from their solo use)
2. **Creates channels** connecting creatures
3. **Injects triggers** so creatures react to incoming channel messages
4. **Injects topology** into each creature's system prompt ("you can send to X, you receive from Y")
5. **Manages lifecycle** (start, stop, monitor)
6. **Provides an API** for external interaction

## The Opacity Principle

Creatures are opaque. The terrarium cannot inspect or modify their internals.

```
+-------------+     +-------------------+     +-----------------+
|  Creatures  |     |  Terrarium Layer  |     | Human Interface |
|  (opaque)   |<--->|  (wiring)         |<--->| (pluggable)     |
|             |     |                   |     |                 |
| Has:        |     | Has:              |     | Has:            |
| - LLM       |     | - Channels        |     | - CLI           |
| - Tools     |     | - Triggers        |     | - HTTP API      |
| - Sub-agents|     | - Lifecycle       |     | - Web UI        |
| - Memory    |     | - Prompt injection|     | - nothing       |
+-------------+     +-------------------+     +-----------------+
```

This mirrors the microservice pattern:
- Creature = microservice (private internals, external interface)
- Terrarium = service mesh (routing, lifecycle, no business logic)
- Channels = message queues

## Communication Model

Creatures communicate **explicitly** through channels. The terrarium never silently pipes creature output into channels.

**Receiving**: The terrarium injects `ChannelTrigger`s. Messages arrive automatically as events. The creature does not poll or call `wait_channel`.

**Sending**: The creature calls `send_message` when its workflow requires it. The LLM decides what to share.

```
brainstorm creature:
  1. Receives seed prompt (via trigger)
  2. Thinks, generates ideas
  3. Sends best idea to "ideas" channel  <-- explicit decision

planner creature:
  1. Receives idea (via trigger, automatic)
  2. Plans chapter outlines
  3. Sends each outline to "outline" channel  <-- explicit decision
```

For channel types, semantics, and topologies, see [Channels](channels.md).

## Topology Patterns

Different wiring patterns emerge from channel configuration:

```
Pipeline:                    Hub-and-spoke:
  A --> [ch] --> B --> [ch]      architect --> [tasks] --> worker_1
       --> C                     architect --> [tasks] --> worker_2
                                 worker_* --> [results] --> architect

Group chat:                  Hybrid:
  A --+                         A --> [queue] --> B
  B --+--> [broadcast] --> all  all <--> [broadcast] <--> all
  C --+
```

All topologies are just channel configuration; no code changes needed.

## The Root Agent

A terrarium can optionally declare a `root:` section. This is an inline agent config (same format as any creature config) that sits **outside** the terrarium:

```yaml
terrarium:
  name: swe_team

  root:
    base_config: creatures/root
    controller:
      model: gpt-5.4
      auth_mode: codex-oauth
      tool_format: native
    input:
      type: tui
    output:
      type: tui
      controller_direct: true

  creatures:
    - name: swe
      base_config: creatures/swe
      channels:
        listen: [tasks]
        can_send: [results]
```

The root agent is NOT a creature inside the terrarium. It does not participate in channels and the terrarium does not inject triggers or topology into it. Instead, the root agent uses terrarium management tools to control the terrarium from the outside:

- `terrarium_send`: inject messages into channels (dispatch tasks)
- `terrarium_observe`: watch a channel for results (runs in background)
- `terrarium_status`: check terrarium and creature status
- `terrarium_history`: get channel message history
- `creature_start` / `creature_stop`: hot-plug creatures

The root agent is force-given all 8 terrarium tools regardless of its creature config. It is typically built on the `root` creature, which inherits general capabilities.

The root config uses the same inheritance system as any other agent config (`base_config`, controller overrides, I/O overrides). The user talks to the root agent; the root agent orchestrates the team.

For event flow details (root agent dispatching tasks, observing results), see [Execution Model](execution.md).

## Defining a Terrarium

```yaml
terrarium:
  name: novel_writer
  creatures:
    - name: brainstorm
      config: ./creatures/brainstorm/
      channels:
        listen: [seed, team_chat]
        can_send: [ideas, team_chat]
    - name: planner
      config: ./creatures/planner/
      channels:
        listen: [ideas, team_chat]
        can_send: [outline, team_chat]
  channels:
    seed:      { type: queue, description: "User prompt" }
    ideas:     { type: queue, description: "Story concepts" }
    outline:   { type: queue, description: "Chapter outlines" }
    team_chat: { type: broadcast, description: "Shared context" }
```

Creatures point to standalone agent configs. The terrarium adds the wiring. The creature configs do not change.

See [Configuration Reference](../guides/configuration.md) for all fields. See [Channels](channels.md) for channel types and semantics.

---

## Runtime Internals

### TerrariumConfig (`terrarium/config.py`)

Loads and validates terrarium YAML configuration. The config parser:

- Finds `terrarium.yaml` or `terrarium.yml` in the given path
- Resolves creature `config` paths relative to the terrarium config directory
- Parses channel declarations (name, type, description)
- Produces a `TerrariumConfig` containing `CreatureConfig` and `ChannelConfig` lists

Key data classes:

```python
@dataclass
class TerrariumConfig:
    name: str
    creatures: list[CreatureConfig]
    channels: list[ChannelConfig]

@dataclass
class CreatureConfig:
    name: str
    config_path: str              # Resolved absolute path to agent config folder
    listen_channels: list[str]
    send_channels: list[str]
    output_log: bool = False
    output_log_size: int = 100

@dataclass
class ChannelConfig:
    name: str
    channel_type: str = "queue"   # "queue" or "broadcast"
    description: str = ""
```

### TerrariumRuntime (`terrarium/runtime.py`)

The runtime orchestrator, focused on lifecycle management. On `start()` it:

1. **Creates shared session**: all creatures share one `Session` with a single `ChannelRegistry`, so channels are visible across creatures.
2. **Pre-creates declared channels**: each channel from the config is created in the shared registry with the correct type and description.
3. **Delegates creature building** to `factory.build_creature()` and `factory.build_root_agent()`.
4. **Starts all creature agents** by calling `agent.start()` on each creature.

On `run()`, each creature runs its event loop as a concurrent `asyncio.Task`. The runtime waits for all tasks to finish (or handles cancellation).

### Factory (`terrarium/factory.py`)

Pure functions for building Agent instances from terrarium config. Extracted from `runtime.py` to keep the runtime focused on lifecycle.

- `build_root_agent(config, environment)`: builds the root agent with terrarium management tools force-registered
- `build_creature(creature_config, environment, channel_registry)`: builds a creature agent (loads standalone config, overrides input to `NoneInput`, injects `ChannelTrigger` instances for listen channels, injects channel topology into the system prompt, wraps output with `OutputLogCapture` if configured)

Both functions import from `builtins.tool_catalog` (not `builtins.tools`) to look up terrarium tools, avoiding import cycles.

### Tool Registration (`terrarium/tool_registration.py`)

Registers a deferred loader with `tool_catalog` so that terrarium tools (terrarium_create, terrarium_status, etc.) are loaded on first demand. The actual `builtins.tools.terrarium_tools` module is only imported when the catalog encounters a cache miss and invokes the loader.

### CreatureHandle (`terrarium/creature.py`)

A lightweight wrapper around an `Agent` instance that tracks terrarium metadata:

```python
@dataclass
class CreatureHandle:
    name: str
    agent: Agent
    config: CreatureConfig
    listen_channels: list[str]
    send_channels: list[str]

    @property
    def is_running(self) -> bool: ...
```

## Lifecycle Management

### Startup

1. `TerrariumRuntime.start()` initializes the shared session, channels, and creatures.
2. `TerrariumRuntime.run()` fires each creature's `startup_trigger` (if configured) and runs all creature event loops as concurrent tasks.

### Running

Each creature runs its own event loop, which:
- Waits for input events (from triggers, including channel triggers)
- Processes each event through the agent's controller
- Continues until the agent stops (termination conditions, exit request, or cancellation)

### Shutdown

`TerrariumRuntime.stop()`:
1. Cancels all running creature tasks
2. Waits for cancellation with `asyncio.gather(..., return_exceptions=True)`
3. Calls `agent.stop()` on each creature
4. Logs any errors during shutdown

### Status Monitoring

`TerrariumRuntime.get_status()` returns:

```python
{
    "name": "novel_writer",
    "running": True,
    "has_root": True,              # Whether a root agent is configured
    "creatures": {
        "brainstorm": {
            "running": True,
            "listen_channels": ["feedback"],
            "send_channels": ["ideas", "team_chat"],
        },
        # ...
    },
    "channels": [
        {"name": "ideas", "type": "queue", "description": "..."},
        # ...
    ],
}
```

## Hot-Plug Architecture

The terrarium supports runtime modification of its topology without stopping the system.

### What Can Be Modified at Runtime

| Component | Add | Remove | Notes |
|-----------|-----|--------|-------|
| Trigger (agent-level) | Yes | Yes | Starts/stops immediately |
| System prompt (agent-level) | Append or replace | N/A | Takes effect on next LLM call |
| Creature (terrarium-level) | Yes | Yes | Full wiring before first trigger |
| Channel (terrarium-level) | Yes | N/A | Available immediately after creation |
| Channel wiring | Yes | N/A | Adds trigger or send permission |

### Timing Guarantees

When a creature is added at runtime via `runtime.add_creature()`, the runtime performs the same wiring steps as during initial startup. The creature is fully wired before its first trigger fires; there is no window where a partially-configured creature could receive a message.

### Session Sharing

Hot-added creatures share the same `Session` as creatures present at startup:
- They can immediately send to and receive from any existing channel
- Channels created via `runtime.add_channel()` are visible to all creatures
- There is no separate session for hot-added components

### Agent-Level vs Terrarium-Level

Agent-level methods (`add_trigger`, `remove_trigger`, `update_system_prompt`, `get_system_prompt`) operate on a single agent and do not require a terrarium.

Terrarium-level methods (`add_creature`, `remove_creature`, `add_channel`, `wire_channel`) coordinate across multiple agents and handle the wiring that the runtime normally performs at startup.

For method signatures and code examples, see [Python API Reference](../reference/python.md).

## Observer Pattern

The `ChannelObserver` class (`terrarium/observer.py`) provides non-destructive visibility into channel traffic. It is accessed via `runtime.observer` (lazily created after the terrarium starts).

**Broadcast channels**: the observer subscribes as a silent participant (`_observer_<channel_name>`). A background task receives copies of every message.

**Queue channels**: non-destructive peeking is not possible. Queue messages are only recorded when sent via `TerrariumAPI.send_to_channel()`, which calls `observer.record()` internally.

**Features:**
- **Callbacks**: `on_message(callback)` registers a function called for every observed message
- **History retrieval**: `get_messages(channel, last_n)` returns recent `ObservedMessage` entries
- **Bounded memory**: history buffer capped at `max_history` (default 1000)

## Output Log Capture

The `OutputLogCapture` class (`terrarium/output_log.py`) is a tee wrapper that intercepts a creature's output and records it into a ring buffer.

When a creature has `output_log: true` in config, the runtime wraps its default output module during setup. All output flows to the original module unchanged, and a copy is stored.

Three entry types:

| Entry type | Source | Description |
|------------|--------|-------------|
| `text` | `write()` calls | Complete text blocks |
| `stream_flush` | `flush()` after streaming | Accumulated streaming chunks |
| `activity` | `on_activity()` calls | Tool start/done/error notifications |

The ring buffer size is controlled by `output_log_size` (default 100).

## Session Persistence Helpers

Session persistence helpers are extracted into `terrarium/persistence.py`. The runtime delegates to `attach_session_store(runtime, store, resume_data=None)`. When attached:

1. A `SessionOutput` is added as a secondary output on each creature, recording events to the store
2. `on_send` callbacks are registered on all channels to capture channel messages
3. If `resume_data` is provided, conversation and scratchpad state are injected into each creature before event loops start

`persistence.py` also provides `build_conversation_from_messages()` for rebuilding Conversation objects from saved message dicts during resume.

The `root_agent` property and `get_creature_agent(name)` method on `TerrariumRuntime` provide direct access to agent instances for resume injection.

## API Layer

The `TerrariumAPI` class (`terrarium/api.py`) wraps `TerrariumRuntime` with convenience methods for external interaction. It is accessed via the `runtime.api` property (lazily created on first use).

Three groups of operations:
- **Channel operations**: `list_channels()`, `channel_info(name)`, `send_to_channel(name, content, sender, metadata)`
- **Creature operations**: `list_creatures()`, `get_creature_status(name)`, `stop_creature(name)`, `start_creature(name)`
- **Terrarium operations**: `get_status()`, `is_running`

The API integrates with the observer: when `send_to_channel()` is called, the message is recorded in the observer automatically.

For full method signatures, see [Python API Reference](../reference/python.md).
