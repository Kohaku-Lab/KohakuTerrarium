# Environment and Session

KohakuTerrarium uses two levels of runtime state isolation.

This separation matters because a creature needs private state for its own work, while a terrarium needs shared state for collaboration between creatures.

## Two levels

| Level | Scope | Holds | Example |
|-------|-------|-------|---------|
| **Environment** | Per terrarium / per runtime world | Shared channels and shared runtime state | `ideas`, `team_chat`, shared coordination state |
| **Session** | Per creature / per standalone run | Private channels, scratchpad, per-creature runtime state, searchable history | sub-agent channels, working notes, stored run history |

```text
Environment (shared)
  +-- shared_channels: ChannelRegistry
  |     +-- "ideas" (queue)
  |     +-- "outline" (queue)
  |     +-- "team_chat" (broadcast)
  |
  +-- Session "brainstorm" (private)
  |     +-- channels: ChannelRegistry
  |     +-- scratchpad: Scratchpad
  |     +-- history / stored state
  |
  +-- Session "planner" (private)
  |     +-- channels: ChannelRegistry
  |     +-- scratchpad: Scratchpad
  |     +-- history / stored state
  |
  +-- Session "writer" (private)
        +-- channels: ChannelRegistry
        +-- scratchpad: Scratchpad
        +-- history / stored state
```

## What this separation guarantees

- creatures cannot accidentally read each other's scratchpads
- private channels stay invisible across creature boundaries
- shared channels live at the environment level
- module state can be scoped to either the environment or the session
- terrarium collaboration does not collapse creature-local state into one global blob

## Standalone creature vs terrarium runtime

### Standalone creature

A standalone creature has a session but no terrarium environment.

That means:

- the creature keeps its own scratchpad and private channels
- stored session history belongs to that creature run
- all communication is local unless external modules provide I/O surfaces

### Terrarium runtime

A terrarium creates an environment and gives each creature its own session.

That means:

- shared channels live in the environment
- each creature still has private state in its own session
- terrarium collaboration happens through explicit channels
- session history can be stored per creature while still preserving shared terrarium history

## Session history is more than resume state

A session is not only a container for current runtime state.
It is also the persistence unit for later retrieval.

Session history supports:

- resuming past work
- inspecting prior tool calls and runtime behavior
- full-text search over stored history
- vector search over stored history when embeddings are available
- agent-driven retrieval through memory search tools

That means KohakuTerrarium session history behaves not only like a resume file, but also like a searchable knowledge base built from prior runs.

## Programmatic usage

### Standalone creature

```python
from kohakuterrarium.core.agent import Agent

agent = Agent.from_path("examples/agent-apps/planner_agent")
# agent.session exists
# agent.environment is None unless explicitly supplied
```

### Standalone creature with explicit session

```python
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.session import Session

session = Session(key="user_123_planner")
agent = Agent.from_path("examples/agent-apps/planner_agent", session=session)

agent.session.scratchpad.set("task", "fix auth bug")
```

### Terrarium with environment

```python
from kohakuterrarium.terrarium import TerrariumRuntime, load_terrarium_config

config = load_terrarium_config("examples/terrariums/novel_terrarium")
runtime = TerrariumRuntime(config)
await runtime.start()

env = runtime.environment
shared = env.shared_channels.list_channels()
```

## Accessing environment from tools

Tools can receive both session and environment through context.

That lets a tool use:

- private scratchpad or local state from the session
- shared channels or shared coordination state from the environment

This is one of the reasons creature internals can stay private while still participating in terrarium collaboration.

## Channel resolution

When a tool like `send_message` resolves a channel name, resolution distinguishes between:

1. private session channels
2. shared environment channels

This keeps sub-agent and creature-local channels private while exposing declared terrarium channels as shared communication paths.

See [Channels](channels.md) for the full channel model.

## Session persistence

Sessions can be persisted to `.kohakutr` files for later resume, inspection, and search.

Stored session data includes things like:

- conversation history
- scratchpad state
- event logs
- channel messages
- jobs and runtime metadata
- terrarium topology metadata for terrarium sessions

By default, session files live under:

```text
~/.kohakuterrarium/sessions/
```

See [Sessions](../guides/sessions.md) for the user-facing guide.

## Multi-user and service isolation

The serving layer can host multiple runtimes at once.

Each user or mounted runtime can have:

- a separate environment
- separate creature sessions
- separate stored session history

This prevents cross-user leakage while still allowing each runtime to preserve its own collaboration state and memory history.

For the serving layer that manages these instances, see [Serving](serving.md).

## Shared state registration

Modules can register their own state at the environment level without coupling the whole runtime to a fixed schema.

That means shared things like:

- budgets
- connection pools
- shared registries
- coordination state

can live in the environment when needed, while creature-local state stays in sessions.

## Compact summary

- **environment** is the shared state layer for terrarium collaboration
- **session** is the private state layer for each creature or standalone run
- **session history** supports both resume and later retrieval
- **memory search** turns stored session history into a searchable knowledge base
- **creature-local state stays private even inside a terrarium**
