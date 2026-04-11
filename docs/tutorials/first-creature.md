# First Creature

This tutorial walks through the smallest useful path for creating and running your own creature.

By the end, you will have:

- a creature folder
- a config file
- a prompt file
- a runnable standalone creature

If you just want to get something running fast, start with [Getting Started](../guides/getting-started.md). This tutorial is for learning the structure by doing it yourself.

## What you are building

A creature is a standalone agent.

In this tutorial, you will build a minimal creature with:

- one controller
- CLI input
- stdout output
- a small built-in tool set
- one system prompt file

## Step 1: create the folder

Create a new folder anywhere you like:

```text
my-creature/
  config.yaml
  prompts/
    system.md
```

## Step 2: write `config.yaml`

Use this minimal config:

```yaml
name: my_creature
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

## Step 3: write `prompts/system.md`

```markdown
# My Creature

You are a careful assistant for repository work.

Rules:
- understand the codebase before editing
- keep changes small and correct
- explain what changed clearly
```

## Step 4: run it

```bash
kt run path/to/my-creature
```

Now ask it something simple, such as:

```text
List the Python files in this repository.
```

## Step 5: understand what just happened

Your creature worked because you defined the main blocks of the agent:

- `controller` gave it a model and tool-call format
- `input` let it receive user input from the CLI
- `output` let it print results
- `tools` gave it actions
- `system_prompt_file` gave it authored behavior

That is the creature abstraction in practice.

## Step 6: inherit instead of starting from scratch

Now try the more common real-world pattern: inherit from a stronger base creature.

Replace `config.yaml` with:

```yaml
name: my_creature
base_config: "@kt-defaults/creatures/swe"

controller:
  llm: gemini

input:
  type: cli

output:
  type: stdout

system_prompt_file: prompts/system.md
```

Now your creature keeps the SWE creature's defaults and only layers your local behavior on top.

This is usually a better long-term workflow than rebuilding everything by hand.

## What you learned

You just learned three important things:

1. a creature is a full agent, not just a prompt
2. the config defines the runtime blocks
3. inheritance is the normal way to build specialized creatures cleanly

## Next steps

- [Creatures](../guides/creatures.md)
- [Configuration](../guides/configuration.md)
- [Plugins and Extensibility](../concepts/plugins.md)
