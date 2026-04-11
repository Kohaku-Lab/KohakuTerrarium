# Programmatic Usage

How to use KohakuTerrarium as a Python library — embedding agents in your own
applications instead of running them standalone.

## Two Paradigms

KohakuTerrarium supports two fundamentally different modes:

**Config-driven ("agent runs itself")**
You write a creature config folder (YAML + system prompt), launch it with
`kt run`, and the agent owns the event loop. Your code is the config. This is
covered in the [Creatures](creatures.md) and [Terrariums](terrariums.md) guides.

**Programmatic ("you control the agent")**
Your program is the orchestrator. Agents are workers you create, invoke, and
destroy. You decide when they speak, what they see, and how their output is
used. The agent never runs its own interactive loop — your code does.

Use programmatic mode when:

- You are building a web server, Discord bot, or desktop app and agents are
  components inside it
- The number and type of agents are determined at runtime
- You need strict turn ordering (debate, review loop, pipeline)
- You want to mix agent calls with regular Python logic
- Terrarium channels are too loose for your coordination needs

## 1. Agent as Library

The simplest pattern: load an agent, send messages, collect responses.

### AgentSession

`AgentSession` wraps an `Agent` with streaming chat. It is the same interface
the web API uses internally.

```python
import asyncio

from kohakuterrarium.serving.agent_session import AgentSession


async def main() -> None:
    # Load from any creature config path (package ref or filesystem path)
    session = await AgentSession.from_path("@kt-defaults/creatures/general")

    try:
        questions = [
            "What is a terrarium?",
            "How would you build one for tropical plants?",
        ]
        for q in questions:
            print(f"\nQ: {q}")
            print("A: ", end="", flush=True)

            # .chat() returns an async iterator of text chunks
            async for chunk in session.chat(q):
                print(chunk, end="", flush=True)
            print()

    finally:
        await session.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

Key points:

- `AgentSession.from_path(path)` creates the agent and starts it. The path can
  be a package reference like `@kt-defaults/creatures/general` or a filesystem
  path to a creature config folder.
- `session.chat(message)` is an async generator yielding text chunks as the
  agent streams its response. The agent accumulates conversation context across
  calls.
- Always call `session.stop()` when done. Use `try/finally` to ensure cleanup.

### Building from an AgentConfig

When you need to customize the agent programmatically instead of using a config
folder on disk, build an `AgentConfig` object directly:

```python
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.serving.agent_session import AgentSession


async def create_custom_agent() -> AgentSession:
    # Start from a base config, then override fields
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = "my-custom-agent"
    config.system_prompt = "You are a pirate. Respond in pirate speak."
    config.tools = []       # No tools needed
    config.subagents = []   # No sub-agents needed

    return await AgentSession.from_config(config)
```

### Collecting Output with set_output_handler

If you need to capture output without streaming (e.g., for a bot framework
that takes a complete string), you can set a custom output handler on the
underlying `Agent`:

```python
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config import load_agent_config


async def collect_full_response(question: str) -> str:
    config = load_agent_config("@kt-defaults/creatures/general")
    agent = Agent(config)

    parts: list[str] = []
    agent.set_output_handler(lambda chunk: parts.append(chunk))

    await agent.start()
    try:
        await agent.inject_input(question)
        return "".join(parts)
    finally:
        await agent.stop()
```

`set_output_handler` registers a callback that receives every text chunk the
agent produces. `inject_input` sends a message without going through the
input module (no terminal prompt, no ASR — just your string).


## 2. Terrarium from Code

A terrarium is a multi-agent system where creatures communicate through
channels. You can start one programmatically and interact with the channels
from your code.

```python
import asyncio

from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime


async def main() -> None:
    config = load_terrarium_config("@kt-defaults/terrariums/swe_team")
    runtime = TerrariumRuntime(config)
    await runtime.start()

    # Inject a task into the "tasks" channel
    tasks_channel = runtime.environment.shared_channels.get("tasks")
    if tasks_channel:
        msg = ChannelMessage(
            sender="user",
            content="Fix the off-by-one error in src/pagination.py",
        )
        await tasks_channel.send(msg)
        print(f"Injected task: {msg.content}")

    try:
        # runtime.run() blocks until all creatures finish or you interrupt
        await runtime.run()
    except KeyboardInterrupt:
        print("\nStopping terrarium...")
    finally:
        await runtime.stop()

    # Check final status
    status = runtime.get_status()
    print(f"\nTerrarium '{status['name']}' finished.")
    for name, info in status.get("creatures", {}).items():
        print(f"  {name}: running={info['running']}")


if __name__ == "__main__":
    asyncio.run(main())
```

Key points:

- `load_terrarium_config(path)` loads the terrarium YAML config.
- `TerrariumRuntime(config)` creates the runtime. Call `.start()` to create
  channels and creatures, then `.run()` to enter the event loop.
- Channels are accessible via `runtime.environment.shared_channels.get(name)`.
  Send a `ChannelMessage` to inject work.
- `runtime.get_status()` returns a dict with creature states and channel info.
- Individual creatures are accessible via `runtime.get_creature_agent(name)`.


## 3. Composition Algebra

This is the main programmatic API. It lets you treat agents as composable
functions and combine them with Python operators.

```python
from kohakuterrarium.compose import agent, factory, Pure
```

### agent() — Persistent Agents

`agent()` creates a persistent agent that accumulates conversation context
across calls. The agent is started immediately and must be closed when done.

```python
async with await agent(config) as a:
    result = await a("Tell me a joke")
    # a remembers the joke — context carries over
    result2 = await a("Tell me another one in the same style")
```

Use `agent()` when the agent needs memory of previous interactions — debates,
review loops, ongoing conversations.

### factory() — Ephemeral Agents

`factory()` creates an agent factory. Each call spins up a fresh agent,
runs the task, and destroys it. No context carries over.

```python
specialist = factory(make_config("coder"))
result = await specialist("Write a function that sorts by frequency")
# Agent is already gone — next call creates a new one
result2 = await specialist("Write a binary search function")
```

Use `factory()` when agents are disposable workers — one task, one answer,
no history needed. Cheaper and simpler than persistent agents.

### The >> Operator (Sequence / Pipeline)

Pipe the output of one step as the input to the next. This is the core
composition primitive.

```python
# Agent output flows to the next agent
pipeline = writer >> reviewer

# Plain functions are auto-wrapped — no need for Pure()
pipeline = extractor >> json.loads >> formatter

# Mix agents and functions freely
pipeline = agent >> (lambda text: text.upper()) >> next_agent
```

When you use `>>` with a plain callable (function, lambda, method), it is
automatically wrapped as a `Pure` runnable. You never need to manually
wrap functions unless you want to be explicit.

**Complete example — data extraction pipeline:**

```python
import asyncio
import json

from kohakuterrarium.compose import factory
from kohakuterrarium.core.config import load_agent_config


def make_extractor_config():
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = "extractor"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        "You are a data extractor. Given text, extract all mentioned people "
        "and their roles. Output ONLY valid JSON:\n"
        '[{"name": "...", "role": "..."}, ...]\n'
        "No markdown, no explanation."
    )
    return config


def parse_json(text: str) -> list[dict]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
    if cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[:-1])
    return json.loads(cleaned)


async def main() -> None:
    document = (
        "The project is led by Alice Chen, the CTO. "
        "Bob Martinez serves as the lead architect."
    )

    extractor = factory(make_extractor_config())

    # >> auto-wraps parse_json as Pure — agents and functions in one pipeline
    extract_pipeline = extractor >> parse_json

    people = await extract_pipeline(document)
    print(f"Found {len(people)} people: {people}")


if __name__ == "__main__":
    asyncio.run(main())
```

### The & Operator (Parallel)

Run multiple branches concurrently on the same input. Returns a tuple of
results.

```python
# Three agents run simultaneously on the same question
results = await (analyst & writer & designer)(task)
# results is a tuple: (analyst_answer, writer_answer, designer_answer)
```

This uses `asyncio.gather` under the hood. All branches receive the same
input string and run in parallel.

**Complete example — ensemble voting:**

```python
import asyncio

from kohakuterrarium.compose import factory
from kohakuterrarium.core.config import load_agent_config


def make_expert(name: str, style: str):
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = f"expert-{name}"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        f"You are an expert. Answer questions {style}.\n"
        "Give ONE clear answer in 2-3 sentences. No hedging."
    )
    return config


def pick_best(answers: tuple[str, ...]) -> str:
    """Pick the longest answer as a proxy for most detailed."""
    return max(answers, key=len)


async def main() -> None:
    analytical = factory(make_expert("analytical", "with logical analysis"))
    creative = factory(make_expert("creative", "using analogies"))
    concise = factory(make_expert("concise", "as briefly as possible"))

    # & runs all 3 in parallel, >> pipes the tuple through pick_best
    ensemble = (analytical & creative & concise) >> pick_best

    result = await ensemble("What causes rain?")
    print(f"Best answer:\n{result}")


if __name__ == "__main__":
    asyncio.run(main())
```

### The | Operator (Fallback)

Try the primary; if it raises an exception, run the fallback instead.

```python
safe_pipeline = risky_agent | reliable_agent
result = await safe_pipeline(task)
```

If the left side throws any `Exception`, the right side runs with the same
input. This is how you add resilience.

### The * Operator (Retry)

Retry a runnable up to N times on exception.

```python
# Retry the ensemble up to 2 times, then fall back to a single agent
safe = (ensemble * 2) | fallback_agent
```

`pipeline * 3` means: try up to 3 times. If all 3 attempts raise exceptions,
the last exception propagates. Combine with `|` for a final fallback.

### Combining Operators

Operators compose naturally. A realistic resilient pipeline:

```python
# 3 experts in parallel → vote → format, retry twice, fall back to single agent
ensemble = (analytical & creative & concise) >> pick_best >> format_result
safe_pipeline = (ensemble * 2) | analytical

result = await safe_pipeline(question)
```

### >> dict (Routing)

Pipe into a dict to route by key. The upstream step produces a classification
key, and the dict maps keys to specialist agents.

```python
from kohakuterrarium.compose import Pure, factory

async def classify_and_pair(request: str) -> tuple[str, str]:
    """Return (category, original_request)."""
    category = await classifier(request)
    return (category.strip().lower(), request)

specialists = {
    "code": factory(make_specialist_config("code")),
    "writing": factory(make_specialist_config("writing")),
    "_default": factory(make_specialist_config("general")),
}

router = Pure(classify_and_pair) >> specialists
result = await router("Fix the bug in auth.py")
```

How routing works:

- If the input is a `(key, payload)` tuple, `key` selects the branch and
  `payload` is passed to it.
- If the input is a plain string, it is used as both the key and the payload.
- `"_default"` is the catch-all key. If no route matches and no default exists,
  a `KeyError` is raised.

**Complete example — smart router:**

```python
import asyncio

from kohakuterrarium.compose import Pure, factory
from kohakuterrarium.core.config import load_agent_config


def make_classifier_config():
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = "classifier"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        "You are a task classifier. Output EXACTLY one word:\n"
        "- code: programming tasks\n"
        "- writing: content creation\n"
        "- general: anything else\n\n"
        "Output ONLY the category word."
    )
    return config


def make_specialist_config(role: str):
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = f"specialist-{role}"
    config.tools = []
    config.subagents = []
    config.system_prompt = f"You are a {role} specialist."
    return config


async def main() -> None:
    classifier = factory(make_classifier_config())

    specialists = {
        "code": factory(make_specialist_config("code")),
        "writing": factory(make_specialist_config("writing")),
        "_default": factory(make_specialist_config("general")),
    }

    async def classify_and_pair(request: str) -> tuple[str, str]:
        category = await classifier(request)
        return (category.strip().lower(), request)

    router = Pure(classify_and_pair) >> specialists
    safe_router = (router * 2) | factory(make_specialist_config("general"))

    result = await safe_router("Write a blog post about AI")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
```

### .iterate() — Loops with async for

`pipeline.iterate(initial_input)` returns an async iterator that runs the
pipeline repeatedly, feeding each output back as the next input.

```python
async for result in pipeline.iterate("start"):
    if some_condition(result):
        break
```

This is how you express "write, review, revise until done" without recursion
or callbacks. Your code controls the loop — you decide when to break.

**Complete example — review loop:**

```python
import asyncio

from kohakuterrarium.compose import agent
from kohakuterrarium.core.config import load_agent_config


def make_writer_config():
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = "writer"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        "You are a writer. When given a task or feedback, produce improved text. "
        "Output ONLY the text, no commentary."
    )
    return config


def make_reviewer_config():
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = "reviewer"
    config.tools = []
    config.subagents = []
    config.system_prompt = (
        "You are a strict reviewer. If the text needs improvement, explain why. "
        "If it's good enough, respond with EXACTLY 'APPROVED' on the first line."
    )
    return config


async def main() -> None:
    async with (
        await agent(make_writer_config()) as writer,
        await agent(make_reviewer_config()) as reviewer,
    ):
        # Writer produces text, lambda formats for reviewer, reviewer evaluates
        write_and_review = (
            writer
            >> (lambda text: f"Review this text:\n\n{text}\n\nIs it good enough?")
            >> reviewer
        )

        round_num = 0
        async for feedback in write_and_review.iterate("Write a haiku about rain"):
            round_num += 1
            print(f"--- Round {round_num} ---")
            print(f"Reviewer: {feedback[:200]}\n")

            if feedback.strip().startswith("APPROVED"):
                print(f"Approved after {round_num} round(s)!")
                break

            if round_num >= 5:
                print("Max rounds reached.")
                break


if __name__ == "__main__":
    asyncio.run(main())
```

Both agents are persistent (`agent()` not `factory()`), so the writer
remembers all previous feedback and the reviewer sees the full evolution.

### PipelineIterator.feed()

By default, `.iterate()` feeds the output of each iteration as the input to
the next. If you need to override what the next iteration receives, use
`.feed(value)`:

```python
it = pipeline.iterate("start")
async for result in it:
    if needs_different_input(result):
        it.feed("use this as next input instead")
    if done(result):
        break
```

### Pure() — Explicit Function Wrapping

`>>` auto-wraps callables, but if you need a standalone function-as-runnable
(e.g., as the first element of a pipeline or to use with `&`), wrap it
explicitly:

```python
from kohakuterrarium.compose import Pure

normalize = Pure(lambda x: x.strip().lower())
result = await normalize("  HELLO  ")  # "hello"

# Use as a standalone runnable in parallel
results = await (agent & Pure(some_function))(input)
```

`Pure` handles both sync and async callables:

```python
async def fetch_data(url: str) -> str:
    # ... async HTTP call ...
    return data

fetcher = Pure(fetch_data)
result = await fetcher("https://example.com")
```

### .map() and .contramap()

Profunctor transforms for targeted input/output processing:

```python
# .map(fn) — post-process output (equivalent to self >> Pure(fn))
uppercase_agent = my_agent.map(str.upper)

# .contramap(fn) — pre-process input (equivalent to Pure(fn) >> self)
prefixed_agent = my_agent.contramap(lambda x: f"Please help: {x}")
```

### .fails_when() — Custom Failure Predicates

Make a runnable raise `ValueError` when its output matches a predicate. This
triggers fallback (`|`) or retry (`*`):

```python
# Treat empty responses as failures
reliable = agent.fails_when(lambda x: len(x.strip()) == 0)
safe = reliable | fallback_agent
```

### Effects Tracking

Each runnable can carry optional cost/latency/reliability annotations. These
compose automatically through the operators:

- `>>` (sequence): costs add, latencies add, reliabilities multiply
- `&` (parallel): costs add, latencies take max, reliabilities multiply

This is for cost analysis, not runtime behavior. Effects are metadata only.


## 4. Service Layer: KohakuManager

`KohakuManager` is the unified service manager. Use it when you are building
your own API server, background worker, or any system that manages multiple
agents and terrariums over their full lifecycle.

The web API and dashboard use `KohakuManager` internally. If you are building
something similar, this is your entry point.

```python
import asyncio

from kohakuterrarium.serving.manager import KohakuManager


async def main() -> None:
    manager = KohakuManager(session_dir="./sessions")

    # Create an agent — returns an agent_id
    agent_id = await manager.agent_create(
        config_path="@kt-defaults/creatures/general"
    )
    print(f"Agent created: {agent_id}")

    # Chat with streaming
    print("Response: ", end="", flush=True)
    async for chunk in manager.agent_chat(agent_id, "What is a terrarium?"):
        print(chunk, end="", flush=True)
    print()

    # Check status
    status = manager.agent_status(agent_id)
    print(f"Model: {status['model']}, Tools: {len(status['tools'])}")

    # List running jobs (tools and sub-agents)
    jobs = manager.agent_get_jobs(agent_id)
    print(f"Running jobs: {len(jobs)}")

    # Switch model mid-session
    new_model = manager.agent_switch_model(agent_id, "claude-sonnet-4")
    print(f"Switched to: {new_model}")

    # Interrupt the agent's current turn
    manager.agent_interrupt(agent_id)

    # Stop the agent
    await manager.agent_stop(agent_id)

    # Clean up everything
    await manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

### Managing Terrariums

```python
async def manage_terrarium(manager: KohakuManager) -> None:
    # Create a terrarium
    terrarium_id = await manager.terrarium_create(
        config_path="@kt-defaults/terrariums/swe_team"
    )

    # Check status
    status = manager.terrarium_status(terrarium_id)
    print(f"Creatures: {list(status['creatures'].keys())}")

    # Send a message to a shared channel
    msg_id = await manager.terrarium_channel_send(
        terrarium_id, "tasks", "Fix the pagination bug"
    )

    # Chat with a specific creature (or root agent)
    async for chunk in manager.terrarium_chat(terrarium_id, "root", "Status?"):
        print(chunk, end="", flush=True)

    # List creatures
    creatures = manager.creature_list(terrarium_id)
    for c in creatures:
        print(f"  {c['name']}: running={c['running']}")

    # Hot-plug: add a new channel
    await manager.terrarium_channel_add(
        terrarium_id, "review", channel_type="queue", description="Code reviews"
    )

    # Hot-plug: wire a creature to the new channel
    await manager.creature_wire(terrarium_id, "developer", "review", "send")

    # Cancel a specific job on a creature
    jobs = manager.creature_get_jobs(terrarium_id, "developer")
    if jobs:
        await manager.creature_cancel_job(terrarium_id, "developer", jobs[0]["id"])

    # Stop the terrarium
    await manager.terrarium_stop(terrarium_id)
```

### Manager Method Reference

The naming convention tells you what each method operates on:

| Prefix | Scope |
|--------|-------|
| `agent_*` | Standalone agent lifecycle and interaction |
| `agent_channel_*` | Standalone agent channel operations |
| `terrarium_*` | Terrarium lifecycle |
| `terrarium_channel_*` | Shared inter-creature channels |
| `creature_*` | Creature operations within a terrarium |
| `creature_channel_*` | Creature private channels |

Key methods:

- `agent_create(config_path=...) -> str` — create and start an agent
- `agent_chat(agent_id, message)` — async iterator of response chunks
- `agent_stop(agent_id)` — stop and clean up
- `agent_switch_model(agent_id, profile)` — change LLM mid-session
- `agent_interrupt(agent_id)` — cancel current processing
- `agent_cancel_job(agent_id, job_id)` — cancel a specific tool/sub-agent
- `terrarium_create(config_path=...) -> str` — create and start a terrarium
- `terrarium_chat(terrarium_id, target, message)` — chat with root or creature
- `terrarium_channel_send(terrarium_id, channel, content)` — inject a message
- `creature_add(terrarium_id, config)` — hot-plug a new creature
- `creature_remove(terrarium_id, name)` — remove a running creature
- `shutdown()` — stop everything


## 5. Background Task Management

### BackgroundifyHandle

When a tool or sub-agent is running, it is wrapped in a `BackgroundifyHandle`.
This allows **mid-flight promotion**: a direct (blocking) task can be moved to
background execution at any time, without restarting it.

```
Direct mode: Agent waits for the tool to finish before continuing
     |
     v  (user clicks "move to background" in TUI / frontend)
     |
Background mode: Agent continues, tool runs independently,
                  result delivered via callback when done
```

The handle has three states:

1. **Direct** — the agent is awaiting `handle.wait()`. The tool runs and the
   agent blocks until it finishes.
2. **Promoted** — someone called `handle.promote()`. The `wait()` call
   immediately returns a `PromotionResult` placeholder, and the agent
   continues processing. The tool keeps running.
3. **Complete** — the underlying task finished. If still direct, the result
   goes to the agent normally. If promoted, the `on_bg_complete` callback
   fires.

### How It Surfaces

- **TUI**: Running tasks appear in a panel. Click a task to promote it to
  background. Click again to cancel it.
- **Frontend**: The task accordion shows running jobs with an X button to
  cancel and a promote button to move to background.
- **API**: Use `manager.agent_cancel_job(agent_id, job_id)` to cancel.

### Interrupt vs Cancel

These are different operations:

- **Interrupt** (`agent.interrupt()` / Escape key): Cancels the agent's
  _current processing cycle_ — the LLM stream, the tool gather, everything
  in progress for this turn. The agent stays alive and waits for the next
  input. Background sub-agents are NOT affected.

- **Cancel** (`agent._cancel_job(job_id, name)` / click in TUI): Cancels a
  _single specific job_ (tool execution or sub-agent). Other jobs keep
  running. The agent keeps processing.


## 6. Dynamic Agent Configuration

### Loading and Modifying Configs

`load_agent_config()` reads a creature's config folder and returns an
`AgentConfig` dataclass. You can modify any field before passing it to
`Agent()` or `AgentSession.from_config()`.

```python
from kohakuterrarium.core.config import load_agent_config

config = load_agent_config("@kt-defaults/creatures/general")

# Change identity
config.name = "custom-assistant"
config.system_prompt = "You are a financial advisor."

# Change model
config.model = "openai/gpt-4.1-mini"
config.temperature = 0.3

# Strip tools and sub-agents for a focused agent
config.tools = []
config.subagents = []

# Add specific tools
from kohakuterrarium.core.config_types import ToolConfigItem
config.tools = [
    ToolConfigItem(name="read", type="builtin"),
    ToolConfigItem(name="write", type="builtin"),
]

# Set tool call format
config.tool_format = "native"  # Use the LLM's native function calling

# Configure auto-compact
config.compact = {
    "threshold": 0.7,   # Compact when 70% of context is used
    "target": 0.4,      # Compact down to 40%
    "keep_recent_turns": 4,
}

# Configure termination
config.termination = {
    "max_turns": 20,
    "keywords": ["TASK_COMPLETE"],
}
```

### Building Configs from Scratch

You can also build an `AgentConfig` entirely in code, without loading from
disk:

```python
from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
)

config = AgentConfig(
    name="from-scratch-agent",
    model="openai/gpt-4o-mini",
    system_prompt="You are a helpful assistant.",
    input=InputConfig(type="none"),  # No interactive input
    output=OutputConfig(type="stdout"),
)
```

### Switching Models Mid-Session

On a running agent, call `switch_model()` with a profile name:

```python
# Via Agent directly
agent.switch_model("claude-sonnet-4")

# Via KohakuManager
manager.agent_switch_model(agent_id, "claude-sonnet-4")

# Via KohakuManager for a creature in a terrarium
manager.creature_switch_model(terrarium_id, "developer", "gpt-4.1")
```

The model switch is immediate — the next LLM call uses the new model. The
conversation history is preserved. The compact manager's context window
threshold is updated automatically from the new profile.

### Modifying a Running Agent

Several aspects of a running agent can be modified:

```python
# Update system prompt (append or replace)
agent.update_system_prompt("Additional instruction here.")
agent.update_system_prompt("Complete new prompt.", replace=True)

# Read current system prompt
prompt = agent.get_system_prompt()

# Add a trigger at runtime
from kohakuterrarium.modules.trigger.base import BaseTrigger
trigger_id = await agent.add_trigger(my_trigger, trigger_id="my-timer")

# Remove a trigger
await agent.remove_trigger("my-timer")

# Check agent state
state = agent.get_state()
print(f"Running: {state['running']}, Messages: {state['message_count']}")
```


## Choosing the Right Pattern

| Situation | Pattern | Key import |
|-----------|---------|------------|
| Simple chat integration | `AgentSession` | `serving.agent_session` |
| Multi-agent with channels | `TerrariumRuntime` | `terrarium.runtime` |
| Agent pipelines and composition | `agent()` / `factory()` + operators | `compose` |
| Managing many agents/terrariums | `KohakuManager` | `serving.manager` |
| One-shot task, no state | `factory()` | `compose` |
| Ongoing conversation, memory | `agent()` with `async with` | `compose` |
| Parallel redundancy | `&` operator | `compose` |
| Classify and route | `>> dict` | `compose` |
| Iterative refinement | `.iterate()` with `async for` | `compose` |

All the code examples in this guide correspond to scripts in `examples/code/`.
Run them directly to see the patterns in action:

```bash
python examples/code/programmatic_chat.py
python examples/code/run_terrarium.py
python examples/code/debate_arena.py "Pineapple belongs on pizza"
python examples/code/ensemble_voting.py "What causes rain?"
python examples/code/review_loop.py "Write a haiku about programming"
python examples/code/smart_router.py "Fix the bug in auth.py"
python examples/code/pipeline_transforms.py
python examples/code/task_orchestrator.py "Build a landing page"
```
