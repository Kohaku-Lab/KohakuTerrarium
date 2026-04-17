# CLI Reference

Every `kt` command, subcommand, and flag. The CLI is the operator-facing
surface over the framework: starting creatures, starting terrariums,
managing packages, configuring LLMs, serving the web UI, and searching
saved sessions.

For the mental model of creatures, terrariums, and the root agent, read
[concepts/boundaries](../concepts/boundaries.md). For task-oriented
paths, see [guides/getting-started](../guides/getting-started.md) and
[guides/creatures](../guides/creatures.md).

## Entry points

- `kt` — installed console script.
- `python -m kohakuterrarium` — equivalent.
- Invoked with no subcommand (e.g. from a Briefcase double-click), `kt`
  starts the desktop app automatically.

## Global flags

| Flag | Purpose |
|---|---|
| `--version` | Print version, install source, package path, Python version, and git commit. |
| `--verbose` | With `--version`, also print `$VIRTUAL_ENV`, executable, and git branch. |

---

## Core commands

### `kt run`

Run a single creature.

```
kt run <agent_path> [flags]
```

Positional:

- `agent_path` — local folder containing `config.yaml`, or a package
  reference like `@kt-defaults/creatures/swe`.

Flags:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--log-level` | `DEBUG\|INFO\|WARNING\|ERROR` | `INFO` | Root logger level. |
| `--session` | path | auto | Session file to write; absolute or name under `~/.kohakuterrarium/sessions/`. |
| `--no-session` | flag | — | Disable session persistence entirely. |
| `--llm` | str | — | Override LLM profile (e.g. `gpt-5.4`, `claude-opus-4.6`). |
| `--mode` | `cli\|plain\|tui` | auto | Interaction mode. Defaults to `cli` on TTY, `plain` otherwise. |

Behaviour:

- `@package/...` paths resolve to `~/.kohakuterrarium/packages/<pkg>/...`,
  following `.link` pointers for editable installs.
- A session is auto-created under `~/.kohakuterrarium/sessions/` with
  extension `.kohakutr` unless `--no-session` is set.
- On exit, prints a `kt resume <name>` hint.
- Ctrl+C triggers graceful shutdown.

### `kt resume`

Resume a prior session. Agent vs terrarium is auto-detected from the
session file.

```
kt resume [session] [flags]
```

Positional:

- `session` — name prefix, full filename, or full path. Omit for an
  interactive picker of the 10 most-recent sessions.

Flags:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--pwd` | path | session's stored cwd | Override working directory. |
| `--last` | flag | — | Resume the most-recent session without prompting. |
| `--log-level` | as `kt run` | | |
| `--mode` | as `kt run` | | Terrarium sessions force `tui`. |
| `--llm` | str | | Override LLM profile for the resumed session. |

Behaviour:

- `.kohakutr` and legacy `.kt` extensions are accepted and stripped.
- Prefix matches that are ambiguous trigger a picker.

### `kt list`

List installed packages and local agents.

```
kt list [--path agents]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--path` | str | `agents` | Local folder to scan in addition to installed packages. |

### `kt info`

Print name, description, model, tools, sub-agents, and files for a
creature config.

```
kt info <agent_path>
```

---

## Terrarium

### `kt terrarium run`

Run a multi-agent terrarium.

```
kt terrarium run <terrarium_path> [flags]
```

Positional:

- `terrarium_path` — YAML file or `@package/terrariums/<name>`.

Flags:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--log-level` | as `kt run` | | |
| `--seed` | str | — | Prompt to inject into the seed channel at startup. |
| `--seed-channel` | str | `seed` | Channel to receive `--seed`. |
| `--observe` | list of channel names | — | Channels to observe (plain/log mode). |
| `--no-observe` | flag | — | Disable observation entirely. |
| `--session` | path | auto | Session file path. |
| `--no-session` | flag | — | Disable persistence. |
| `--llm` | str | — | Override LLM profile for *every* creature (and root). |
| `--mode` | `cli\|plain\|tui` | `tui` | UI mode. |

Behaviour:

- `tui` mounts multi-tab view: root + each creature + each channel.
- `cli` mounts root (if present) or the first creature under RichCLI.
- `plain` streams observed channel messages to stdout.

### `kt terrarium info`

Print terrarium name, creatures, listen/send channels, and channel list.

```
kt terrarium info <terrarium_path>
```

---

## Packages

### `kt install`

Install a package from a git URL or local path.

```
kt install <source> [-e|--editable] [--name <name>]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `-e`, `--editable` | flag | — | Write a `<name>.link` pointing at the source instead of copying. |
| `--name` | str | derived from URL/path | Override installed package name. |

`<source>` may be:

- A git URL (cloned into `~/.kohakuterrarium/packages/<name>`).
- A local directory (copied, or linked with `-e`).

### `kt uninstall`

Remove an installed package.

```
kt uninstall <name>
```

### `kt update`

Update git-backed packages. Skips editable and non-git packages.

```
kt update [target] [--all]
```

| Flag | Type | Description |
|---|---|---|
| `--all` | flag | Update every git-backed package. |

### `kt edit`

Open a creature or terrarium config in `$EDITOR` (falls back to
`$VISUAL`, then `nano`).

```
kt edit <target>
```

`target` accepts package refs (`@pkg/creatures/name`) and local paths.

---

## Configuration: `kt config`

### `kt config show`

Print every config file path used by the CLI.

### `kt config path`

Print the path for one of: `home`, `llm_profiles`, `api_keys`,
`mcp_servers`, `ui_prefs`.

```
kt config path [name]
```

### `kt config edit`

Open a config file in `$EDITOR`. Defaults to `llm_profiles` when no name
is given.

```
kt config edit [name]
```

### `kt config provider` (alias: `kt config backend`)

Manage LLM providers (backends).

#### `kt config provider list`

Show Name, Backend Type, and Base URL for each provider.

#### `kt config provider add`

Interactive. Prompts for backend type (`openai`, `codex`, `anthropic`),
base URL, and `api_key_env`.

```
kt config provider add [name]
```

#### `kt config provider edit`

Same fields as `add`, pre-filled from the current entry.

```
kt config provider edit <name>
```

#### `kt config provider delete`

```
kt config provider delete <name>
```

### `kt config llm` (aliases: `kt config model`, `kt config preset`)

Manage LLM presets.

#### `kt config llm list`

Show Name, Provider, Model, and a Default marker.

#### `kt config llm show`

Print the full preset: provider, model, max_context, max_output,
base_url, api_key_env, temperature, reasoning_effort, service_tier,
extra_body.

```
kt config llm show <name>
```

#### `kt config llm add`

Interactive. Optionally marks the new preset as default.

```
kt config llm add [name]
```

#### `kt config llm edit`

```
kt config llm edit <name>
```

#### `kt config llm delete`

```
kt config llm delete <name>
```

#### `kt config llm default`

Without argument, print the current default. With `name`, set it.

```
kt config llm default [name]
```

### `kt config key`

Manage stored API keys.

#### `kt config key list`

Columns: provider, api_key_env, source (`stored`/`env`/`missing`),
masked value.

#### `kt config key set`

Save an API key to `~/.kohakuterrarium/api_keys.yaml`. Prompts
(masked) if `value` is omitted.

```
kt config key set <provider> [value]
```

#### `kt config key delete`

Clear the stored key (the provider entry itself stays intact).

```
kt config key delete <provider>
```

### `kt config login`

Alias of `kt login`. See [Auth](#auth).

### `kt config mcp`

Manage the global MCP server catalog
(`~/.kohakuterrarium/mcp_servers.yaml`).

- `list` — show file path and server inventory.
- `add [name]` — interactive. Prompts for transport (`stdio`/`http`),
  command, args JSON, env JSON, URL.
- `edit <name>` — interactive edit.
- `delete <name>` — remove entry.

---

## Auth

### `kt login`

Authenticate with a provider.

```
kt login <provider>
```

- For `codex` backend: OAuth device-code flow. Tokens stored at
  `~/.kohakuterrarium/codex-auth.json`.
- For API-key backends: prompts (masked) and saves to
  `~/.kohakuterrarium/api_keys.yaml`.

---

## Models

### `kt model`

Thin wrappers over `kt config llm`:

```
kt model list
kt model default [name]
kt model show <name>
```

---

## Memory and search

### `kt embedding`

Build FTS and vector indices for a saved session.

```
kt embedding <session> [--provider ...] [--model ...] [--dimensions N]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--provider` | `auto\|model2vec\|sentence-transformer\|api` | `auto` | Auto prefers jina-v5-nano. |
| `--model` | str | provider-dependent | Provider-specific model, including aliases like `@tiny`, `@best`, `@multilingual-best`. |
| `--dimensions` | int | — | Matryoshka truncation (shorter vectors). |

### `kt search`

Search a session's memory.

```
kt search <session> <query> [flags]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--mode` | `fts\|semantic\|hybrid\|auto` | `auto` | Search mode. Auto picks semantic when vectors exist, else FTS. |
| `--agent` | str | — | Restrict to events from one agent. |
| `-k` | int | `10` | Max results. |

---

## Web and desktop UI

### `kt web`

Run the web server (blocking, single process).

```
kt web [flags]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--host` | str | `127.0.0.1` | Bind host. |
| `--port` | int | `8001` | Bind port. Auto-increments if busy. |
| `--dev` | flag | — | API-only (serve frontend separately via `vite dev`). |
| `--log-level` | as `kt run` | | |

### `kt app`

Run the native desktop build (requires pywebview).

```
kt app [--port 8001] [--log-level ...]
```

### `kt serve`

Daemon management for the web server. Process state lives under
`~/.kohakuterrarium/run/web.{pid,json,log}`.

#### `kt serve start`

Start a detached server process.

```
kt serve start [--host 127.0.0.1] [--port 8001] [--dev] [--log-level INFO]
```

#### `kt serve stop`

Send SIGTERM, then SIGKILL after the grace period.

```
kt serve stop [--timeout 5.0]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--timeout` | float | `5.0` | Seconds to wait for graceful shutdown. |

#### `kt serve restart`

`stop` then `start`, forwarding all flags to `start`.

#### `kt serve status`

Print `running` / `stopped` / `stale`, PID, URL, started_at, version,
git commit.

#### `kt serve logs`

Read `~/.kohakuterrarium/run/web.log`.

```
kt serve logs [--follow] [--lines 80]
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--follow` | flag | — | Tail the log. |
| `--lines` | int | `80` | Initial lines to print. |

---

## Extensions

### `kt extension list`

List every tool, plugin, and LLM preset contributed by installed
packages. Marks editable installs.

### `kt extension info`

Show package metadata plus its creatures, terrariums, tools, plugins,
and LLM presets.

```
kt extension info <name>
```

---

## MCP (per-agent)

### `kt mcp list`

List MCP servers declared in an agent's `config.yaml` `mcp_servers:`
section. Columns: name, transport, command, URL, args, env keys.

```
kt mcp list --agent <path>
```

---

## File paths

| Path | Purpose |
|---|---|
| `~/.kohakuterrarium/` | Home. |
| `~/.kohakuterrarium/llm_profiles.yaml` | LLM presets and providers. |
| `~/.kohakuterrarium/api_keys.yaml` | Stored API keys. |
| `~/.kohakuterrarium/mcp_servers.yaml` | Global MCP server catalog. |
| `~/.kohakuterrarium/ui_prefs.json` | UI preferences. |
| `~/.kohakuterrarium/codex-auth.json` | Codex OAuth tokens. |
| `~/.kohakuterrarium/sessions/*.kohakutr` | Saved sessions (legacy `*.kt` also accepted). |
| `~/.kohakuterrarium/packages/` | Installed packages (copies or `.link` pointers). |
| `~/.kohakuterrarium/run/web.{pid,json,log}` | Web daemon state. |

## Environment variables

| Var | Purpose |
|---|---|
| `EDITOR`, `VISUAL` | Editor for `kt edit` / `kt config edit`. |
| `VIRTUAL_ENV` | Reported by `kt --version --verbose`. |
| `<PROVIDER>_API_KEY` | Whatever `api_key_env` each provider references. |
| `KT_SHELL_PATH` | Override shell used by the `bash` tool. |
| `KT_SESSION_DIR` | Override session directory for the web API (default `~/.kohakuterrarium/sessions`). |

## Exit codes

- `0` — success.
- `1` — generic error.
- Editor exit code — for `kt edit` / `kt config edit`.

## Interactive prompts

These commands may drop into interactive prompts:

- `kt resume` with no argument, or ambiguous prefix.
- `kt terrarium run` when there is no root and no `--seed`.
- `kt login`.
- Every `... add` subcommand under `kt config`.
- `kt config key set` with no value.

## Package reference syntax

`@<pkg-name>/<path-inside-pkg>` resolves to
`~/.kohakuterrarium/packages/<pkg-name>/<path-inside-pkg>`, or follows
`<pkg-name>.link`. Accepted by `kt run`, `kt terrarium run`, `kt edit`,
`kt update`, and `kt info`.

## Terrarium TUI slash commands

Inside `kt terrarium run --mode tui`, the input bar accepts slash
commands. Built-ins: `/exit`, `/quit`. Additional commands come from
the terrarium's registered user commands. See
[builtins.md#user-commands](builtins.md#user-commands).

## See also

- Concepts: [boundaries](../concepts/boundaries.md),
  [session persistence](../concepts/impl-notes/session-persistence.md).
- Guides: [getting-started](../guides/getting-started.md),
  [sessions](../guides/sessions.md),
  [terrariums](../guides/terrariums.md).
- Reference: [configuration](configuration.md), [builtins](builtins.md),
  [http](http.md).
