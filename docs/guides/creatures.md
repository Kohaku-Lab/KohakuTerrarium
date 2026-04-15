# Creatures

A **creature** is KohakuTerrarium's definition of a standalone agent.

It is not just a prompt, not just a tool list, and not just a workflow node. A creature is a complete agent runtime with its own controller, tools, sub-agents, triggers, prompts, session state, and I/O surfaces.

You can run a creature directly:

```bash
kt run <path>
kt run @package/path/to/creature
```

You can also place the same creature inside a terrarium without changing its internal logic.

For the architecture behind this split, see [Agents](../concepts/agents.md) and [Terrariums](../concepts/terrariums.md).

## Creature first, not terrarium first

The creature is the main reusable unit in KohakuTerrarium.

That means the normal workflow is:

1. use a creature directly
2. customize or inherit from a creature
3. package and share creatures
4. optionally compose them into a terrarium later

This matters because many people assume they need to start with a multi-agent setup. In KohakuTerrarium, you usually do not.

## What lives in a creature

A creature config usually defines:

- `controller` for model and reasoning settings
- `system_prompt_file` for the authored prompt layer
- `input` and `output` for runtime surfaces
- `tools` for executable capabilities
- `subagents` for nested delegation
- `triggers` for automatic wake-up behavior
- `termination` and `compact` for runtime control
- session-related behavior such as searchable history and memory usage

A typical creature folder looks like this:

```text
my-creature/
  config.yaml
  prompts/
    system.md
  custom/           # optional
  memory/           # optional
```

## The most common ways to use creatures

### 1. Run an existing creature directly

This is the fastest path.

```bash
kt run @kt-defaults/creatures/general
kt run @kt-defaults/creatures/swe
kt run @kt-defaults/creatures/reviewer
```

You can also run local creatures from this repository or from your own package.

### 2. Inherit from an existing creature

This is the most common real-world customization path.

```yaml
name: my_team_coder
base_config: "@kt-defaults/creatures/swe"

controller:
  llm: claude-sonnet-4.6

system_prompt_file: prompts/system.md
```

Use this when you want to keep most of an existing creature, but change:

- the model
- prompt additions
- tools or sub-agents
- runtime surfaces
- project-specific behavior

### 3. Build a creature from scratch

This is useful when you really want a new standalone agent identity and capability profile.

## CLI terminology note

The product concept is **creature**, but some CLI help text and internal code paths still use the older word **agent** for standalone creature operations.

For example, `kt run` and `kt info` may describe a standalone creature as an "agent" in help output.
In practice, for user-facing architecture, you should think in terms of creatures.

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
| tools / subagents | child list extends the base list, typically by name |
| prompt files | base prompt comes first, child prompt is appended |

That gives you a practical workflow:

- put shared behavior in a base creature
- put specialization in child creatures
- put app-specific overrides in the final creature config

## Prompt layering

Prompt inheritance is additive.

If a specialized creature inherits from a base creature, the base prompt is loaded first and the specialized prompt is appended after it.

That lets you separate:

- general behavior and safety rules
- domain-specific methodology
- app-specific instructions

## Official default creatures

The official [`kt-defaults`](../../kt-defaults/README.md) package ships several reusable creature profiles.

### `general`

The broad default creature.

Use it when you want a capable general-purpose agent with the standard built-in tools and sub-agents.

### `swe`

Software-engineering focused creature.

Use it for coding, repository work, debugging, and implementation-heavy tasks.

### `reviewer`

Review-focused creature.

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

Terrarium-management creature.

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

## Package-based creature workflow

One of the important practical ideas in KohakuTerrarium is that creatures are meant to be packaged, installed, reused, and shared.

Typical workflow:

1. install an official or third-party package
2. run a creature directly with `@package/...`
3. inherit from that creature if you need customization
4. publish your own package later if needed

Examples:

```bash
kt install https://github.com/Kohaku-Lab/kt-defaults.git
kt install https://github.com/someone/cool-creatures.git

kt run @kt-defaults/creatures/swe
kt run @cool-creatures/creatures/my-agent
```

## Creatures inside terrariums

When a creature is used inside a terrarium, the terrarium runtime handles the wiring around it:

- channel triggers are injected based on terrarium topology
- communication happens through channels
- the terrarium manages lifecycle and observation
- send/listen behavior is determined by terrarium wiring, not by rewriting the creature itself

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
- [Sessions](sessions.md)
- [Terrariums](terrariums.md)
- [Agents Concept](../concepts/agents.md)
- [Terrariums Concept](../concepts/terrariums.md)
- [Examples](examples.md)
