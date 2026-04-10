# CLI Reference

KohakuTerrarium provides a command-line interface (`kt`) for running agents, managing terrariums, searching sessions, and configuring LLM providers.

## Installation

```bash
uv pip install -e .
```

After installation, all commands are available via `kt`.

## Quick Reference

| Command | Description |
|---------|-------------|
| `kt login <provider>` | Authenticate with an LLM provider |
| `kt model list` | List all available LLM profiles |
| `kt model default <name>` | Set the default LLM profile |
| `kt model show <name>` | Show detailed profile info |
| `kt run <path>` | Run an agent |
| `kt terrarium run <path>` | Run a multi-agent terrarium |
| `kt terrarium info <path>` | Show terrarium config without running |
| `kt resume [session]` | Resume a saved session |
| `kt list` | List installed packages and local agents |
| `kt search <session> <query>` | Search a session's memory |
| `kt embedding <session>` | Build embedding index for a session |
| `kt install <source>` | Install a creature/terrarium package |
| `kt uninstall <name>` | Remove an installed package |
| `kt edit <ref>` | Open a package config in your editor |
| `kt extension list` | List installed extension modules |
| `kt extension info <name>` | Show details of a package's extensions |
| `kt web` | Serve web UI + API |
| `kt app` | Launch native desktop UI |
| `kt mcp list --agent <path>` | List MCP servers from agent config |
| `kt info <path>` | Show agent config info |

## Authentication

### `kt login` - Authenticate with a Provider

```bash
kt login <provider>
```

Supported providers: `codex`, `openrouter`, `openai`, `anthropic`, `gemini`, `mimo`.

**Codex** uses OAuth (opens a browser for ChatGPT login). After authenticating, agents with `auth_mode: codex-oauth` will use your ChatGPT Plus/Pro subscription. Tokens are saved to `~/.kohakuterrarium/codex-auth.json`.

**All other providers** prompt for an API key interactively. Keys are stored in `~/.kohakuterrarium/api_keys.yaml`. If a key already exists, you are shown a masked preview and asked to confirm replacement.

**Workflow example:**

```bash
# 1. Add your API key
kt login openrouter

# 2. See available models
kt model list

# 3. Set a default model
kt model default gpt-5.4

# 4. Run an agent (uses default model, or override with --llm)
kt run @kt-defaults/creatures/swe
kt run @kt-defaults/creatures/swe --llm claude-sonnet-4
```

## Model Management

### `kt model list` - List LLM Profiles

```bash
kt model list
```

Shows all available profiles (built-in presets and user-defined) with model name, provider, context size, and source. The current default is marked with `*`.

**Sample output:**

```
Default model: gpt-5.4

Name                      Model                                    Provider       Context    Src
-------------------------------------------------------------------------------------------------
gpt-5.4                   gpt-5.4                                  openai         1000k      preset *
claude-sonnet-4           claude-sonnet-4-20250514                  anthropic      200k       preset
gemini                    gemini-2.5-pro                            gemini         1000k      preset
```

### `kt model default` - Set Default Model

```bash
kt model default <name>
```

Sets the default LLM profile. The name must match a profile from `kt model list`.

```bash
kt model default claude-sonnet-4
```

### `kt model show` - Show Profile Details

```bash
kt model show <name>
```

Displays full configuration for a profile: provider, model, context window, max output tokens, base URL, API key env var, temperature, reasoning effort, and extra body parameters.

```bash
kt model show gpt-5.4
```

## Running Agents

### `kt run` - Run an Agent

```bash
kt run <agent_path> [options]
```

Start an agent from a config folder and enter the interactive event loop. The agent folder must contain a `config.yaml` or `config.yml` file.

Paths starting with `@` are resolved as package references (e.g., `@kt-defaults/creatures/swe`).

**Arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `agent_path` | Yes | Path to agent config folder, or `@package/creatures/name` |

**Options:**

| Flag | Values | Default | Description |
|------|--------|---------|-------------|
| `--mode` | `cli`, `plain`, `tui` | `cli` if stdout is a TTY, else `plain` | Input/output mode — see [Interactive Modes](#interactive-modes) |
| `--llm` | profile name | (from config) | Override LLM profile (e.g., `gpt-5.4`, `claude-sonnet-4`) |
| `--session` | path | auto | Session file path; auto-generates in `~/.kohakuterrarium/sessions/` |
| `--no-session` | flag | off | Disable session persistence entirely |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` | Logging verbosity |

**Session behavior:** By default, sessions are saved automatically to `~/.kohakuterrarium/sessions/<agent_name>_<id>.kohakutr`. Use `--no-session` to disable. Use `--session <path>` to specify a custom file path.

**Examples:**

```bash
# Run a local agent (auto-picks rich CLI on a TTY, plain otherwise)
kt run examples/agent-apps/swe_agent

# Run a package agent in the full-screen Textual TUI
kt run @kt-defaults/creatures/swe --mode tui

# Run in dumb stdout mode (for CI / piping)
kt run @kt-defaults/creatures/swe --mode plain

# Override the LLM profile
kt run @kt-defaults/creatures/swe --llm gemini

# Disable session recording
kt run examples/agent-apps/swe_agent --no-session

# Run with debug logging
kt run examples/agent-apps/swe_agent --log-level DEBUG
```

## Interactive Modes

The `--mode` flag picks the input/output frontend.

### `cli` — Rich inline CLI (default on a TTY)

A `prompt_toolkit` `Application` with a fixed layout: a bordered input box (auto-sizes to its content), a live status region above it (streaming assistant message + running tool panels + sub-agent progress), and a one-line footer (token usage, model name, key hints). Finished content is committed to **real terminal scrollback**, so you can mouse-scroll up and copy anything from earlier in the session just like in Claude Code or Codex.

**Key bindings:**

| Key | Action |
|-----|--------|
| `Enter` | Submit message |
| `Shift+Enter` / `Ctrl+Enter` | Insert newline. Requires a terminal that emits `modifyOtherKeys=2` or CSI-u (Windows Terminal, xterm, kitty, foot, alacritty with progressive enhancement). The CLI enables both protocols automatically on start. |
| `Alt+Enter` / `Ctrl+J` | Insert newline. Works in every PTY — the universal fallback. |
| `Esc` | Interrupt the current agent turn (cancel running tools, stop streaming, keep the session alive) |
| `Ctrl+B` | Promote the latest running **direct** tool or sub-agent to background — the agent loop returns a placeholder immediately and the task keeps running |
| `Ctrl+X` | Cancel the latest **backgrounded** tool or sub-agent |
| `Ctrl+C` | Clear the input buffer if non-empty, else interrupt |
| `Ctrl+L` | Clear the terminal |
| `Ctrl+D` | Exit the session |
| `/` | Open the slash-command popup (tab-completes from the builtin command registry) |

**Sub-agent panels** render as cards:

- **Direct sub-agent** — one card with `header → tool list → output preview → footer (turns · tools · in↑ out↓ tokens)`. The tool list shows each nested tool as it's called, in the order it ran.
- **Background sub-agent** — two cards. An inline notice (`⏳ dispatched agent_name in background`) commits to scrollback the moment it's dispatched. When it finishes, the full result panel commits below.

**Session resume (`kt resume <name> --mode cli`)** replays the recorded events into real terminal scrollback before the live loop starts: user messages, assistant turns (rendered as markdown when appropriate), sub-agent panels (with full tool lists), bg dispatch notices, and compaction notices — everything uses the exact same render path as live streaming, so the history visually matches what you saw when the session was originally running.

### `tui` — Full-screen Textual TUI

An alt-screen Textual app with a tabbed chat view (one tab per agent / channel in terrarium mode), a right-side running-jobs panel with **click-to-cancel** and **click-to-promote** (`[→bg]` appears on the right of a direct job's line after it's been running for a second), and compact summary / token-usage widgets. Best for multi-agent terrariums or when you want a dashboard-style view. Alt-screen means the transcript **does not** persist in your terminal's scrollback — exit returns you to the pre-launch screen.

### `plain` — Dumb stdout/stdin

Forward-only print output, line-based stdin. No live region, no input box, no re-rendering. Auto-selected when stdout is not a TTY (piping, CI, log capture). Also useful when you want to grep the output in real time or you're running in a terminal that doesn't support ANSI cursor movement.

## Running Terrariums

### `kt terrarium run` - Run a Terrarium

```bash
kt terrarium run <terrarium_path> [options]
```

Start a multi-agent terrarium. If the terrarium config declares a root agent, the TUI launches with tabs for the root, each creature, and each channel. Without a root agent, a simpler seed/observe CLI is used.

Paths starting with `@` are resolved as package references.

**Arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `terrarium_path` | Yes | Path to terrarium config folder/file, or `@package/terrariums/name` |

**Options:**

| Flag | Values | Default | Description |
|------|--------|---------|-------------|
| `--seed` | string | (prompt) | Seed prompt to inject on startup |
| `--seed-channel` | string | `seed` | Channel to send the seed prompt to |
| `--observe` | channel names | all | Channels to observe (space-separated) |
| `--no-observe` | flag | off | Disable channel observation entirely |
| `--session` | path | auto | Session file path |
| `--no-session` | flag | off | Disable session persistence |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` | Logging verbosity |

**Seed prompt behavior:** If the terrarium config declares a `seed` channel and no `--seed` flag is provided, the CLI prompts the user to enter a seed interactively.

**Observation (no-root mode only):** By default, all declared channels are observed. When `--observe` is used with channel names, only those channels are watched. Use `--no-observe` to disable. Observed messages are printed with timestamp, channel name, sender, and content preview.

**Examples:**

```bash
# Run a terrarium (TUI if root agent is configured)
kt terrarium run examples/terrariums/novel_terrarium/

# Run with a seed prompt
kt terrarium run examples/terrariums/novel_terrarium/ --seed "Write a cyberpunk story"

# Observe specific channels only
kt terrarium run examples/terrariums/novel_terrarium/ --observe ideas outline draft

# Run without observation
kt terrarium run examples/terrariums/novel_terrarium/ --no-observe

# Disable session recording
kt terrarium run examples/terrariums/novel_terrarium/ --no-session
```

### `kt terrarium info` - Show Terrarium Info

```bash
kt terrarium info <terrarium_path>
```

Display terrarium configuration without running it: creature names, base configs, channel assignments, output log settings, and channel types/descriptions.

**Example:**

```bash
kt terrarium info examples/terrariums/novel_terrarium/
```

**Sample output:**

```
Terrarium: novel_writer
========================================

Creatures (3):
  brainstorm
    base: creatures/brainstorm
    listen: ['feedback']
    send:   ['ideas', 'team_chat']
  planner
    base: creatures/planner
    listen: ['ideas']
    send:   ['outline', 'team_chat']
  writer
    base: creatures/writer
    listen: ['outline']
    send:   ['draft', 'feedback', 'team_chat']

Channels (5):
  ideas (queue) - Raw ideas from brainstorm to planner
  outline (queue) - Chapter outlines from planner to writer
  draft (queue) - Written chapters for review
  feedback (queue) - Feedback from writer back to brainstorm
  team_chat (broadcast) - Team-wide status updates
```

## Session Management

### `kt resume` - Resume a Session

```bash
kt resume [session] [options]
```

Resume a previously saved session from a `.kohakutr` file. Restores conversation history, scratchpad state, and event log. Both standalone agent sessions and terrarium sessions are supported.

The `session` argument accepts several forms: a full file path, a filename, a name prefix (matched against `~/.kohakuterrarium/sessions/`), or omitted entirely to list recent sessions interactively.

**Arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `session` | No | Session name/prefix, full path, or omit to list recent sessions |

**Options:**

| Flag | Values | Default | Description |
|------|--------|---------|-------------|
| `--last` | flag | off | Resume the most recent session (no interactive picker) |
| `--mode` | `cli`, `tui` | `tui` | Input/output mode |
| `--pwd` | path | (original) | Override working directory |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` | Logging verbosity |

**Examples:**

```bash
# List recent sessions and pick interactively
kt resume

# Resume the most recent session
kt resume --last

# Resume by name prefix
kt resume swe_agent

# Resume a specific file
kt resume ~/.kohakuterrarium/sessions/swe_agent_01a2b3c4.kohakutr

# Resume in CLI mode
kt resume swe_agent --mode cli
```

### `kt list` - List Agents and Packages

```bash
kt list [options]
```

Shows installed packages (from `~/.kohakuterrarium/packages/`) with their creatures and terrariums, then scans a local directory for agent folders.

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--path` | `agents` | Path to local agents directory |

**Examples:**

```bash
kt list
kt list --path /path/to/agents
```

## Session Search

### `kt search` - Search Session Memory

```bash
kt search <session> <query> [options]
```

Search a session's indexed memory. Supports full-text search (FTS), semantic search (requires embeddings), and hybrid mode.

**Arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `session` | Yes | Session name/prefix or path |
| `query` | Yes | Search query string |

**Options:**

| Flag | Values | Default | Description |
|------|--------|---------|-------------|
| `--mode` | `fts`, `semantic`, `hybrid`, `auto` | `auto` | Search mode |
| `--agent` | agent name | (all) | Filter results by agent name |
| `-k` | integer | `10` | Maximum number of results |

**Search modes:**

- `fts`: Full-text search only (always available)
- `semantic`: Vector similarity search (requires `kt embedding` first)
- `hybrid`: Combines FTS and semantic results
- `auto`: Uses hybrid if vectors are available, falls back to FTS

**Examples:**

```bash
# Search with auto mode
kt search swe_agent "file reading bug"

# FTS only, filter by agent
kt search my_session "config loading" --mode fts --agent root

# Semantic search, top 5
kt search my_session "error handling patterns" --mode semantic -k 5
```

### `kt embedding` - Build Embedding Index

```bash
kt embedding <session> [options]
```

Build a vector embedding index for a session's events. This is required before using `semantic` or `hybrid` search modes.

**Arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `session` | Yes | Session name/prefix or path |

**Options:**

| Flag | Values | Default | Description |
|------|--------|---------|-------------|
| `--provider` | `auto`, `model2vec`, `sentence-transformer`, `api` | `auto` | Embedding provider (auto prefers jina v5 nano) |
| `--model` | model name | (provider-dependent) | Override embedding model |
| `--dimensions` | integer | (model default) | Embedding dimensions (Matryoshka truncation) |

**Examples:**

```bash
# Build with default provider
kt embedding swe_agent

# Use a specific provider and model
kt embedding swe_agent --provider sentence-transformer --model all-MiniLM-L6-v2

# Specify dimensions for Matryoshka embeddings
kt embedding swe_agent --dimensions 256
```

## Package Management

### `kt install` - Install a Package

```bash
kt install <source> [options]
```

Install a creature/terrarium package from a git URL or local path. Packages are stored in `~/.kohakuterrarium/packages/`.

**Arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `source` | Yes | Git URL or local path to the package |

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `-e`, `--editable` | off | Install as editable (symlink, like `pip -e`) |
| `--name` | (auto) | Override package name |

After installation, package contents are referenced with `@package_name/creatures/<name>` or `@package_name/terrariums/<name>`.

**Examples:**

```bash
# Install from git
kt install https://github.com/user/my-creatures.git

# Install from local path (editable)
kt install ./my-creatures -e

# Install with custom name
kt install ./my-creatures --name my-custom-pack
```

### `kt uninstall` - Remove a Package

```bash
kt uninstall <name>
```

Remove an installed package by name.

```bash
kt uninstall my-creatures
```

### `kt edit` - Edit Package Config

```bash
kt edit <target>
```

Open a creature or terrarium config file in your editor (`$EDITOR`, `$VISUAL`, or `nano`).

The target uses the `@package/path` format. The leading `@` is optional.

```bash
kt edit @kt-defaults/creatures/swe
kt edit kt-defaults/terrariums/novel
```

The command locates the config file (`config.yaml`, `config.yml`, `terrarium.yaml`, or `terrarium.yml`) inside the resolved path and opens it.

## Extension Management

### `kt extension list` - List Extension Modules

```bash
kt extension list
```

Shows all installed extension modules (tools, plugins, LLM presets) from installed packages. For each package with extensions, displays the package name, version, and a summary of available module types.

### `kt extension info` - Show Package Extension Details

```bash
kt extension info <name>
```

Display detailed information about a specific package's extension modules, including creatures, terrariums, tools, plugins, and LLM presets. Shows module names, descriptions, and source file paths.

**Arguments:**

| Argument | Required | Description |
|----------|----------|-------------|
| `name` | Yes | Package name (as shown in `kt extension list`) |

**Example:**

```bash
kt extension info kt-defaults
```

## Web and Desktop

### `kt web` - Serve Web UI + API

```bash
kt web [options]
```

Start the FastAPI server with the Vue 3 web dashboard. Provides REST and WebSocket endpoints for managing agents and terrariums, plus the frontend UI.

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Bind host |
| `--port` | `8001` | Bind port |
| `--dev` | off | API-only mode (run vite dev server separately for frontend development) |

**Examples:**

```bash
# Start with defaults
kt web

# Custom port
kt web --port 9000

# Dev mode (API only, use vite for frontend)
kt web --dev
```

### `kt app` - Launch Native Desktop UI

```bash
kt app [options]
```

Launch the web dashboard as a native desktop window using pywebview. Starts an internal FastAPI server and opens it in a native window.

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8001` | Internal server port |

**Example:**

```bash
kt app
kt app --port 9000
```

## MCP (Model Context Protocol)

### `kt mcp list` - List MCP Servers

```bash
kt mcp list --agent <agent_path>
```

List all MCP servers configured in an agent's config file.

**Options:**

| Flag | Required | Description |
|------|----------|-------------|
| `--agent` | Yes | Path to agent config folder |

**Example:**

```bash
kt mcp list --agent examples/agent-apps/swe_agent
```

## Utility

### `kt info` - Show Agent Info

```bash
kt info <agent_path>
```

Display agent configuration: name, description, model, tools, sub-agents, and files in the config folder.

```bash
kt info examples/agent-apps/swe_agent
```
