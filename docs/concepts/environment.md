# Environment-Session Isolation

Two-level isolation for running multiple agents and terrariums safely in one process.

## The Problem

Without isolation, two agents sharing the same session key would collide on channels and scratchpad. Two creatures in the same terrarium would see each other's sub-agent channels and working memory.

## Two Levels

| Level | Scope | Holds | Example |
|-------|-------|-------|---------|
| **Environment** | Per terrarium / per user request | Shared channels (inter-creature) | `ideas`, `outline`, `team_chat` |
| **Session** | Per creature / per agent | Private channels (sub-agent), scratchpad | `explore_results`, working notes |

```
Environment (shared)
  +-- shared_channels: ChannelRegistry
  |     +-- "ideas" (queue) - brainstorm -> planner
  |     +-- "outline" (queue) - planner -> writer
  |     +-- "team_chat" (broadcast) - all
  |
  +-- Session "brainstorm" (private)
  |     +-- channels: ChannelRegistry (sub-agent only)
  |     +-- scratchpad: Scratchpad
  |
  +-- Session "planner" (private)
  |     +-- channels: ChannelRegistry
  |     +-- scratchpad: Scratchpad
  |
  +-- Session "writer" (private)
        +-- channels: ChannelRegistry
        +-- scratchpad: Scratchpad
```

### Isolation Guarantees

- Creatures cannot accidentally read each other's scratchpads
- Private channels (sub-agent communication within a creature) are invisible to other creatures
- Shared channels (inter-creature communication) are managed at the environment level
- Module state can be scoped to either environment or session as appropriate

## Programmatic Usage

### Standalone Agent (No Environment)

Standalone agents have a Session but no Environment. All channels are private.

```python
from kohakuterrarium.core.agent import Agent

# Session is auto-created from config name
agent = Agent.from_path("examples/agent-apps/swe_agent")
# agent.session = Session(key="swe_agent")
# agent.environment = None
```

### Standalone Agent with Explicit Session

```python
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.session import Session

# Create a session with a unique key (for multi-user)
session = Session(key="user_123_swe_agent")
agent = Agent.from_path("examples/agent-apps/swe_agent", session=session)

# Agent uses this session's scratchpad and channels
agent.session.scratchpad.set("task", "fix auth bug")
```

### Terrarium with Environment

The terrarium runtime creates an Environment automatically. Each creature gets its own Session.

```python
from kohakuterrarium.terrarium import TerrariumRuntime, load_terrarium_config

config = load_terrarium_config("examples/terrariums/novel_terrarium")
runtime = TerrariumRuntime(config)
await runtime.start()

# Environment was created
env = runtime.environment

# Shared channels (inter-creature)
shared = env.shared_channels.list_channels()
# ['ideas', 'outline', 'draft', 'feedback', 'team_chat']

# Each creature has a private session
brainstorm_session = env.get_session("brainstorm")
planner_session = env.get_session("planner")

# Private scratchpads are isolated
brainstorm_session.scratchpad.set("notes", "brainstorm's private notes")
assert planner_session.scratchpad.get("notes") is None  # not visible
```

### Custom Environment (Advanced)

```python
from kohakuterrarium.core.environment import Environment

env = Environment(env_id="my_custom_env")

# Pre-create shared channels
env.shared_channels.get_or_create("tasks", channel_type="queue", description="Work items")
env.shared_channels.get_or_create("events", channel_type="broadcast", description="Status")

# Register custom shared state
env.register("budget", {"max_calls": 100, "used": 0})

# Pass to terrarium
runtime = TerrariumRuntime(config, environment=env)
await runtime.start()

# Or pass to standalone agent
agent = Agent.from_path("examples/agent-apps/swe_agent", environment=env, session=env.get_session("my_agent"))
```

### Multi-User Isolation

Two users in the same process get separate environments:

```python
from kohakuterrarium.serving import KohakuManager

manager = KohakuManager()

# User A creates a terrarium - gets unique environment
tid_a = await manager.create_terrarium("examples/terrariums/novel_terrarium")

# User B creates the same terrarium - gets a DIFFERENT environment
tid_b = await manager.create_terrarium("examples/terrariums/novel_terrarium")

# No collision: different shared channels, different creature sessions
```

For the serving layer that manages these instances, see [Serving](serving.md).

### Accessing Environment from Tools

Tools receive both session (private) and environment (shared) via `ToolContext`:

```python
class MyTool(BaseTool):
    needs_context = True

    async def _execute(self, args, context=None):
        # Private scratchpad
        context.session.scratchpad.set("key", "private value")

        # Shared channels (if in a terrarium)
        if context.environment:
            shared = context.environment.shared_channels.get("ideas")
            budget = context.environment.get("budget")
```

## Channel Resolution

When a tool like `send_message` resolves a channel name:

1. **Private session channels**: checked first (sub-agent internal channels)
2. **Shared environment channels**: checked second (inter-creature channels)
3. **Auto-create in private**: if not found anywhere, creates a queue in the session
4. **Conflict validation**: if a shared channel has the same name, auto-create is blocked with an error

This means sub-agent channels stay private automatically, terrarium channels are accessible to all creatures, and there is no accidental shadowing. See [Channels](channels.md) for the full channel type reference.

## Session Persistence

Sessions can be persisted to `.kohakutr` files (SQLite via KohakuVault) for later resume. The `SessionStore` records conversation history, scratchpad state, event log, token usage, and channel messages. When resuming, the stored state is injected back into agents.

```python
from kohakuterrarium.session import SessionStore

# Record a session
store = SessionStore("session.kohakutr")
store.init_meta(session_id="sess_001", config_type="agent", ...)
store.save_conversation("my_agent", messages)
store.save_state("my_agent", scratchpad={"plan": "step 1"})

# Resume later
messages = store.load_conversation("my_agent")
scratchpad = store.load_scratchpad("my_agent")
```

For full API, see [Python API Reference](../reference/python.md).

## Shared State Registration

Modules can register their own state at the environment level without coupling to specific data structures:

```python
# Register shared state (e.g., in a custom tool's setup)
environment.register("db_pool", connection_pool)
environment.register("rate_limiter", limiter)

# Retrieve from anywhere with environment access
pool = environment.get("db_pool")
```

The Environment does not define what state it holds. Modules register what they need, keeping Environment generic and extensible.
