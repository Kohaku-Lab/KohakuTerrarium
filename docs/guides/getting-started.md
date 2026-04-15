# Getting Started

This guide gets you from installation to a working creature with the current KohakuTerrarium runtime.

The fastest way to understand KohakuTerrarium is not to build everything from scratch.
Start by installing the framework, install the official OOTB creature pack, run a useful creature, and then customize from there.

## What you need

- Python 3.10+
- one supported model provider
  - Codex OAuth through `kt login codex`
  - or an API-backed provider such as OpenRouter, OpenAI, Anthropic, Gemini, or Mimo

## 1. Install KohakuTerrarium

### Install from PyPI

```bash
pip install kohakuterrarium
```

If you want more optional dependencies in one shot:

```bash
pip install "kohakuterrarium[full]"
```

### Install from source

```bash
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
pip install -e ".[dev]"
```

If you are running from source and want `kt web` or `kt app`, build the frontend too:

```bash
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

## 2. Install the official defaults package

The main reusable unit in KohakuTerrarium is the creature, and the easiest way to start is with the official package of OOTB creatures and plugins:

```bash
kt install https://github.com/Kohaku-Lab/kt-defaults.git
```

That gives you package-style references such as:

- `@kt-defaults/creatures/general`
- `@kt-defaults/creatures/swe`
- `@kt-defaults/creatures/reviewer`
- `@kt-defaults/terrariums/swe_team`

You can also install third-party or local packages:

```bash
kt install https://github.com/someone/cool-creatures.git
kt install ./my-creatures
kt install ./my-creatures -e
```

Package installs are stored under `~/.kohakuterrarium/packages/`.
Editable installs use link files instead of symlinks, so local package development stays simple and portable.

## 3. Authenticate a model provider

### Option A: Codex OAuth

```bash
kt login codex
```

This is a common starting point for the bundled SWE-oriented defaults.

### Option B: Other providers

You can also authenticate other providers:

```bash
kt login openrouter
kt login openai
kt login anthropic
kt login gemini
kt login mimo
```

Inspect models and set a default:

```bash
kt model list
kt model default gpt-5.4
```

If you want more direct control over saved config, profiles, API keys, or MCP entries, see:

```bash
kt config show
kt config path
kt config edit
```

## 4. Run your first creature

The fastest useful path is to run an installed default creature:

```bash
kt run @kt-defaults/creatures/swe
```

Other good starting points:

```bash
kt run @kt-defaults/creatures/general
kt run @kt-defaults/creatures/reviewer
kt run @kt-defaults/creatures/researcher
```

You can also run local example creatures from the repository:

```bash
kt run examples/agent-apps/planner_agent
kt run examples/agent-apps/monitor_agent
kt run examples/agent-apps/rp_agent
```

### Standalone run modes

`kt run` defaults to:

- `cli` when running in a TTY
- `plain` when not running in a TTY

You can choose a mode explicitly:

```bash
kt run @kt-defaults/creatures/swe --mode cli
kt run @kt-defaults/creatures/swe --mode tui
kt run @kt-defaults/creatures/swe --mode plain
```

## 5. Optional: run a terrarium

If you want multi-agent composition, run a terrarium:

```bash
kt terrarium run @kt-defaults/terrariums/swe_team
```

You can also run the example terrariums in `examples/terrariums/`.

A terrarium is not a second agent brain. It is a runtime layer that wires multiple creatures together through channels and lifecycle management.

### Terrarium run modes

`kt terrarium run` defaults to `tui`.

You can choose a mode explicitly:

```bash
kt terrarium run @kt-defaults/terrariums/swe_team --mode tui
kt terrarium run @kt-defaults/terrariums/swe_team --mode cli
kt terrarium run @kt-defaults/terrariums/swe_team --mode plain
```

In plain mode, you can observe channel traffic explicitly with `--observe` or disable it with `--no-observe`.

## 6. Web and desktop runtime surfaces

KohakuTerrarium supports several user-facing runtime surfaces:

### Web server / dashboard

```bash
kt serve
kt serve start
kt serve status
kt serve logs
```

`kt serve` manages the web server as a daemon-style process.

### Desktop app

```bash
kt app
```

`kt app` launches the same web UI in a native desktop window.

## 7. Resume a saved session

KohakuTerrarium saves session state by default unless disabled.

```bash
kt resume
kt resume --last
kt resume swe_team
```

Sessions are saved under `~/.kohakuterrarium/sessions/`.

Session files store much more than a transcript. They capture operational state such as:

- conversation history
- tool call metadata
- event logs
- scratchpad state
- sub-agent state
- channel messages
- jobs
- resumable triggers
- config and topology metadata

That history is not only for resuming a past session. It also acts as a searchable knowledge base. Past runs can be searched in full-text or vector form, and agents can retrieve useful history through the built-in memory search tools.

## 8. Search session memory

You can search stored session history directly from the CLI:

```bash
kt search "auth bug"
kt search "why did the deployment fail" --mode fts
kt search "similar incident with websocket reconnects" --mode semantic
```

Search defaults to `auto`, which uses hybrid behavior when vector search is available.

Embedding configuration also defaults to `auto`:

```bash
kt embedding status
kt embedding rebuild
```

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

### I want useful OOTB agents

- [`kt-defaults`](../../kt-defaults/README.md)
- [Creatures](creatures.md)
- [Examples](examples.md)

### I want to customize or build creatures

- [Creatures](creatures.md)
- [Configuration](configuration.md)
- [Custom Modules](custom-modules.md)
- [Plugins](plugins.md)

### I want sessions and searchable memory

- [Sessions](sessions.md)
- [Environment and Session](../concepts/environment.md)

### I want optional multi-agent composition

- [Terrariums](terrariums.md)
- [Channels](../concepts/channels.md)
- [Overview](../concepts/overview.md)

### I want code and service integration

- [Programmatic Usage](programmatic-usage.md)
- [Python API](../reference/python.md)
- [CLI Reference](../reference/cli.md)
- [HTTP API](../reference/http.md)
