# Configuration

Every configuration field for creatures, terrariums, LLM profiles,
MCP servers, and package manifests. File formats: YAML (preferred),
JSON, TOML. All files support `${VAR}` / `${VAR:default}` env-var
interpolation, applied at load time.

For the model of how creatures and terrariums relate, see
[concepts/boundaries](../concepts/boundaries.md). For hands-on
examples, see [guides/configuration](../guides/configuration.md) and
[guides/creatures](../guides/creatures.md).

## Path resolution

Config fields referring to other files or packages resolve in this
order:

1. `@<pkg>/<path-inside-pkg>` → `~/.kohakuterrarium/packages/<pkg>/<path-inside-pkg>`
   (following `<pkg>.link` for editable installs).
2. `creatures/<name>` or similar project-relative forms → walk up from
   the current agent folder to the project root.
3. Otherwise relative to the agent folder (falling back to the
   base-config folder when inherited).

---

## Creature config (`config.yaml`)

Loaded by `kohakuterrarium.core.config.load_agent_config`. File lookup
order: `config.yaml` → `config.yml` → `config.json` → `config.toml`.

### Top-level fields

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `name` | str | — | yes | Creature name. Default session key if `session_key` unset. |
| `version` | str | `"1.0"` | no | Informational. |
| `base_config` | str | `null` | no | Parent config to inherit from (`@package/path`, `creatures/<name>`, or relative). |
| `controller` | dict | `{}` | no | LLM/controller block. See [Controller](#controller-block). |
| `system_prompt` | str | `"You are a helpful assistant."` | no | Inline system prompt. |
| `system_prompt_file` | str | `null` | no | Path to a markdown prompt file; relative to the agent folder. Concatenated through the inheritance chain. |
| `prompt_context_files` | dict[str,str] | `{}` | no | Jinja variable → file path; files are read and injected when the prompt is rendered. |
| `skill_mode` | str | `"dynamic"` | no | `dynamic` (on-demand via the `info` framework command) or `static` (full docs up-front). |
| `include_tools_in_prompt` | bool | `true` | no | Include auto-generated tool list. |
| `include_hints_in_prompt` | bool | `true` | no | Include framework hints (tool-call syntax and `info` / `read_job` / `jobs` / `wait` command examples). |
| `max_messages` | int | `0` | no | Conversation cap. `0` = unlimited. |
| `ephemeral` | bool | `false` | no | Clear conversation after each turn (group-chat mode). |
| `session_key` | str | `null` | no | Override default session key (which is `name`). |
| `input` | dict | `{}` | no | Input module config. See [Input](#input). |
| `output` | dict | `{}` | no | Output module config. See [Output](#output). |
| `tools` | list | `[]` | no | Tool entries. See [Tools](#tools). |
| `subagents` | list | `[]` | no | Sub-agent entries. See [Sub-agents](#sub-agents). |
| `triggers` | list | `[]` | no | Trigger entries. See [Triggers](#triggers). |
| `compact` | dict | `null` | no | Compaction config. See [Compact](#compact). |
| `startup_trigger` | dict | `null` | no | One-shot trigger fired on start. `{prompt: "..."}`. |
| `termination` | dict | `null` | no | Termination conditions. See [Termination](#termination). |
| `max_subagent_depth` | int | `3` | no | Max nested sub-agent depth. `0` = unlimited. |
| `tool_format` | str \| dict | `"bracket"` | no | `bracket`, `xml`, `native`, or a custom dict format. |
| `mcp_servers` | list | `[]` | no | Per-agent MCP servers. See [MCP servers](#mcp-servers-in-agent-config). |
| `plugins` | list | `[]` | no | Lifecycle plugins. See [Plugins](#plugins). |
| `no_inherit` | list[str] | `[]` | no | Keys that replace (not merge) base values. E.g. `[tools, subagents]`. |
| `memory` | dict | `{}` | no | `memory.embedding.{provider,model}`. See [Memory](#memory). |

### Controller block

All fields may also be set at the top level for backward compatibility.

| Field | Type | Default | Description |
|---|---|---|---|
| `llm` | str | `""` | Profile reference in `~/.kohakuterrarium/llm_profiles.yaml` (e.g. `gpt-5.4`, `claude-opus-4.6`). |
| `model` | str | `""` | Inline model id if `llm` unset. |
| `auth_mode` | str | `""` | Blank (auto), `codex-oauth`, etc. |
| `api_key_env` | str | `""` | Env var holding the key. |
| `base_url` | str | `""` | Override endpoint URL. |
| `temperature` | float | `0.7` | Sampling temperature. |
| `max_tokens` | int \| null | `null` | Output cap. |
| `reasoning_effort` | str | `"medium"` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`. |
| `service_tier` | str | `null` | `priority`, `flex`. |
| `extra_body` | dict | `{}` | Merged verbatim into the request JSON. |
| `skill_mode`, `include_tools_in_prompt`, `include_hints_in_prompt`, `max_messages`, `ephemeral`, `tool_format` | | | Mirror top-level fields. |

Resolution order per turn:

1. `--llm` CLI flag.
2. `controller.llm`.
3. `controller.model` (+ optional `base_url` / `api_key_env`).
4. `default_model` from `llm_profiles.yaml`.

### Input

Dict fields: `{type, module?, class_name?, options?, ...type-specific keys}`.

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | str | `"cli"` | `cli`, `tui`, `asr`, `whisper`, `none`, `custom`, `package`. |
| `module` | str | — | For `custom` (e.g. `./custom/input.py`) or `package` (e.g. `pkg.mod`). |
| `class_name` | str | — | Class to instantiate. |
| `options` | dict | `{}` | Module-specific options. |
| `prompt` | str | `"> "` | CLI prompt (cli input). |
| `exit_commands` | list[str] | `[]` | Strings that trigger exit. |

### Output

Supports a default output plus optional `named_outputs` for side
channels (e.g. a Discord webhook).

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | str | `"stdout"` | `stdout`, `tts`, `tui`, `custom`, `package`. |
| `module` | str | — | For `custom`/`package` output modules. |
| `class_name` | str | — | Class to instantiate. |
| `options` | dict | `{}` | Module-specific options. |
| `controller_direct` | bool | `true` | Route controller text through the default output. |
| `named_outputs` | dict[str, OutputConfigItem] | `{}` | Named side outputs. Each item has the same shape as the default. |

### Tools

List of tool entries. Each entry is a dict or a shorthand string
(builtin by that name).

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Tool name (required). |
| `type` | str | `"builtin"` | `builtin`, `custom`, `package`. |
| `module` | str | — | For `custom` (e.g. `./custom/tools/my_tool.py`) or `package`. |
| `class_name` | str | — | Class to instantiate. |
| `doc` | str | — | Override for the skill documentation file. |
| `options` | dict | `{}` | Tool-specific options. |

Shorthand:

```yaml
tools:
  - bash
  - read
  - write
```

### Sub-agents

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Sub-agent identifier. |
| `type` | str | `"builtin"` | `builtin`, `custom`, `package`. |
| `module` | str | — | For `custom`/`package`. |
| `config_name` | str | — | Named config object inside the module (e.g. `MY_AGENT_CONFIG`). |
| `description` | str | — | Description used in the parent's prompt. |
| `tools` | list[str] | `[]` | Tools this sub-agent is allowed to use. |
| `can_modify` | bool | `false` | Whether the sub-agent can perform mutating operations. |
| `interactive` | bool | `false` | Stay alive across turns; receive context updates. |
| `options` | dict | `{}` | Sub-agent-specific options. |

### Triggers

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | str | — | `timer`, `idle`, `webhook`, `channel`, `custom`, `package`. |
| `module` | str | — | For `custom`/`package`. |
| `class_name` | str | — | Class to instantiate. |
| `prompt` | str | — | Default prompt injection when the trigger fires. |
| `options` | dict | `{}` | Trigger-specific options. |

Common per-type options:

- `timer`: `interval` (seconds).
- `idle`: `timeout` (seconds).
- `channel`: channel name + filter.
- `webhook`: endpoint settings.

### Compact

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Turn compaction on. |
| `max_tokens` | int | profile-default | Target token ceiling. |
| `threshold` | float | `0.8` | Fraction of `max_tokens` at which compaction starts. |
| `target` | float | `0.5` | Target fraction after compaction. |
| `keep_recent_turns` | int | `5` | Turns preserved verbatim. |
| `compact_model` | str | controller's model | Override LLM used for summarisation. |

### Termination

Any non-zero threshold is enforced. Keyword match stops the agent
when the output contains the keyword.

| Field | Type | Default | Description |
|---|---|---|---|
| `max_turns` | int | `0` | |
| `max_tokens` | int | `0` | |
| `max_duration` | float | `0` | Seconds. |
| `idle_timeout` | float | `0` | Seconds with no events. |
| `keywords` | list[str] | `[]` | Case-sensitive substring match. |

### MCP servers in agent config

Per-agent MCP servers. Connected on agent start.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Server identifier. |
| `transport` | `stdio` \| `http` | — | Transport. |
| `command` | str | — | stdio executable. |
| `args` | list[str] | `[]` | stdio args. |
| `env` | dict[str,str] | `{}` | stdio env. |
| `url` | str | — | HTTP/SSE endpoint. |

### Plugins

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Plugin identifier. |
| `type` | str | `"builtin"` | `builtin`, `custom`, `package`. |
| `module` | str | — | For `custom` (e.g. `./custom/plugins/my.py`) or `package`. |
| `class` or `class_name` | str | — | Class to instantiate. |
| `description` | str | — | Free-form metadata. |
| `options` | dict | `{}` | Plugin-specific options. |

Shorthand: a bare string is treated as a package-resolved plugin name.

### Memory

```yaml
memory:
  embedding:
    provider: model2vec       # or sentence-transformer, api
    model: "@best"            # preset alias or HuggingFace path
```

Provider options:

- `model2vec` (default, no torch dependency).
- `sentence-transformer` (torch-based, higher quality).

Preset aliases: `@tiny`, `@base`, `@retrieval`, `@best`,
`@multilingual`, `@multilingual-best`, `@science`, `@nomic`, `@gemma`.

### Inheritance rules

- `base_config` resolves via the path rules above.
- **Scalars**: child overrides.
- **Lists** (`tools`, `subagents`): extend with dedup by `name` unless
  the key is listed in `no_inherit`.
- **Dicts** (`controller`, `input`, `output`): shallow merge.
- `system_prompt_file`: concatenated along the chain; inline
  `system_prompt` is appended last.

### File convention

```
creatures/<name>/
  config.yaml           # required
  prompts/system.md     # if referenced
  tools/                # custom tool modules
  memory/               # context files
  subagents/            # custom sub-agent configs
```

---

## Terrarium config (`terrarium.yaml`)

Loaded by `kohakuterrarium.terrarium.config.load_terrarium_config`.

```yaml
terrarium:
  name: str
  root:                  # optional — outside-terrarium root agent
    base_config: str     # or any AgentConfig field inline
    ...
  creatures:
    - name: str
      base_config: str   # legacy alias: `config:`
      channels:
        listen: [str]
        can_send: [str]
      output_log: bool         # default false
      output_log_size: int     # default 100
      ...                      # any AgentConfig override
  channels:
    <name>:
      type: queue | broadcast  # default queue
      description: str
    # or shorthand — string = description:
    # <name>: "description"
```

Terrarium field summary:

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Terrarium name. |
| `root` | object | `null` | Optional root-agent config. Forced to receive terrarium management tools. |
| `creatures` | list | `[]` | Creatures that run inside the terrarium. |
| `channels` | dict | `{}` | Shared channel declarations. |

Creature entry fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Creature name. |
| `base_config` (or `config`) | str | — | Config path (agent config). |
| `channels.listen` | list[str] | `[]` | Channels the creature consumes. |
| `channels.can_send` | list[str] | `[]` | Channels the creature can publish to. |
| `output_log` | bool | `false` | Capture stdout per creature. |
| `output_log_size` | int | `100` | Max lines per creature's log buffer. |

Channel entry fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | `queue` \| `broadcast` | `queue` | Delivery semantics. |
| `description` | str | `""` | Documented in the channel topology prompt. |

Auto-created channels:

- One `queue` per creature, named after the creature (direct message).
- `report_to_root` queue when `root` is set.

Root agent:

- Gets the `TerrariumToolManager` with `terrarium_*` and `creature_*`
  tools.
- Auto-listens to every creature channel; receives `report_to_root`.
- Inheritance / merge rules are the same as for creatures.

---

## LLM profiles (`~/.kohakuterrarium/llm_profiles.yaml`)

```yaml
version: 3
default_model: <preset name>

backends:
  <provider-name>:
    backend_type: openai | codex | anthropic
    base_url: str
    api_key_env: str

presets:
  <preset-name>:
    provider: <backend-name>   # reference to backends or built-in
    model: str                 # model id
    max_context: int           # default 256000
    max_output: int            # default 65536
    temperature: float         # optional
    reasoning_effort: str      # none | minimal | low | medium | high | xhigh
    service_tier: str          # priority | flex
    extra_body: dict
```

Built-in provider names (not overridable): `codex`, `openai`,
`openrouter`, `anthropic`, `gemini`, `mimo`.

See [builtins.md — LLM presets](builtins.md#llm-presets) for every
shipped preset.

---

## MCP server catalog (`~/.kohakuterrarium/mcp_servers.yaml`)

Global MCP registry, an alternative to per-agent `mcp_servers:`.

```yaml
- name: sqlite
  transport: stdio
  command: mcp-server-sqlite
  args: ["/path/to/db"]
  env: {}
- name: web_api
  transport: http
  url: https://mcp.example.com/sse
  env: { API_KEY: ${MCP_API_KEY} }
```

Fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Unique identifier. |
| `transport` | `stdio` \| `http` | — | Transport. |
| `command` | str | — | stdio executable. |
| `args` | list[str] | `[]` | stdio args. |
| `env` | dict[str,str] | `{}` | stdio env. |
| `url` | str | — | HTTP endpoint for `http` transport. |

---

## Package manifest (`kohaku.yaml`)

```yaml
name: my-package
version: "1.0.0"
description: "..."
creatures:
  - name: researcher
terrariums:
  - name: research_team
tools:
  - name: my_tool
    module: my_package.tools
    class: MyTool
plugins:
  - name: my_plugin
    module: my_package.plugins
    class: MyPlugin
llm_presets:
  - name: my_preset
python_dependencies:
  - requests>=2.28.0
```

| Field | Type | Description |
|---|---|---|
| `name` | str | Package name; installed as `~/.kohakuterrarium/packages/<name>/`. |
| `version` | str | Semver. |
| `description` | str | Free-form. |
| `creatures` | list | `[{name}]` — creature configs under `creatures/<name>/`. |
| `terrariums` | list | `[{name}]` — terrarium configs under `terrariums/<name>/`. |
| `tools` | list | `[{name, module, class}]` — contributed tool classes. |
| `plugins` | list | `[{name, module, class}]` — contributed plugins. |
| `llm_presets` | list | `[{name}]` — contributed LLM presets (values live in the package). |
| `python_dependencies` | list[str] | Pip requirement strings. |

Install modes:

- `kt install <git_url>` — clone.
- `kt install <path>` — copy.
- `kt install <path> -e` — write `<name>.link` pointer to the source.

---

## API-key storage (`~/.kohakuterrarium/api_keys.yaml`)

Managed by `kt login` and `kt config key set`. Format:

```yaml
openai: sk-...
openrouter: sk-or-...
anthropic: sk-ant-...
```

Resolution order: stored file → env var (`api_key_env`) → empty.

---

## See also

- Concepts: [boundaries](../concepts/boundaries.md),
  [composing an agent](../concepts/foundations/composing-an-agent.md),
  [multi-agent overview](../concepts/multi-agent/README.md).
- Guides: [configuration](../guides/configuration.md),
  [creatures](../guides/creatures.md),
  [terrariums](../guides/terrariums.md).
- Reference: [cli](cli.md), [builtins](builtins.md),
  [python](python.md).
