# CLI Reference

This page is the command lookup for the `kt` CLI.

Use it when you already know the workflow you want and need the exact command surface. For a guided setup path, see [Getting Started](../guides/getting-started.md).

## Command map

| Area | Commands |
|------|----------|
| Authentication | `kt login <provider>` |
| Models | `kt model list`, `kt model default <name>`, `kt model show <name>` |
| Standalone creatures | `kt run <path>` , `kt info <path>` |
| Terrariums | `kt terrarium run <path>` , `kt terrarium info <path>` |
| Sessions | `kt resume`, `kt search <session> <query>`, `kt embedding <session>` |
| Packages | `kt install <source>`, `kt uninstall <name>`, `kt list`, `kt edit <ref>` |
| Extensions | `kt extension list`, `kt extension info <name>` |
| UI and service | `kt web`, `kt app` |
| MCP | `kt mcp list --agent <path>` |

## Authentication

### `kt login <provider>`

Authenticate with a provider.

Common providers:

- `codex`
- `openrouter`
- `openai`
- `anthropic`
- `gemini`

Examples:

```bash
kt login codex
kt login openrouter
kt login anthropic
```

Notes:

- `codex` uses OAuth and is suited to the bundled Codex-oriented defaults
- API-backed providers store credentials in the local KohakuTerrarium config area

## Model management

### `kt model list`

List available LLM profiles and presets.

```bash
kt model list
```

### `kt model default <name>`

Set the default model profile.

```bash
kt model default claude-sonnet-4.6
```

### `kt model show <name>`

Show the details for a profile.

```bash
kt model show gpt-5.4
```

## Running standalone creatures

### `kt run <path>`

Run a creature from a local path or installed package reference.

```bash
kt run examples/agent-apps/planner_agent
kt run @kt-defaults/creatures/swe
```

Common options:

| Flag | Purpose |
|------|---------|
| `--mode <cli|tui|plain>` | Choose the interaction surface |
| `--llm <profile>` | Override the model profile for this run |
| `--session <path>` | Write the session to a specific file |
| `--no-session` | Disable session persistence |
| `--log-level <level>` | Set logging verbosity |

Examples:

```bash
kt run @kt-defaults/creatures/swe --mode cli
kt run @kt-defaults/creatures/swe --mode tui
kt run @kt-defaults/creatures/swe --llm gemini
kt run examples/agent-apps/monitor_agent --no-session
```

### `kt info <path>`

Show creature config information without starting the runtime.

```bash
kt info @kt-defaults/creatures/swe
```

## Interaction modes

### `cli`

Rich terminal interaction mode.

Best for most day-to-day usage.

### `tui`

Full-screen Textual interface.

Best for dashboard-style use and multi-agent work.

### `plain`

Simple stdout and stdin mode.

Best for piping, CI, logging, and minimal terminal environments.

## Running terrariums

### `kt terrarium run <path>`

Run a terrarium from a local path or installed package reference.

```bash
kt terrarium run examples/terrariums/code_review_team
kt terrarium run @kt-defaults/terrariums/swe_team
```

Common options:

| Flag | Purpose |
|------|---------|
| `--mode <cli|tui|plain>` | Choose runtime UI behavior when supported |
| `--session <path>` | Write the terrarium session to a specific file |
| `--no-session` | Disable session persistence |
| `--log-level <level>` | Set logging verbosity |

Examples:

```bash
kt terrarium run @kt-defaults/terrariums/swe_team
kt terrarium run @kt-defaults/terrariums/swe_team --mode tui
kt terrarium run examples/terrariums/research_assistant --no-session
```

### `kt terrarium info <path>`

Show terrarium config information without starting it.

```bash
kt terrarium info @kt-defaults/terrariums/swe_team
```

## Session commands

### `kt resume`

Resume a previous session.

```bash
kt resume
kt resume --last
kt resume swe_team
```

### `kt search <session> <query>`

Search a saved session.

```bash
kt search my_session "auth bug"
kt search my_session "database error" --mode fts
```

### `kt embedding <session>`

Build or refresh an embedding index for a session.

```bash
kt embedding my_session
```

## Package commands

### `kt install <source>`

Install a package from Git or a local path.

```bash
kt install https://github.com/Kohaku-Lab/kt-defaults.git
kt install ./my-creatures -e
```

### `kt uninstall <name>`

Remove an installed package.

```bash
kt uninstall my-creatures
```

### `kt list`

List installed packages and available references.

```bash
kt list
```

### `kt edit <ref>`

Open an installed config reference in your editor.

```bash
kt edit @kt-defaults/creatures/general
```

## Extension commands

### `kt extension list`

List installed extension modules.

```bash
kt extension list
```

### `kt extension info <name>`

Show extension details for a package.

```bash
kt extension info kt-defaults
```

## Web and desktop

### `kt web`

Serve the web UI and API.

```bash
kt web
kt web --port 8001
```

### `kt app`

Launch the desktop application shell.

```bash
kt app
kt app --port 8001
```

## MCP

### `kt mcp list --agent <path>`

List MCP servers declared for an agent.

```bash
kt mcp list --agent @kt-defaults/creatures/swe
```

## Notes on path types

The CLI accepts both local paths and package references.

### Local path

```bash
kt run examples/agent-apps/planner_agent
```

### Package reference

```bash
kt run @kt-defaults/creatures/swe
kt terrarium run @kt-defaults/terrariums/swe_team
```

## Related reading

- [Getting Started](../guides/getting-started.md)
- [Creatures](../guides/creatures.md)
- [Terrariums](../guides/terrariums.md)
- [Python API](python.md)
- [HTTP API](http.md)
