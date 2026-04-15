# CLI Reference

This page is the command lookup for the `kt` CLI.

Use it when you already know the workflow you want and need the exact command surface. For a guided setup path, see [Getting Started](../guides/getting-started.md).

## Command map

| Area | Commands |
|------|----------|
| Authentication | `kt login <provider>` |
| Models | `kt model list`, `kt model default <name>`, `kt model show <name>` |
| Config | `kt config show`, `kt config path`, `kt config edit`, `kt config llm ...`, `kt config key ...`, `kt config mcp ...` |
| Standalone creatures | `kt run <path>`, `kt info <path>` |
| Terrariums | `kt terrarium run <path>`, `kt terrarium info <path>` |
| Sessions | `kt resume`, `kt search ...`, `kt embedding ...` |
| Packages | `kt install <source>`, `kt uninstall <name>`, `kt list`, `kt edit <ref>` |
| Extensions | `kt extension list`, `kt extension info <name>` |
| UI and service | `kt web`, `kt app`, `kt serve [start|stop|restart|status|logs]` |
| MCP | `kt mcp list --agent <path>` |
| Misc | `kt update`, `kt version` |

## Authentication

### `kt login <provider>`

Authenticate with a provider.

Common providers:

- `codex`
- `openrouter`
- `openai`
- `anthropic`
- `gemini`
- `mimo`

Examples:

```bash
kt login codex
kt login openrouter
kt login anthropic
kt login mimo
```

Notes:

- `codex` uses OAuth
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
kt model default gpt-5.4
```

### `kt model show <name>`

Show the details for a profile.

```bash
kt model show gpt-5.4
```

## Config management

### `kt config show`

Show the current saved config.

```bash
kt config show
```

### `kt config path`

Print the path to the config file.

```bash
kt config path
```

### `kt config edit`

Open the config file in your editor.

```bash
kt config edit
```

### `kt config llm ...`

Manage saved LLM profiles.

```bash
kt config llm list
kt config llm show default
kt config llm add my-model
kt config llm update my-model
kt config llm delete old-model
kt config llm default my-model
```

### `kt config key ...`

Manage saved API keys and provider credentials.

```bash
kt config key list
kt config key set openai
kt config key delete openai
```

### `kt config mcp ...`

Manage saved MCP server definitions.

```bash
kt config mcp list
kt config mcp add my-server
kt config mcp update my-server
kt config mcp delete my-server
```

## Running standalone creatures

### `kt run <path>`

Run a creature from a local path or installed package reference.

```bash
kt run examples/agent-apps/planner_agent
kt run @kt-defaults/creatures/swe
```

`kt run` defaults to:

- `cli` when running in a TTY
- `plain` when not running in a TTY

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
kt info examples/agent-apps/planner_agent
```

Note: current `kt info` behavior is most reliable with local paths. Package-style `@package/...` refs are supported in `kt run` and `kt terrarium run`, but should not be assumed here unless verified in your environment.

## Interaction modes

### `cli`

Rich terminal interaction mode.

### `tui`

Full-screen Textual interface.

### `plain`

Simple stdout and stdin mode.

## Running terrariums

### `kt terrarium run <path>`

Run a terrarium from a local path or installed package reference.

```bash
kt terrarium run examples/terrariums/code_review_team
kt terrarium run @kt-defaults/terrariums/swe_team
```

`kt terrarium run` defaults to `tui`.

Common options:

| Flag | Purpose |
|------|---------|
| `--mode <cli|tui|plain>` | Choose runtime UI behavior |
| `--session <path>` | Write the terrarium session to a specific file |
| `--no-session` | Disable session persistence |
| `--observe` | Observe channels in plain mode |
| `--no-observe` | Disable channel observation in plain mode |
| `--seed-channel <name>` | Seed channel used for initial non-root input in some flows |
| `--log-level <level>` | Set logging verbosity |

Examples:

```bash
kt terrarium run @kt-defaults/terrariums/swe_team
kt terrarium run @kt-defaults/terrariums/swe_team --mode tui
kt terrarium run @kt-defaults/terrariums/swe_team --mode cli
kt terrarium run examples/terrariums/research_assistant --mode plain --observe
```

Mode notes:

- In `cli` mode, the runtime mounts the root agent when one exists.
- If no root agent exists, `cli` mode auto-mounts the first creature and warns that other creature output is not directly surfaced there.
- In `plain` mode, channel observation can be enabled or disabled explicitly.

### `kt terrarium info <path>`

Show terrarium config information without starting it.

```bash
kt terrarium info examples/terrariums/code_review_team
```

## Session commands

### `kt resume`

Resume a previous session.

```bash
kt resume
kt resume --last
kt resume swe_team
```

Notes:

- Sessions are stored under `~/.kohakuterrarium/sessions/`
- `kt resume` detects whether the saved session is a standalone creature or a terrarium
- Terrarium sessions are resumed into the terrarium TUI

### `kt search ...`

Search saved session history.

```bash
kt search "auth bug"
kt search "database error" --mode fts
kt search "a similar retry problem" --mode semantic
```

Search modes:

- `fts`
- `semantic`
- `hybrid`
- `auto`

Default search mode is `auto`, which uses hybrid behavior when vector search is available.

### `kt embedding ...`

Manage session embeddings used for semantic and hybrid search.

```bash
kt embedding status
kt embedding rebuild
```

Embedding provider selection defaults to `auto`.

## Package commands

### `kt install <source>`

Install a package from Git or a local path.

```bash
kt install https://github.com/Kohaku-Lab/kt-defaults.git
kt install ./my-creatures
kt install ./my-creatures -e
```

Notes:

- installed packages live under `~/.kohakuterrarium/packages/`
- editable installs use link files rather than symlinks
- package refs use `@package/path` syntax

### `kt uninstall <name>`

Remove an installed package.

```bash
kt uninstall my-creatures
```

### `kt list`

List installed packages and available local creature references.

```bash
kt list
```

Note: `kt list` is not the session listing command.

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

## Web, app, and service runtime

### `kt web`

Serve the web UI and API in a single process.

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

### `kt serve`

Manage the web server as a daemon-style service.

```bash
kt serve
kt serve start
kt serve stop
kt serve restart
kt serve status
kt serve logs
```

If no subcommand is given, `kt serve` behaves like `kt serve start`.

## MCP

### `kt mcp list --agent <path>`

List MCP servers declared for an agent.

```bash
kt mcp list --agent examples/agent-apps/planner_agent
```

## Miscellaneous

### `kt update`

Update KohakuTerrarium and optionally installed packages, depending on the environment.

```bash
kt update
```

### `kt version`

Show the installed version.

```bash
kt version
```

## Notes on path types

The CLI accepts both local paths and package references.

### Local path

```bash
kt run examples/agent-apps/planner_agent
kt terrarium run examples/terrariums/code_review_team
```

### Package reference

```bash
kt run @kt-defaults/creatures/swe
kt terrarium run @kt-defaults/terrariums/swe_team
```

## Related reading

- [Getting Started](../guides/getting-started.md)
- [Creatures](../guides/creatures.md)
- [Sessions](../guides/sessions.md)
- [Terrariums](../guides/terrariums.md)
- [Python API](python.md)
- [HTTP API](http.md)
