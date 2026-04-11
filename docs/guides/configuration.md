# Configuration Reference

Complete reference for agent configuration (`config.yaml`).

## Agent Configuration

KohakuTerrarium supports YAML, JSON, and TOML configuration formats. YAML is recommended.

### Environment Variable Interpolation

Use `${VAR:default}` syntax for environment variables:

```yaml
controller:
  model: "${OPENROUTER_MODEL:google/gemini-3-flash-preview}"  # Uses env var or default
  api_key_env: OPENROUTER_API_KEY                             # Reads from this env var
```

### Top-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Agent identifier |
| `version` | string | No | Version string |
| `session_key` | string | No | Session key for shared state (default: agent name). Agents with the same key share channels, scratchpad, and TUI state |
| `controller` | object | Yes | LLM configuration |
| `system_prompt_file` | string | No | Path to system prompt markdown |
| `input` | object | No | Input module configuration |
| `output` | object | No | Output module configuration |
| `tools` | list | No | Tool configurations |
| `subagents` | list | No | Sub-agent configurations |
| `triggers` | list | No | Trigger configurations |
| `memory` | object | No | Memory system configuration |
| `compact` | object | No | Context compaction settings |
| `termination` | object | No | Stop conditions (max turns, duration, keywords) |
| `startup_trigger` | object | No | Event fired on agent start |
| `base_config` | string | No | Path to parent creature config for inheritance |
| `max_subagent_depth` | int | No | Sub-agent nesting depth limit (default: 3) |

### Controller Configuration

```yaml
# Using an LLM profile (recommended)
controller:
  llm: claude-sonnet-4.6     # Profile name from presets or ~/.kohakuterrarium/llm_profiles.yaml
  tool_format: native

# Codex OAuth (uses ChatGPT subscription)
controller:
  model: gpt-5.4
  auth_mode: codex-oauth
  tool_format: native

# OpenRouter / OpenAI-compatible (inline config, backward compat)
controller:
  model: "google/gemini-3-flash-preview"
  temperature: 0.7
  max_tokens: 4096
  api_key_env: OPENROUTER_API_KEY
  base_url: https://openrouter.ai/api/v1
  max_messages: 100
  max_context_chars: 100000
  ephemeral: false
  include_tools_in_prompt: true
  include_hints_in_prompt: true
  skill_mode: "dynamic"
  tool_format: bracket       # "bracket", "xml", "native", or custom dict
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `llm` | string | "" | LLM profile name (e.g., `gpt-5.4`, `claude-sonnet-4.6`). Overridable via `--llm` CLI flag |
| `model` | string | Required* | Model identifier (*not needed when `llm` is set) |
| `auth_mode` | string | None | Authentication mode: `codex-oauth` for ChatGPT subscription |
| `temperature` | float | 0.7 | Sampling temperature |
| `max_tokens` | int | None | Max tokens to generate (None = let the API decide) |
| `reasoning_effort` | string | "medium" | Reasoning effort: none, minimal, low, medium, high, xhigh |
| `service_tier` | string | None | API service tier (None, priority, flex) |
| `extra_body` | dict | {} | Extra fields merged into the API request body |
| `api_key_env` | string | Required* | Env var containing API key (*not needed with codex-oauth or `llm` profile) |
| `base_url` | string | OpenAI URL | API endpoint |
| `max_messages` | int | 0 (unlimited) | Max conversation messages |
| `max_context_chars` | int | 0 (unlimited) | Max context characters |
| `ephemeral` | bool | false | Clear conversation after each turn |
| `include_tools_in_prompt` | bool | true | Include tool list |
| `include_hints_in_prompt` | bool | true | Include framework hints |
| `skill_mode` | string | "dynamic" | "dynamic" (use info command) or "static" (all docs in prompt) |
| `tool_format` | string or dict | "bracket" | Tool call format. See [Tool Formats](../concepts/tool-formats.md) |

#### LLM Profiles

The `llm` field references a named profile that bundles all LLM connection settings (provider, model, base URL, API key, context limits). This is the recommended way to configure the LLM.

Resolution order: `--llm` CLI flag > `controller.llm` in config > `default_model` in `~/.kohakuterrarium/llm_profiles.yaml` > inline controller fields (backward compat).

```yaml
# Profile resolves all connection details automatically
controller:
  llm: gpt-5.4
  temperature: 0.5           # Inline fields still override profile values
```

Built-in presets include models from OpenAI, Anthropic, Google, Qwen, Kimi, MiniMax, and more. Custom profiles can be defined in `~/.kohakuterrarium/llm_profiles.yaml`. The inline config fields (`model`, `auth_mode`, `api_key_env`, `base_url`) still work as fallback when no profile is set.

### Input Configuration

```yaml
# CLI input (builtin)
input:
  type: cli
  prompt: "> "

# TUI input (builtin)
input:
  type: tui
  prompt: "You: "
  session_key: my_agent     # Optional: override session key

# None input (trigger-only agents)
input:
  type: none

# Custom input
input:
  type: custom
  module: ./custom/my_input.py
  class: MyInputModule
  my_option: value          # Additional fields passed to constructor
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | "cli", "tui", "none", or "custom" |
| `module` | string | For custom | Path to module |
| `class` | string | For custom | Class name |
| `prompt` | string | For CLI/TUI | Input prompt string |
| `session_key` | string | For TUI | Override session key for TUI session |

### Output Configuration

```yaml
# Basic output
output:
  type: stdout
  controller_direct: true

# TUI output
output:
  type: tui
  controller_direct: true
  session_key: my_agent

# With named outputs
output:
  type: stdout
  named_outputs:
    discord:
      type: custom
      module: ./custom/discord_output.py
      class: DiscordOutput
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | "stdout", "tui", or "custom" |
| `controller_direct` | bool | No | Controller output to default |
| `named_outputs` | object | No | Named output targets |

### Tools Configuration

```yaml
tools:
  # Builtin tools
  - name: bash
    type: builtin
  - name: read
    type: builtin

  # Custom tools
  - name: my_tool
    type: custom
    module: ./custom/my_tool.py
    class: MyTool
    timeout: 30
```

**Available built-in tools (30 total):**

**General tools (21):**

| Name | Description | Name | Description |
|------|-------------|------|-------------|
| `bash` | Execute shell commands | `think` | Extended reasoning step |
| `python` | Execute Python code | `scratchpad` | Session key-value memory |
| `read` | Read file contents | `send_message` | Send to named channel |
| `write` | Create/overwrite files | `web_fetch` | Fetch and read web pages |
| `edit` | Search-replace in files | `web_search` | Search the web (DuckDuckGo) |
| `glob` | Find files by pattern | `ask_user` | Prompt user for input |
| `grep` | Regex search in files | `json_read` | Query JSON files |
| `tree` | Directory structure | `json_write` | Modify JSON files |
| `info` | Load tool/sub-agent docs | `list_triggers` | Show active triggers |
| `search_memory` | Search session history | `create_trigger` | Create trigger at runtime |
| `stop_task` | Cancel a running background task | | |

**Terrarium management tools (9):** Used by the `root` creature for managing terrariums.

| Name | Description |
|------|-------------|
| `terrarium_create` | Create and start a terrarium |
| `terrarium_status` | Get terrarium status |
| `terrarium_stop` | Stop a running terrarium |
| `terrarium_send` | Send a message to a terrarium channel |
| `terrarium_observe` | Observe terrarium channel traffic |
| `terrarium_history` | Get channel message history |
| `creature_start` | Start a creature in a terrarium |
| `creature_stop` | Stop a creature in a terrarium |
| `creature_interrupt` | Interrupt a creature's current LLM turn |

### Sub-Agents Configuration

```yaml
subagents:
  # Builtin
  - name: explore
    type: builtin

  # Custom
  - name: output
    type: custom
    description: Generate responses
    prompt_file: prompts/output.md
    tools: []
    can_modify: false
    max_turns: 5
    timeout: 60
    interactive: false
    output_to: controller
```

**Available built-in sub-agents:**

| Name | Description | Tools |
|------|-------------|-------|
| `explore` | Search and analyze codebase | glob, grep, read |
| `plan` | Create implementation plans | glob, grep, read |
| `worker` | Implement changes | read, write, edit, bash, glob, grep |
| `critic` | Review and critique | read, glob, grep |
| `summarize` | Condense content | (none) |
| `research` | Web + file research | http, read, glob, grep |
| `coordinator` | Multi-agent via channels | send_message, wait_channel |
| `memory_read` | Retrieve from memory | read, glob |
| `memory_write` | Store to memory | write, read |
| `response` | Generate user responses | (none) |

**Sub-agent fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | Required | Sub-agent identifier |
| `type` | string | Required | "builtin" or "custom" |
| `description` | string | "" | One-line description |
| `tools` | list | [] | Allowed tool names |
| `system_prompt` | string | "" | Inline system prompt |
| `prompt_file` | string | None | Path to prompt file |
| `can_modify` | bool | false | Allow write/edit tools |
| `stateless` | bool | true | No persistent state |
| `interactive` | bool | false | Long-lived with context updates |
| `context_mode` | string | "interrupt_restart" | How to handle updates |
| `output_to` | string | "controller" | "controller" or "external" |
| `output_module` | string | None | Output module name |
| `return_as_context` | bool | false | Return output to parent |
| `max_turns` | int | 10 | Max conversation turns |
| `timeout` | float | 300.0 | Max execution time |
| `model` | string | None | Override LLM model |
| `temperature` | float | None | Override temperature |
| `memory_path` | string | None | Memory folder path |

**Context update modes:** `interrupt_restart` (stop, start new), `queue_append` (queue, process after), `flush_replace` (flush, replace immediately).

### Triggers Configuration

```yaml
triggers:
  - type: custom
    module: ./custom/idle_trigger.py
    class: IdleTrigger
    prompt: "The chat has been quiet."
    min_idle_seconds: 300
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | "custom" (builtins coming) |
| `module` | string | Yes | Path to module |
| `class` | string | Yes | Class name |
| `prompt` | string | No | Default prompt for events |

### Memory Configuration

```yaml
memory:
  path: ./memory
  init_files:
    - character.md       # Read-only
    - rules.md
  writable_files:
    - context.md         # Agent can modify
    - facts.md
```

### Compact (Context Management)

Non-blocking background context compaction. When the prompt token count reaches `threshold`, a background task summarizes older messages to free context space. The agent continues working during compaction.

```yaml
compact:
  max_tokens: 256000       # Max context token budget (match your model's window)
  threshold: 0.80          # Trigger compaction at this fraction of max_tokens
  target: 0.40             # After compaction, reduce to this fraction of max_tokens
  keep_recent: 8           # Always keep last N user/assistant turns uncompacted
  compact_model: null      # Optional: use a different (cheaper) model for summarization
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_tokens` | int | 256000 | Context window budget in tokens |
| `threshold` | float | 0.80 | Fraction of max_tokens that triggers compaction |
| `target` | float | 0.40 | Target fraction of max_tokens after compaction |
| `keep_recent` | int | 8 | Number of recent turns to keep verbatim (live zone) |
| `compact_model` | string | null | Override model for the summarization call |

The compaction process: messages are split into a "compact zone" (older, will be summarized) and a "live zone" (recent, untouched). The LLM produces a structured summary of the compact zone, which atomically replaces it. If summarization fails, emergency truncation is used as a fallback.

### Termination (Stop Conditions)

Configurable conditions that stop the agent loop. All conditions are optional; if multiple are set, ANY triggered condition stops the agent.

```yaml
termination:
  max_turns: 50            # Stop after N controller turns
  max_tokens: 100000       # Stop after N total tokens used (reserved)
  max_duration: 3600       # Stop after N seconds
  idle_timeout: 300        # Stop after N seconds of inactivity
  keywords: ["##done##"]   # Stop when controller outputs any keyword
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_turns` | int | 0 (unlimited) | Max controller turns before stopping |
| `max_tokens` | int | 0 (unlimited) | Total token budget (reserved for future use) |
| `max_duration` | float | 0 (unlimited) | Max wall-clock duration in seconds |
| `idle_timeout` | float | 0 (unlimited) | Stop after N seconds with no events |
| `keywords` | list[str] | [] | Stop when controller output contains any keyword |

### Startup Trigger

```yaml
startup_trigger:
  prompt: "Agent starting. Initialize your state."
```

### Memory Embedding

Configures the embedding provider for semantic search via the `search_memory` tool. Without this, only FTS keyword search is available.

```yaml
memory:
  path: ./memory
  embedding:
    provider: auto              # auto | model2vec | sentence-transformer | api | none
    model: <string>             # Provider-specific model name
    dimensions: <int>           # Optional dimension truncation (Matryoshka)
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | string | "auto" | Embedding provider. `auto` tries model2vec, then sentence-transformer, then none |
| `model` | string | varies | Model name. Defaults: `minishlab/potion-base-8M` (model2vec), `jinaai/jina-embeddings-v5-text-nano` (sentence-transformer), `text-embedding-3-small` (api) |
| `dimensions` | int | null | Optional dimension truncation for Matryoshka-capable models |
| `device` | string | "cpu" | Device for sentence-transformer: `cpu` or `cuda` |
| `api_key_env` | string | "OPENAI_API_KEY" | Env var for API provider key |
| `base_url` | string | OpenAI URL | API endpoint for API provider |

Provider tiers (by weight and speed):
- **model2vec**: ~8 MB, numpy-only, microsecond inference (lightest)
- **sentence-transformer**: Gemma, Jina, bge, any HuggingFace model (better quality)
- **api**: OpenAI, Google, Jina via HTTP (no local model needed)
- **none**: Disables semantic search, FTS keyword search only

### Agent Folder Structure

```
examples/agent-apps/my_agent/
+-- config.yaml              # Main configuration
+-- prompts/
|   +-- system.md            # System prompt
|   +-- output.md            # Output sub-agent prompt
|   +-- tools/               # Tool documentation overrides
|       +-- bash.md
+-- memory/
|   +-- character.md
|   +-- context.md
+-- custom/
    +-- my_input.py
    +-- my_tool.py
    +-- my_trigger.py
```

---

For terrarium configuration, see [Terrariums](terrariums.md).
