---
title: Configuration
summary: Every configuration field for creatures, terrariums, LLM profiles, MCP servers, compaction, plugins, and output wiring.
tags:
  - reference
  - config
---

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
| `tool_format` | str \| dict | `"bracket"` | no | `bracket`, `xml`, `native`, or a custom dict format. `native` requires the configured LLM provider to support structured tool calling. |
| `mcp_servers` | list | `[]` | no | Per-agent MCP servers. See [MCP servers](#mcp-servers-in-agent-config). |
| `plugins` | list | `[]` | no | Lifecycle plugins. See [Plugins](#plugins). |
| `no_inherit` | list[str] | `[]` | no | Keys that replace (not merge) base values. E.g. `[tools, subagents]`. |
| `memory` | dict | `{}` | no | `memory.embedding.{provider,model}`. See [Memory](#memory). |
| `output_wiring` | list | `[]` | no | Per-creature automatic round-output routing. See [Output wiring](#output-wiring). |
| `skills` | list[str] | `[]` | no | Package-skill opt-in list. Package skills default disabled unless named here; `"*"` enables all discovered package skills. |
| `skill_index_budget_bytes` | int | `4096` | no | Byte budget for the auto-invoke procedural-skill index in the system prompt. |
| `framework_hint_overrides` | dict[str,str] | `{}` | no | Creature-level override map for built-in framework-hint prose blocks. |
| `disable_provider_tools` | list[str] | `[]` | no | Opt out of provider-native tools auto-injected by the active backend. |
| `max_iterations` | int \| null | `null` | no | Shared iteration budget for the parent controller and inheriting sub-agents. |
| `sanitize_orphan_tool_calls` | bool | `true` | no | Drop orphan tool-call/tool-result fragments before sending history to the provider. |

### Controller block

All fields may also be set at the top level for backward compatibility.

| Field | Type | Default | Description |
|---|---|---|---|
| `llm` | str | `""` | Profile reference in `~/.kohakuterrarium/llm_profiles.yaml` (e.g. `gpt-5.4`, `claude-opus-4.7`). May carry an inline variation selector, e.g. `claude-opus-4.7@reasoning=xhigh`. |
| `model` | str | `""` | Inline model id if `llm` unset. Also accepts a `name@group=option` selector. |
| `provider` | str | `""` | Disambiguator when `model` is set and the same model id is bound to multiple backends (e.g. `openai` vs `openrouter`). |
| `variation_selections` | dict[str,str] | `{}` | Per-group variation overrides — `{group_name: option_name}`. See [Variation selector](#variation-selector). |
| `variation` | str | `""` | Shorthand for a single-option selection; resolved against the preset's groups. |
| `auth_mode` | str | `""` | Blank (auto), `codex-oauth`, etc. |
| `api_key_env` | str | `""` | Env var holding the key. |
| `base_url` | str | `""` | Override endpoint URL. |
| `temperature` | float | `0.7` | Sampling temperature. |
| `max_tokens` | int \| null | `null` | Maps onto the resolved profile's `max_output` (per-response output cap), not `max_context` (total window). |
| `reasoning_effort` | str | `"medium"` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`. Consumed directly by Codex; for other providers use `extra_body` (see [Provider-specific `extra_body` notes](#provider-specific-extra_body-notes)). |
| `service_tier` | str | `null` | `priority`, `flex`. |
| `extra_body` | dict | `{}` | Deep-merged onto the resolved preset's `extra_body` (which may already carry variation patches). |
| `skill_mode`, `include_tools_in_prompt`, `include_hints_in_prompt`, `max_messages`, `ephemeral`, `tool_format` | | | Mirror top-level fields. |

Canonical model identifiers are now `provider/name[@group=option,...]`. The runtime stores and surfaces this full identifier (for `/model`, session-info events, and UI display), so a round-trip like `/model openai/gpt-5.4-api@reasoning=high` is stable.

Resolution order per turn (see `llm/profiles.py:resolve_controller_llm`):

1. `--llm` CLI flag wins over the YAML `controller.llm`.
2. Otherwise `controller.llm` (preset name + optional `@group=option` selector).
3. Otherwise `controller.model` — matched against the built-in and user preset registry by model id. `controller.provider` disambiguates cross-backend collisions; a `name@group=option` selector is also parsed out.
4. If neither `llm` nor `model` was set, fall back to `default_model` from `llm_profiles.yaml`.
5. After a profile is resolved, the controller's `temperature`, `reasoning_effort`, `service_tier`, `max_tokens` (remapped to `max_output`), and `extra_body` are layered on top. `extra_body` is deep-merged, every other override is a scalar replace.

### Variation selector

A preset may expose **variation groups** — two-level dicts of `{group_name:
{option_name: patch}}` that let one preset serve multiple knobs (reasoning
effort, speed, thinking level) without duplicating the entry. Selection
happens either inside the preset reference string or via explicit dict fields
on the controller.

Shorthand forms (usable in `--llm`, `controller.llm`, or `controller.model`):

```text
claude-opus-4.7@reasoning=xhigh                 # one group = option
claude-opus-4.7@reasoning=xhigh,speed=fast      # multiple groups, comma-separated
claude-opus-4.7@xhigh                           # bare option; auto-resolves
                                                # to the single matching group
                                                # (fails if ambiguous)
```

Explicit form (preferred when the selector is assembled in config):

```yaml
controller:
  llm: claude-opus-4.7
  variation_selections:
    reasoning: xhigh
  # or, single-option shorthand:
  variation: xhigh
```

Rules:

- The bare-shorthand form (`@xhigh`) is rejected when more than one group
  would match the option — disambiguate with `@group=option`.
- Unknown groups or options raise at resolve time.
- Variation patches may write to only these roots: `temperature`,
  `reasoning_effort`, `service_tier`, `max_context`, `max_output`,
  `extra_body`. Anything else is rejected.
- Cross-group collisions on the same dotted path raise — two selections
  cannot both claim `extra_body.reasoning.effort`.

See [builtins.md — Variation groups](builtins.md#variation-groups) for the
per-preset catalogue of groups and options.

### Provider-specific `extra_body` notes

`extra_body` is deep-merged into the JSON request body. Each provider reads
reasoning/effort knobs from a different path — set the knob the provider
actually honours:

| Provider | Canonical path | Notes |
|---|---|---|
| Codex (ChatGPT-OAuth) | top-level `reasoning_effort`, `service_tier` | `reasoning_effort`: `none\|low\|medium\|high\|xhigh`. Fast mode: use the `speed=fast` variation on `gpt-5.4` — it maps to `service_tier: priority`. Setting `service_tier: fast` literally is rejected by the OpenAI API. |
| OpenAI direct (`-api` presets) | `extra_body.reasoning.effort` | Full scale `none\|low\|medium\|high\|xhigh`. |
| OpenRouter (`-or` presets) | `extra_body.reasoning.effort` | Unified scale `minimal\|low\|medium\|high`; `xhigh` only honoured by a handful of models (Opus 4.7, GPT-5.x). |
| Anthropic direct | `extra_body.output_config.effort` | Compat endpoint silently drops top-level `reasoning_effort` / `service_tier`. Opus 4.7: `low\|medium\|high\|xhigh\|max`; Opus 4.6 / Sonnet 4.6: `low\|medium\|high\|max`. Haiku 4.5 uses the older `thinking.budget_tokens`. |
| Gemini direct | `extra_body.google.thinking_config.thinking_level` | `LOW\|MEDIUM\|HIGH` (Pro) or `MINIMAL\|LOW\|MEDIUM\|HIGH` (Flash / Flash-Lite). |

Anthropic-via-OpenRouter (`claude-*-or`) presets ship with
`extra_body.cache_control: {type: ephemeral}` pre-set; your inline
`controller.extra_body` is deep-merged over it and can disable or replace
it.

Anthropic-compatible endpoints also get automatic prompt-caching markers
applied to the system message and the last three non-tool conversation
messages unless you set `extra_body.disable_prompt_caching: true`.

### Input

Dict fields: `{type, module?, class?, options?, ...type-specific keys}`.

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | str | `"cli"` | `cli`, `cli_nonblocking`, `tui`, `none`, `custom`, `package`. Audio/ASR inputs are custom/package modules. |
| `module` | str | — | For `custom` (e.g. `./custom/input.py`) or `package` (e.g. `pkg.mod`). |
| `class` | str | — | Class to instantiate. YAML key is `class`; the loader stores it on the `class_name` dataclass attribute. |
| `options` | dict | `{}` | Module-specific options. |
| `prompt` | str | `"> "` | CLI prompt (plain `cli` input only — ignored by the Rich CLI and TUI). |
| `exit_commands` | list[str] | `[]` | Strings that trigger exit. |

### Output

Supports a default output plus optional `named_outputs` for side
channels (e.g. a Discord webhook).

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | str | `"stdout"` | `stdout`, `stdout_prefixed`, `console_tts`, `dummy_tts`, `tui`, `custom`, `package`. |
| `module` | str | — | For `custom`/`package` output modules. |
| `class` | str | — | Class to instantiate. YAML key is `class`; the loader stores it on the `class_name` dataclass attribute. |
| `options` | dict | `{}` | Module-specific options. |
| `controller_direct` | bool | `true` | Route controller text through the default output. |
| `named_outputs` | dict[str, OutputConfigItem] | `{}` | Named side outputs. Each item has the same shape as the default. |

### Tools

List of tool entries. Each entry is a dict or a shorthand string
(builtin by that name).

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Tool name (required). For `type: trigger`, must match the trigger's `setup_tool_name`. |
| `type` | str | `"builtin"` | `builtin`, `trigger`, `custom`, `package`. |
| `module` | str | — | For `custom` (e.g. `./custom/tools/my_tool.py`) or `package`. |
| `class` | str | — | Class to instantiate for `custom`/`package`. YAML key is `class`; stored on the `class_name` dataclass attribute. |
| `doc` | str | — | Override for the skill documentation file. |
| `options` | dict | `{}` | Tool-specific options. For builtins, top-level keys such as `timeout`, `max_output`, `working_dir`, `env`, and `notify_controller_on_background_complete` are mapped into `ToolConfig`; remaining keys stay in `config.extra`. |

Tool types:

- `builtin` — resolved against the built-in tool catalog by `name`.
- `trigger` — exposes a universal trigger class as an LLM-callable setup
  tool. `name` must match the trigger's `setup_tool_name`. Shipped
  setup tools: `add_timer` (TimerTrigger), `watch_channel`
  (ChannelTrigger), `add_schedule` (SchedulerTrigger).
- `custom` / `package` — load the class at `module` + `class`.

Provider-native tools are auto-injected from the active backend's
`provider_native_tools` declaration. The creature does not need to list
such a tool under `tools:` unless it wants to override per-tool knobs.
The shipped example is `image_gen` for Codex-backed creatures.

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
| `config` | str | — | Named config object inside the module (e.g. `MY_AGENT_CONFIG`). YAML key is `config`; stored on the `config_name` dataclass attribute. |
| `description` | str | — | Description used in the parent's prompt. |
| `tools` | list[str] | `[]` | Tools this sub-agent is allowed to use. |
| `can_modify` | bool | `false` | Whether the sub-agent can perform mutating operations. |
| `interactive` | bool | `false` | Stay alive across turns; receive context updates. |
| `options` | dict | `{}` | Sub-agent-specific options. Inline sub-agent config fields such as `notify_controller_on_background_complete` are read from here when supported by `SubAgentConfig`. |

Background completion note:

```yaml
tools:
  - name: web_fetch
    type: builtin
    notify_controller_on_background_complete: false

subagents:
  - name: research
    type: builtin
    notify_controller_on_background_complete: false
```

With this flag set to `false`, the background job still emits normal activity/log/output updates, but its completion does not push a fresh event back into the controller loop.

Sub-agent option fields also include shared-budget controls via `options`:

- `budget_inherit: true` (default) — child reuses the parent's shared iteration budget if one exists.
- `budget_allocation: N` — child gets a fresh isolated budget of `N` turns.
- `budget_inherit: false` with no allocation — child runs without the parent's shared budget.

### Triggers

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | str | — | `timer`, `context`, `channel`, `custom`, `package`. |
| `module` | str | — | For `custom`/`package`. |
| `class` | str | — | Class to instantiate. YAML key is `class`; stored on the `class_name` dataclass attribute. |
| `prompt` | str | — | Default prompt injection when the trigger fires. |
| `options` | dict | `{}` | Trigger-specific options. |

Common per-type options:

- `timer`: `interval` (seconds), `immediate` (bool, default `false`).
- `context`: `debounce_ms` (int, default `100`) — debounced context-update trigger.
- `channel`: `channel` (name), `filter_sender` (optional).

For a clock-aligned scheduler, expose `SchedulerTrigger` as an LLM-callable
setup tool via a `tools` entry with `type: trigger, name: add_schedule`
(see [Tools](#tools)) rather than declaring it in the `triggers:` list.

### Compact

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Turn compaction on. |
| `max_tokens` | int | profile-default | Target token ceiling. |
| `threshold` | float | `0.8` | Fraction of `max_tokens` at which compaction starts. |
| `target` | float | `0.4` | Target fraction after compaction. |
| `keep_recent_turns` | int | `8` | Turns preserved verbatim. |
| `compact_model` | str | controller's model | Override LLM used for summarisation. |

### Output wiring

A list of framework-level routing entries. At each turn-end, the
framework constructs a `creature_output` `TriggerEvent` and pushes it
directly into each target creature's event queue — bypassing channels
entirely. See [terrariums guide — output wiring](../guides/terrariums.md#output-wiring)
and [patterns.md — pattern 1b](../concepts/patterns.md) for
discussion; this section is the config reference.

Entry fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `to` | str | — | Target creature name, or the magic string `"root"`. |
| `with_content` | bool | `true` | If `false`, the event carries an empty `content` (metadata-only ping). |
| `prompt` | str \| null | `null` | Template for the receiver's prompt override. When unset, a default template is used depending on `with_content`. |
| `prompt_format` | `simple` \| `jinja` | `"simple"` | `simple` uses `str.format_map`; `jinja` uses the `prompt.template` renderer for conditionals / filters. |

Available template variables (both formats): `source`, `target`,
`content`, `turn_index`, `source_event_type`, `with_content`.

Shorthand — a bare string is sugar for `{to: <str>, with_content: true}`:

```yaml
output_wiring:
  - runner                                   # shorthand
  - { to: root, with_content: false }        # lifecycle ping
  - to: analyzer
    prompt: "[From coder] {content}"         # simple (default)
  - to: critic
    prompt: "{{ source | upper }}: {{ content }}"
    prompt_format: jinja
```

Notes:

- Only meaningful when the creature runs inside a terrarium. Standalone
  creatures with `output_wiring` configured emit nothing (the resolver
  is attached by the terrarium runtime; a standalone agent gets a
  no-op resolver that logs once).
- Unknown / stopped targets are logged and skipped; they never raise
  into the source creature's turn-finalisation.
- The source's `_finalize_processing` runs to completion immediately —
  each target's `_process_event` runs in its own `asyncio.Task` so a
  slow receiver doesn't block the source.

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

Built-in termination checks run first. Plugins may then contribute additional
termination voters programmatically; any positive vote stops the run.

### MCP servers in agent config

Per-agent MCP servers. Connected on agent start. A global catalog at
`~/.kohakuterrarium/mcp_servers.yaml` (managed by `kt config mcp`) uses
the same schema; agents declare the ones they want per-config.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Server identifier. |
| `transport` | `stdio` \| `http` | — | Transport. `http` speaks Server-Sent Events (SSE). |
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
| `class` or `class_name` | str | — | Class to instantiate. Plugins accept both keys (see `bootstrap/plugins.py`); every other module kind uses `class`. |
| `description` | str | — | Free-form metadata. |
| `options` | dict | `{}` | Plugin-specific options. |

Shorthand: a bare string is treated as a package-resolved plugin name.

Plugins may also add controller commands and termination voters at runtime;
those are Python-level extension points, not YAML fields on the creature.

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

`base_config` resolves via the path rules above. Merging follows one
unified rule set for every field:

- **Scalars** — child overrides.
- **Dicts** (`controller`, `input`, `output`, `memory`, `compact`, …) —
  shallow merge; child keys override at the top level.
- **Identity-keyed lists** (`tools`, `subagents`, `plugins`,
  `mcp_servers`, `triggers`) — union by `name`. On name collision
  **child wins** and replaces the base entry in place (preserving base
  order). Items without a `name` value concatenate.
- **Other lists** — child replaces base.
- **Prompt files** — `system_prompt_file` concatenates along the chain;
  inline `system_prompt` is appended last.

Two directives opt out of defaults:

| Directive | Effect |
|-----------|--------|
| `no_inherit: [field, …]` | Drops the inherited value for each listed field. Applies uniformly to scalars, dicts, identity lists, and the prompt chain. |
| `prompt_mode: concat \| replace` | `concat` (default) keeps inherited prompt file chain + inline. `replace` wipes inherited prompts — sugar for `no_inherit: [system_prompt, system_prompt_file]`. |

**Examples.**

Override an inherited tool without replacing the whole list:

```yaml
base_config: "@kt-biome/creatures/swe"
tools:
  - { name: bash, type: custom, module: ./tools/safe_bash.py, class: SafeBash }
```

Start clean: drop inherited tools entirely.

```yaml
base_config: "@kt-biome/creatures/general"
no_inherit: [tools]
tools:
  - { name: think, type: builtin }
```

Replace the prompt entirely for a specialised persona:

```yaml
base_config: "@kt-biome/creatures/general"
prompt_mode: replace
system_prompt_file: prompts/niche.md
```

### File convention

```
creatures/<name>/
  config.yaml           # required
  prompts/system.md     # if referenced
  tools/                # custom tool modules (by convention)
  memory/               # context files (by convention)
  subagents/            # custom sub-agent configs (by convention)
```

These subfolder names are conventions only. The loader resolves each
`module:` path relative to the agent folder via `ModuleLoader` — there
is no auto-scan of `tools/` or `subagents/`, so every custom module
must be declared in `config.yaml`.

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

Creature entry fields (also accepts any AgentConfig field inline, e.g.
`system_prompt_file`, `controller`, `output_wiring`, …):

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | str | — | Creature name. |
| `base_config` (or `config`) | str | — | Config path (agent config). |
| `channels.listen` | list[str] | `[]` | Channels the creature consumes. |
| `channels.can_send` | list[str] | `[]` | Channels the creature can publish to. |
| `output_log` | bool | `false` | Capture stdout per creature. |
| `output_log_size` | int | `100` | Max lines per creature's log buffer. |
| `output_wiring` | list | `[]` | Framework-level auto-delivery of this creature's turn-end output to other creatures. See [Output wiring](#output-wiring) for the entry shape. |

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
    backend_type: openai | codex        # canonical set (see note below)
    base_url: str
    api_key_env: str
    provider_name: str                  # compatibility identity for native tools
    provider_native_tools: [str, ...]   # auto-injected native tools this backend serves

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
    variation_groups:          # optional — see Variation selector
      <group>:
        <option>:
          <dotted.path>: value
```

Canonical `backend_type` values are `openai` and `codex`. Legacy values
(`anthropic`, `codex-oauth`) are accepted for back-compat and silently
normalized on read — `anthropic` → `openai` (Anthropic's OpenAI-compat
endpoint; there is no native Anthropic client), `codex-oauth` → `codex`.
When adding a provider, prefer the canonical values.

Built-in provider names (`codex`, `openai`, `openrouter`, `anthropic`,
`gemini`, `mimo`) cannot be deleted; their base URLs and `api_key_env`
values are fixed via built-in defaults. Per-agent overrides via
`controller.base_url` / `controller.api_key_env` still work.

Custom backends may also declare:

- `provider_name` — the compatibility identity used when checking whether a
  provider-native tool supports this backend.
- `provider_native_tools` — the built-in provider-native tools to auto-inject
  into creatures using this backend.

See [builtins.md — LLM presets](builtins.md#llm-presets) for every
shipped preset, [builtins.md — Variation groups](builtins.md#variation-groups)
for the per-preset catalogue, and [Variation selector](#variation-selector)
for how to pick a specific variation in a controller config.

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
| `transport` | `stdio` \| `http` | — | Transport. `http` speaks Server-Sent Events (SSE). |
| `command` | str | — | stdio executable. |
| `args` | list[str] | `[]` | stdio args. |
| `env` | dict[str,str] | `{}` | stdio env. |
| `url` | str | — | HTTP/SSE endpoint for `http` transport. |

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
io:
  - name: discord_input
    module: my_package.io.discord
    class: DiscordInput
triggers:
  - name: webhook
    module: my_package.triggers.webhook
    class: WebhookTrigger
skills:
  - name: repo-surgery
    path: skills/repo-surgery
commands:
  - name: handoff
    module: my_package.commands.handoff
    class: HandoffCommand
user_commands:
  - name: deploy
    module: my_package.user_commands.deploy
    class: DeployCommand
prompts:
  - name: git-safety
    path: prompts/git-safety.md
framework_hints:
  framework.execution_model.dynamic: "..."
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
| `io` | list | `[{name, module, class}]` — contributed input/output modules resolved by package name. |
| `triggers` | list | `[{name, module, class}]` — contributed trigger classes. |
| `skills` | list | `[{name, path, description?}]` — contributed procedural skill bundles. |
| `commands` | list | `[{name, module, class, override?}]` — controller `##name##` commands. |
| `user_commands` | list | `[{name, module, class}]` — human-facing slash commands. |
| `prompts` / `templates` | list | `[{name, path}]` — reusable prompt fragments for Jinja `{% include %}`. |
| `framework_hints` | dict[str,str] | Package-level override map for framework-hint prose blocks. |
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
