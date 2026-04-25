---
title: Authoring configuration
summary: Creature config shape, inheritance, prompt chains, and the fields that matter most in day-to-day authoring.
tags:
  - guides
  - config
  - creature
---

# Configuration

For readers who want to tweak an existing creature or wire a new one without reading every field in the reference.

Creature configs are YAML (JSON/TOML also supported). Each top-level key maps to an `AgentConfig` field; sub-blocks like `controller`, `input`, `output` are dataclasses with their own fields. This guide is task-oriented — for the full field list see [reference/configuration](../reference/configuration.md).

Concept primer: [creatures](creatures.md), [composing an agent](../concepts/foundations/composing-an-agent.md).

Env-var interpolation works anywhere: `${VAR}` or `${VAR:default}`.

## How do I switch the model?

Pick a preset from `~/.kohakuterrarium/llm_profiles.yaml` (or add one with `kt config llm add`):

```yaml
controller:
  llm: claude-opus-4.7
  reasoning_effort: high
```

You can also pin a **variation** of the preset — built-in presets expose groups like `reasoning`, `speed`, `thinking` (see [reference/builtins — Variation groups](../reference/builtins.md#variation-groups)):

```yaml
controller:
  llm: claude-opus-4.7@reasoning=xhigh
  # or, the explicit form
  variation_selections:
    reasoning: xhigh
```

Each provider exposes the effort knob on a different path. Set
`reasoning_effort` for Codex, `extra_body.reasoning.effort` for OpenAI
direct and OpenRouter, `extra_body.output_config.effort` for Anthropic
direct, and `extra_body.google.thinking_config.thinking_level` for Gemini
direct. Variations wire these for you; see [reference/configuration —
Provider-specific `extra_body` notes](../reference/configuration.md#provider-specific-extra_body-notes)
if you are setting them by hand.

Or override at the command line for one run:

```bash
kt run path/to/creature --llm gpt-5.4
```

If you want fully inline settings (no profile file), use `model` + `api_key_env` + `base_url`:

```yaml
controller:
  model: gpt-4o
  api_key_env: OPENAI_API_KEY
  base_url: https://api.openai.com/v1
  temperature: 0.3
```

## How do I inherit from an OOTB creature?

```yaml
name: my-swe
base_config: "@kt-biome/creatures/swe"
controller:
  reasoning_effort: xhigh
tools:
  - name: my_tool
    type: custom
    module: ./tools/my_tool.py
```

Scalars override; `controller`/`input`/`output` merge; lists extend and dedup by `name`. To replace a list instead of extending:

```yaml
no_inherit: [tools, subagents]
```

## How do I add a tool?

Shorthand for builtins:

```yaml
tools:
  - bash
  - read
  - web_search
```

With options:

```yaml
tools:
  - name: web_search
    options:
      max_results: 10
      region: us-en
```

Custom (local module):

```yaml
tools:
  - name: my_tool
    type: custom
    module: ./tools/my_tool.py
    class: MyTool
```

Package (from an installed package's `kohaku.yaml`):

```yaml
tools:
  - name: kql
    type: package
```

See [Custom Modules](custom-modules.md) for the protocol.

Provider-native tools can also appear automatically. For example, a
Codex-backed creature gets `image_gen` injected unless you opt out:

```yaml
disable_provider_tools:
  - image_gen
```

If you want to keep the provider-native tool but override its knobs, wire it
explicitly under `tools:`; the explicit entry wins over auto-injection.

## How do I add a sub-agent?

```yaml
subagents:
  - plan
  - worker
  - name: my_critic
    type: custom
    module: ./subagents/critic.py
    config: CRITIC_CONFIG
    interactive: true       # stays alive across parent turns
    can_modify: true
    options:
      budget_inherit: true
```

Built-ins: `worker`, `coordinator`, `explore`, `plan`, `research`, `critic`, `response`, `memory_read`, `memory_write`, `summarize`.

If you also set `max_iterations` on the parent creature, sub-agents can share
that turn budget or take an isolated slice:

```yaml
max_iterations: 30
subagents:
  - name: explore
    type: builtin
    options:
      budget_inherit: true      # default: child consumes from the same pool
  - name: critic
    type: builtin
    options:
      budget_allocation: 5      # child gets its own 5-turn pool
```

## How do I add a trigger?

```yaml
triggers:
  - type: timer
    options: { interval: 300 }
    prompt: "Check for pending tasks."
  - type: channel
    options: { channel: alerts }
  - type: context
    options: { debounce_ms: 200 }
    prompt: "Context changed — re-plan if needed."
```

Built-ins: `timer`, `context`, `channel`, `custom`, `package`. `prompt`
is injected as the `TriggerEvent.prompt_override` when the trigger fires.
For a clock-aligned scheduler, expose `SchedulerTrigger` as a setup tool
instead — see [How do I add a tool?](#how-do-i-add-a-tool) and the
`add_schedule` entry in [reference/builtins](../reference/builtins.md#setup-able-triggers-exposed-as-tools-via-type-trigger).

## How do I set up compaction?

```yaml
compact:
  enabled: true
  threshold: 0.8
  target: 0.5
  keep_recent_turns: 5
  compact_model: gpt-4o-mini
```

See [Sessions](sessions.md) for what compaction does.

## How do I add a custom input?

```yaml
input:
  type: custom
  module: ./inputs/discord.py
  class: DiscordInput
  options:
    token: "${DISCORD_TOKEN}"
    channel_id: 123456
```

Built-in types: `cli`, `cli_nonblocking`, `tui`, `none`. Audio/ASR inputs
should be configured as custom or package modules; see the conversational
example and [Custom Modules](custom-modules.md) for the protocol.

## How do I add a named output sink?

Useful when tools or sub-agents want to route to a specific channel (TTS, Discord, file):

```yaml
output:
  type: stdout
  named_outputs:
    tts:
      type: console_tts        # prints character-by-character for quick demos
      options: { char_delay: 0.02 }
    discord:
      type: custom
      module: ./outputs/discord.py
      class: DiscordOutput
      options: { webhook_url: "${DISCORD_WEBHOOK}" }
```

Built-in output types: `stdout`, `stdout_prefixed`, `console_tts`,
`dummy_tts`, `tui`. There is no plain `tts` type — `console_tts` and
`dummy_tts` are the shipped TTS-shaped outputs; richer TTS backends are
custom/package outputs.

## How do I gate a tool with a plugin?

Lifecycle plugin that blocks dangerous commands:

```yaml
plugins:
  - name: tool_guard
    type: custom
    module: ./plugins/tool_guard.py
    class: ToolGuard
    options:
      deny_patterns: ["rm -rf", "dd if="]
```

See [Plugins](plugins.md) for writing the plugin class and [examples/plugins/tool_guard.py](../../examples/plugins/tool_guard.py) for a reference implementation.

## How do I register MCP servers?

Per creature:

```yaml
mcp_servers:
  - name: sqlite
    transport: stdio
    command: mcp-server-sqlite
    args: ["/var/db/my.db"]
  - name: docs_api
    transport: http
    url: https://mcp.example.com/sse
    env: { API_KEY: "${DOCS_API_KEY}" }
```

Global (`~/.kohakuterrarium/mcp_servers.yaml`) uses the same schema. See [MCP](mcp.md).

## How do I change the tool call format?

```yaml
tool_format: bracket        # default: [/name]@@arg=value\n[name/]
# or
tool_format: xml            # <name arg="value"></name>
# or
tool_format: native         # provider-native function calling
```

See [creatures guide — Tool format](creatures.md) for the concrete shape of each, and [reference/configuration.md — `tool_format`](../reference/configuration.md) for fully custom delimiter configs.

## How do I choose dynamic vs static skill mode?

```yaml
skill_mode: dynamic   # default — the `info` framework command loads full docs on demand
# or
skill_mode: static    # full tool docs baked into system prompt
```

Procedural skills are a separate layer. Package skills default disabled and are
opted in by name (or `"*"` for all package skills):

```yaml
skills:
  - repo-surgery
  - "*"
skill_index_budget_bytes: 4096
```

Users can then manage discovered skills at runtime with `/skill ...`, and the
model can invoke them explicitly via `##skill <name>##`.

## How do I keep a creature alive without user input?

```yaml
input:
  type: none
triggers:
  - type: timer
    options: { interval: 60 }
    prompt: "Check for anomalies."
```

A `none` input plus any trigger is the standard monitor-agent pattern.

## How do I bound a run?

```yaml
termination:
  max_turns: 15
  max_duration: 600
  idle_timeout: 120
  keywords: ["DONE", "ABORT"]
```

Any met condition stops the agent.

If what you want is "the whole agent tree only gets N LLM calls", use the
shared iteration budget instead:

```yaml
max_iterations: 30
```

That budget is consumed by the parent controller and, by default, by any
sub-agent with `budget_inherit: true`.

## How do I wire a deterministic pipeline edge?

When the creature runs inside a terrarium, `output_wiring` turns each
turn-end into a `creature_output` event that lands directly in another
creature's queue — bypassing channels entirely:

```yaml
output_wiring:
  - runner                                   # shorthand: ship output to `runner`
  - to: analyzer
    prompt: "[From coder] {content}"         # template; {content} etc. filled in
  - { to: root, with_content: false }        # metadata-only ping
```

Outside a terrarium, `output_wiring` is a no-op. See the full entry
shape at [reference/configuration — Output wiring](../reference/configuration.md#output-wiring)
and [terrariums guide — output wiring](terrariums.md#output-wiring)
for the terrarium-side view.

## How do I share state across creatures (without a terrarium)?

Give them the same `session_key`:

```yaml
name: writer
session_key: shared-workspace
---
name: reviewer
session_key: shared-workspace
```

Both creatures now share `Scratchpad` and `ChannelRegistry`. Useful when multiple creatures run in the same process without a terrarium.

## How do I configure memory/embedding?

```yaml
memory:
  embedding:
    provider: model2vec
    model: "@retrieval"
```

See [Memory](memory.md).

## How do I pin a creature to a specific working directory?

```bash
kt run path/to/creature --pwd /path/to/project
```

`pwd` is passed to every tool's `ToolContext`.

## Troubleshooting

- **Env var not expanding.** Use `${VAR}` (with braces). `$VAR` is left literal.
- **Child config "lost" a tool from the parent.** You declared `no_inherit: [tools]`. Remove it to extend instead.
- **Config loads but tool isn't present.** Shorthand names are resolved against the built-in tool catalog — typos fall through silently. Check `kt info path/to/creature`.
- **Provider-native tool didn't appear.** Confirm the backend advertises it through `provider_native_tools`, and that you didn't opt out with `disable_provider_tools`.
- **Model switching feels ambiguous.** Use the canonical `provider/name` form (`/model codex/gpt-5.5`, `/model openai/gpt-5.4-api`).
- **Two conflicting settings.** CLI overrides (`--llm`) win over config; config wins over `default_model` from `llm_profiles.yaml`.

## See also

- [Reference / configuration](../reference/configuration.md) — every field, type, and default.
- [Creatures](creatures.md) — folder layout and anatomy.
- [Plugins](plugins.md), [Custom Modules](custom-modules.md), [MCP](mcp.md), [Memory](memory.md) — wiring specific surfaces.
