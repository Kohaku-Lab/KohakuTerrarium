# Creatures

A **creature** is KohakuTerrarium's unit of agent definition.

It is not just a prompt or a tool list. A creature is a complete standalone agent with its own controller, tools, sub-agents, triggers, prompts, and state.

You can run a creature directly with:

```bash
kt run <path>
```

You can also place the same creature inside a terrarium without changing its internal logic.

For the architecture behind this split, see [Agents](../concepts/agents.md) and [Terrariums](../concepts/terrariums.md).

## What lives in a creature

A creature config usually defines:

- `controller` for model and reasoning settings
- `system_prompt_file` for the authored prompt layer
- `input` and `output` for runtime surfaces
- `tools` for executable capabilities
- `subagents` for nested delegation
- `triggers` for automatic wake-up behavior
- `termination` and `compact` for runtime control

A typical creature folder looks like this:

```text
my-creature/
  config.yaml
  prompts/
    system.md
  custom/           # optional
  memory/           # optional
```

## Two kinds of creature usage

There are really two common ways to work with creatures.

### 1. Use a packaged or default creature

This is the fastest path.

Examples:

```bash
kt run @kt-defaults/creatures/general
kt run @kt-defaults/creatures/swe
kt run @kt-defaults/creatures/reviewer
```

### 2. Create your own creature or creature-based agent

You can create a new config from scratch, or inherit from an existing creature and only override what differs.

That inheritance model is one of the most important parts of the system.

## Inheritance model

Use `base_config` when you want to build on an existing creature:

```yaml
name: my_agent
base_config: "@kt-defaults/creatures/swe"

controller:
  llm: claude-sonnet-4.6

input:
  type: cli

output:
  type: stdout
```

This means:

- load the base creature first
- merge this config on top
- keep inherited tools, sub-agents, prompts, and other settings unless overridden

## How merging works

At a high level:

| Kind of field | Merge behavior |
|---------------|----------------|
| scalar values | child overrides base |
| dictionaries | child keys override matching base keys |
| tools / subagents | child list extends base list, typically by name |
| prompt files | base prompt comes first, child prompt is appended |

That gives you a practical workflow:

- put shared behavior in a base creature
- put specialization in child creatures
- put app-specific overrides in the final agent config

## Prompt layering

Prompt inheritance is additive.

If a specialized creature inherits from a base creature, the base prompt is loaded first and the specialized prompt is appended after it.

That is important because it lets you separate:

- general behavior and safety rules
- domain-specific methodology
- app-specific instructions

## Default creature roles

The repo ships a defaults package with several reusable creature profiles.

### `general`

The broad default creature.

Use it when you want a capable general-purpose agent with the standard built-in tools and sub-agents.

### `swe`

Software engineering focused creature.

Use it for coding, repository work, and implementation-heavy tasks.

### `reviewer`

Code review focused creature.

Use it when you want a stricter review posture and structured findings.

### `ops`

Infrastructure and operations focused creature.

Use it for deployment, systems work, monitoring, and environment management.

### `researcher`

Research and analysis focused creature.

Use it for investigation, synthesis, and source-oriented work.

### `creative`

Creative writing focused creature.

Use it for storytelling, drafting, and creative collaboration.

### `root`

Terrarium management creature.

Use it when you want a root agent that operates a team through terrarium management tools.

## Creating a creature from scratch

A minimal example:

```yaml
name: my_agent
version: "1.0"

controller:
  llm: gpt-5.4
  tool_format: native

system_prompt_file: prompts/system.md

input:
  type: cli

output:
  type: stdout

tools:
  - name: bash
    type: builtin
  - name: read
    type: builtin
  - name: write
    type: builtin
  - name: glob
    type: builtin
  - name: grep
    type: builtin
```

And a matching prompt file:

```markdown
# My Creature

You are a focused assistant for repository work.

Priorities:
- understand the code before editing
- make the smallest correct change
- report what changed clearly
```

Run it with:

```bash
kt run path/to/my_agent
```

## Creating a creature by inheriting

This is the more common path for real usage.

```yaml
name: my_team_coder
base_config: "@kt-defaults/creatures/swe"

controller:
  llm: gemini

system_prompt_file: prompts/system.md

input:
  type: cli

output:
  type: stdout
```

Use this when you want to keep the default creature behavior, but customize:

- the model
- the prompt additions
- some tools or sub-agents
- runtime surfaces

## Extending tools and sub-agents

A child creature can add more capabilities on top of its base:

```yaml
tools:
  - name: my_custom_tool
    type: custom
    module: ./custom/my_tool.py
    class: MyTool

subagents:
  - name: critic
    type: builtin
```

Use this when the specialization is capability-based, not just prompt-based.

For the full extension model, see [Custom Modules](custom-modules.md).

## Creatures inside terrariums

When a creature is used inside a terrarium, the terrarium runtime handles the wiring around it:

- channel triggers are injected based on terrarium topology
- communication happens through channels
- the terrarium manages lifecycle and observation

The key idea is that the creature still remains a creature. The terrarium does not turn it into a different abstraction.

## Good design pattern for creature hierarchy

A clean large-scale setup usually looks like this:

```text
base creature
  -> domain creature
    -> app creature
```

Example:

```text
@kt-defaults/creatures/general
  -> @kt-defaults/creatures/swe
    -> your project-specific coding creature
```

That hierarchy works well because each layer has one job:

- base creature defines general behavior
- domain creature defines specialized methodology
- app creature defines local project behavior

## When to make a new creature vs a new terrarium

Make a **new creature** when you need a different standalone agent identity or capability profile.

Make a **new terrarium** when you need multiple creatures cooperating through channels.

If the difference is internal behavior, it is probably a creature concern.
If the difference is topology and communication, it is probably a terrarium concern.

## Related reading

- [Getting Started](getting-started.md)
- [Configuration](configuration.md)
- [Terrariums](terrariums.md)
- [Agents Concept](../concepts/agents.md)
- [Terrariums Concept](../concepts/terrariums.md)
- [Examples](examples.md)
