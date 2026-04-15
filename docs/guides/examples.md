# Examples

The `examples/` directory is one of the best ways to understand how KohakuTerrarium is meant to be used in practice.

Read it from a creature-first perspective:

- config-driven creatures in `examples/agent-apps/`
- programmatic usage in `examples/code/`
- plugin examples in `examples/plugins/`
- optional multi-agent systems in `examples/terrariums/`

For the raw directory overview, see [`examples/README.md`](../../examples/README.md).

## How to use the examples well

Do not read the examples as isolated demos.

Read them as architecture patterns:

- what kind of creature is this
- what runtime surface does it use
- what capability or extension point is it demonstrating
- is this a standalone creature or an optional terrarium topology

## Start with `kt-defaults` first

If your goal is to run something useful quickly, start with [`kt-defaults`](../../kt-defaults/README.md) before digging through examples.

The examples are especially good when you want to study patterns, integration, or extension points.

## Agent app examples

These are config-driven creature examples that you can run directly with `kt run`.

```bash
kt run examples/agent-apps/planner_agent
kt run examples/agent-apps/monitor_agent
kt run examples/agent-apps/rp_agent
```

### `planner_agent`

A good example for planning-oriented behavior and internal task structure.

Look here if you want to understand:

- prompt-driven workflow style
- use of built-in sub-agents
- configuration of a focused creature

### `monitor_agent`

A trigger-oriented example.

Look here if you want to understand:

- agents that are not primarily user-input driven
- timer or monitoring-style behavior
- automation-oriented creature design

### `discord_bot`

A custom integration example.

Look here if you want to understand:

- custom input and output flows
- external platform integration
- how a creature can be adapted to a messaging environment

### `conversational`

A richer interaction example with custom behavior and alternate runtime patterns.

Look here if you want to understand:

- conversational experience design
- custom modules around input or output
- specialized app-oriented creature configuration

### `rp_agent`

A character or roleplay-oriented example.

Look here if you want to understand:

- memory-oriented setup
- prompt-driven character behavior
- persistent identity-style creature design

### `compact_test`

A stress and behavior example.

Look here if you want to understand:

- compaction behavior
- context pressure testing
- runtime behavior under constrained settings

## Code examples

These are for using KohakuTerrarium as a Python framework, not just as a CLI app.

The key distinction from config-based usage is simple: your program is the orchestrator, and the agents are runtime components your code invokes.

Important examples include:

### `programmatic_chat.py`

Start here if you want the smallest useful Python embedding example.

### `run_terrarium.py`

Start here if you want to create and run terrariums from code.

### `task_orchestrator.py`

Useful for understanding orchestration patterns where your own application controls the flow.

### `review_loop.py`

Useful for understanding iterative multi-step workflows in code.

### `smart_router.py`

Useful for understanding routing and specialization patterns.

### `debate_arena.py` and `ensemble_voting.py`

Useful for understanding multiple-agent coordination patterns driven directly from Python.

## Terrarium examples

These examples are about topology and collaboration between creatures.
They are useful, but they are not the first place most users should start.

Run them with:

```bash
kt terrarium run examples/terrariums/code_review_team
kt terrarium run examples/terrariums/novel_terrarium
kt terrarium run examples/terrariums/research_assistant
```

### `code_review_team`

A software workflow terrarium.

Look here if you want to understand:

- developer and reviewer role separation
- feedback loops
- practical multi-creature collaboration

### `novel_terrarium`

A creative workflow terrarium.

Look here if you want to understand:

- staged handoff through channels
- how different creative roles can be composed
- pipeline-style multi-agent structures

### `research_assistant`

A research-oriented terrarium.

Look here if you want to understand:

- coordinator-style structures
- analysis and task decomposition
- information flow between specialist roles

## Plugin examples

The plugin examples are especially valuable if you want to extend the framework without replacing core modules.

See:

- [`examples/plugins/README.md`](../../examples/plugins/README.md)

Important patterns shown there include:

- lifecycle hooks
- tool interception
- prompt injection
- cost or budget enforcement
- event observation
- sub-agent tracking
- webhook integration

## Suggested reading paths

### If you want to build a coding creature

1. [`kt-defaults`](../../kt-defaults/README.md)
2. `examples/agent-apps/planner_agent`
3. `examples/code/programmatic_chat.py`
4. [Creatures](creatures.md)

### If you want to build a custom integration

1. `examples/agent-apps/discord_bot`
2. `examples/plugins/`
3. [Custom Modules](custom-modules.md)

### If you want to understand optional multi-agent composition

1. `examples/terrariums/code_review_team`
2. `examples/terrariums/research_assistant`
3. [Terrariums](terrariums.md)
4. [Channels](../concepts/channels.md)

### If you want to embed the framework in Python

1. `examples/code/programmatic_chat.py`
2. `examples/code/run_terrarium.py`
3. `examples/code/task_orchestrator.py`
4. [Programmatic Usage](programmatic-usage.md)

## Main takeaway

The examples directory is not a bonus folder.
It is one of the clearest expressions of how the framework is actually meant to be used.

But if your goal is simply to get a useful agent running, start with `kt-defaults`, then come back to the examples when you want patterns and implementation ideas.
