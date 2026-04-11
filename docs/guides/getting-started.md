# Getting Started

This guide gets you from clone to a working creature or terrarium with the current repository layout.

## What you need

- Python 3.10+
- One supported model provider
  - Codex OAuth through `kt login codex`
  - or an API-backed provider such as OpenRouter, OpenAI, Anthropic, or Gemini

## Install from source

```bash
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
pip install -e .
```

If you want the web dashboard as well:

```bash
pip install -e ".[web]"
```

## Install the defaults package

The repo contains a separate installable defaults package under `kt-defaults/`. The easiest way to use the packaged default creatures and terrariums is to install it through the CLI:

```bash
kt install https://github.com/Kohaku-Lab/kt-defaults.git
```

That gives you package-style paths such as:

- `@kt-defaults/creatures/general`
- `@kt-defaults/creatures/swe`
- `@kt-defaults/terrariums/swe_team`

## Authenticate a model provider

### Option A: Codex OAuth

```bash
kt login codex
```

This is the recommended path for the bundled SWE-oriented defaults.

### Option B: API-backed providers

You can also authenticate and configure other providers:

```bash
kt login openrouter
kt login openai
kt login anthropic
kt login gemini
```

Inspect available models and set a default:

```bash
kt model list
kt model default claude-sonnet-4.6
```

## Run your first creature

The fastest way to start is with an installed default creature:

```bash
kt run @kt-defaults/creatures/swe
```

Or use one of the repository examples:

```bash
kt run examples/agent-apps/planner_agent
kt run examples/agent-apps/monitor_agent
kt run examples/agent-apps/rp_agent
```

If you want a richer terminal interface, choose a mode explicitly:

```bash
kt run @kt-defaults/creatures/swe --mode cli
kt run @kt-defaults/creatures/swe --mode tui
kt run @kt-defaults/creatures/swe --mode plain
```

## Run your first terrarium

To start a multi-agent system:

```bash
kt terrarium run @kt-defaults/terrariums/swe_team
```

You can also run the example terrariums in `examples/terrariums/`.

A terrarium is not a second agent brain. It is a runtime layer that wires multiple creatures together through channels and lifecycle management.

## Resume a saved session

KohakuTerrarium saves session state by default unless disabled.

```bash
kt resume
kt resume --last
kt resume swe_team
```

Session files store much more than a transcript. They capture operational state such as tool calls, job records, channel messages, scratchpad data, and resumable triggers.

## What a creature config looks like

A minimal creature config usually looks like this:

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

The main pieces are:

- `controller` for the LLM and reasoning settings
- `input` and `output` for runtime surfaces
- `tools` for executable capabilities
- `subagents` for nested delegation
- `triggers` for automatic wake-up events
- `system_prompt_file` for the prompt layer

For the full field reference, see [Configuration](configuration.md).

## Recommended next steps

### Learn the main model

- [Creatures](creatures.md)
- [Terrariums](terrariums.md)
- [Overview](../concepts/overview.md)

### Learn configuration and extension

- [Configuration](configuration.md)
- [Custom Modules](custom-modules.md)
- [Plugins](plugins.md)

### Learn code and service integration

- [Programmatic Usage](programmatic-usage.md)
- [Python API](../reference/python.md)
- [HTTP API](../reference/http.md)

### Explore working examples

- [Examples](examples.md)
- [`examples/README.md`](../../examples/README.md)
