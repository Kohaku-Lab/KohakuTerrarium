# Creatures

For readers who want to author, customize, or package a standalone agent.

A **creature** is a self-contained agent: its own controller, tools, sub-agents, triggers, prompts, and I/O. A creature runs standalone (`kt run path/to/creature`), inherits from another creature, or ships inside a package. It never knows whether it's in a terrarium.

Concept primer: [what is an agent](../concepts/foundations/what-is-an-agent.md), [composing an agent](../concepts/foundations/composing-an-agent.md), [module index](../concepts/modules/README.md).

## Anatomy

A creature lives in a folder:

```
creatures/my-agent/
  config.yaml            # required
  prompts/
    system.md            # referenced by system_prompt_file
    context.md           # referenced by prompt_context_files
  tools/                 # optional custom tool modules
  subagents/             # optional custom sub-agent configs
  memory/                # optional text/markdown memory files
```

Lookup order: `config.yaml` → `config.yml` → `config.json` → `config.toml`. Env-var interpolation (`${VAR}` or `${VAR:default}`) works anywhere in the YAML.

### Minimal config

```yaml
name: my-agent
controller:
  llm: claude-opus-4.6
system_prompt_file: prompts/system.md
tools:
  - read
  - write
  - bash
```

Each field matches an `AgentConfig` dataclass. See [Configuration](configuration.md) for the task-oriented index and [reference/configuration](../reference/configuration.md) for every field.

## Inheritance

Reuse an existing creature as the base:

```yaml
name: my-swe
base_config: "@kt-defaults/creatures/swe"
controller:
  reasoning_effort: high
tools:
  - name: my_tool          # new tool, appended
    type: custom
    module: ./tools/my_tool.py
```

Rules — one unified model across all fields:

- **Scalars**: child wins.
- **Dicts** (`controller`, `input`, `output`, `memory`, `compact`, …): shallow merge.
- **Identity-keyed lists** (`tools`, `subagents`, `plugins`, `mcp_servers`, `triggers`): union by `name`. On name collision the **child wins** and replaces the base entry in place. Items without a `name` concatenate.
- **Prompt files**: `system_prompt_file` concatenates along the chain; inline `system_prompt` is appended last.
- `base_config` resolves `@pkg/...`, `creatures/<name>` (walks up the project root), or a relative path.

Two directives opt out of defaults:

```yaml
# 1. Drop an inherited field entirely, then redefine from scratch
no_inherit: [tools, plugins]
tools:
  - { name: think, type: builtin }

# 2. Replace the inherited prompt chain (sugar for
#    no_inherit: [system_prompt, system_prompt_file])
prompt_mode: replace
system_prompt_file: prompts/brand_new.md
```

### When to use `prompt_mode: replace`

Especially useful for **sub-agents** and **terrarium creatures** that inherit from a base persona but need a fundamentally different voice:

```yaml
# sub-agent entry inside a creature config
subagents:
  - name: niche_responder
    base_config: "@kt-defaults/subagents/response"
    prompt_mode: replace
    system_prompt_file: prompts/niche_persona.md
```

```yaml
# terrarium creature that re-purposes an OOTB creature as a team specialist
creatures:
  - name: reviewer
    base_config: "@kt-defaults/creatures/critic"
    prompt_mode: replace
    system_prompt: |
      You are the team's lead reviewer. Speak only to approve or reject, with one-line reasoning.
```

Default (`prompt_mode: concat`) is the right move when the base prompt is a general contract you want to extend, not replace.

### Overriding vs extending a list entry

Collision by `name` means the child's entry wins:

```yaml
base_config: "@kt-defaults/creatures/general"
tools:
  - { name: bash, type: custom, module: ./tools/safe_bash.py, class: SafeBash }
```

The child's `bash` replaces the base's `bash` in place; other inherited tools are preserved.

## Prompt files

Keep the system prompt in Markdown. Only put *personality and guidelines* there — the tool list, call syntax, and full tool docs are auto-aggregated.

```markdown
<!-- prompts/system.md -->
You are a focused SWE agent. Use tools immediately rather than narrating.
Prefer minimal diffs. Validate before declaring done.
```

Template variables come from `prompt_context_files`:

```yaml
prompt_context_files:
  style_guide: prompts/style.md
  today:       memory/today.md
```

Inside `system.md`:

```
## Style guide
{{ style_guide }}

## Today
{{ today }}
```

The aggregator appends tool-list, framework hints, env info, and `CLAUDE.md` automatically. Do not duplicate those yourself.

## Skill mode: dynamic vs static

- `skill_mode: dynamic` (default) — tools show up in the prompt as one-line descriptions. The controller loads full docs on demand with the `info` framework command.
- `skill_mode: static` — all tool docs are inlined upfront (larger system prompt, fewer round-trips).

Use `dynamic` unless you want a fixed, auditable prompt.

## Tool format

Controls the syntax the LLM emits to call tools (and to invoke framework commands). Applies to the parser and to the framework-hints block of the system prompt.

Concrete examples for a `bash` call with `command=ls`:

- `bracket` (default) — opens with `[/name]`, closes with `[name/]`, args as `@@key=value` lines:
  ```
  [/bash]
  @@command=ls
  [bash/]
  ```
- `xml` — standard tag-with-attributes form:
  ```
  <bash command="ls"></bash>
  ```
- `native` — provider-native function calling (OpenAI / Anthropic tool use). The LLM emits no text block; the API carries the call structurally.
- dict — custom delimiters (see [configuration reference — `tool_format`](../reference/configuration.md)).

All three formats are interchangeable — pick whichever your model handles best. `native` tends to be most reliable on major providers; `bracket` works everywhere including local models.

## Tools and sub-agents

```yaml
tools:
  - read                              # shorthand = builtin
  - bash
  - name: my_tool                     # custom / package tool
    type: custom
    module: ./tools/my_tool.py
    class_name: MyTool
  - name: web_search
    options:
      max_results: 5
  # Expose a universal trigger as a setup tool — the LLM can install it
  # at runtime by calling this tool name. The framework wraps the trigger
  # class with `CallableTriggerTool`; the short description is prefixed
  # with "**Trigger** — " so the LLM knows it's installing a long-lived
  # side-effect rather than running an immediate action.
  - { name: add_timer, type: trigger }
  - { name: watch_channel, type: trigger }
  - { name: add_schedule, type: trigger }

subagents:
  - worker
  - plan
  - name: my_specialist
    type: custom
    module: ./subagents/specialist.py
    config_name: SPECIALIST_CONFIG
    interactive: true                 # stays alive across parent turns
    can_modify: true
```

Setup-able triggers opt in per-creature — a creature without any
`type: trigger` entries cannot install triggers at runtime. Each
universal `BaseTrigger` subclass declares its own `setup_tool_name`
(e.g. `add_timer`), `setup_description`, and `setup_param_schema`. To
write your own, see [Custom Modules — Triggers](custom-modules.md).

See [reference/builtins](../reference/builtins.md) for the complete tool and sub-agent catalog; [Custom Modules](custom-modules.md) for writing your own.

## Triggers

```yaml
triggers:
  - type: timer
    options: { interval: 600 }
    prompt: "Health check: anything pending?"
  - type: channel
    options: { channel: alerts }
  - type: custom
    module: ./triggers/webhook.py
    class_name: WebhookTrigger
```

Built-in types: `timer`, `idle`, `webhook`, `channel`, `custom`, `package`. See [concepts/modules/trigger](../concepts/modules/trigger.md).

## Startup trigger

Fires once when the creature starts:

```yaml
startup_trigger:
  prompt: "Review the project status and plan today's work."
```

## Termination conditions

```yaml
termination:
  max_turns: 20
  max_duration: 300          # seconds
  idle_timeout: 60           # seconds without events
  keywords: ["DONE", "SHUTDOWN"]
```

Any met condition stops the agent. `keywords` is substring matching on controller output.

## Session key

Multiple creatures can share the same `Session` (scratchpad + channels) by setting `session_key`:

```yaml
session_key: shared_workspace
```

Default is the creature's `name`. Inside a terrarium, each creature gets a private `Session` and a shared `Environment`; see [concepts/modules/session-and-environment](../concepts/modules/session-and-environment.md).

## Framework commands

The controller can emit inline directives that talk to the framework (no tool round-trip). They are documented in the framework-hints prompt block:

Framework commands use the same syntax family as tool calls — whichever `tool_format` you've configured (bracket, XML, native). Default bracket examples, with placeholders as bare identifiers:

- `[/info]tool_or_subagent[info/]` — load full documentation on demand.
- `[/read_job]job_id[read_job/]` — read output from a background job (accepts `--lines N` and `--offset M` in the body).
- `[/jobs][jobs/]` — list running jobs with their IDs.
- `[/wait]job_id[wait/]` — block the current turn until a background job finishes.

Command names share a namespace with tool names; the read-job-output command is called `read_job` precisely to avoid colliding with the `read` file-reader tool.

These are how the agent reads streaming tool output, looks up docs it didn't memorize, and synchronizes with its own background work.

## User commands

Slash commands the *user* types at the CLI/TUI prompt. Built-ins:

| Command | Alias | Effect |
|---|---|---|
| `/help` | `/h`, `/?` | List commands |
| `/status` | `/info` | Model, messages, tools, jobs, compact state |
| `/clear` | | Clear conversation |
| `/model [name]` | `/llm` | List or switch LLM profile |
| `/compact` | | Manual compaction |
| `/regen` | `/regenerate` | Rerun last assistant turn |
| `/plugin [list\|enable\|disable\|toggle] [name]` | `/plugins` | Manage lifecycle plugins |
| `/exit` | `/quit`, `/q` | Graceful exit |

Custom user commands live under `builtins/user_commands/` or ship inside packages. Authoring: [Custom Modules](custom-modules.md).

## Input and output

```yaml
input:
  type: cli                  # or: tui, whisper, asr, none, custom, package
  prompt: "> "
  history_file: ~/.my_agent_history

output:
  type: stdout               # or: tts, tui, custom, package
  named_outputs:
    discord:
      type: custom
      module: ./outputs/discord.py
      class_name: DiscordOutput
      options: { webhook_url: "${DISCORD_WEBHOOK}" }
```

`named_outputs` lets tools or sub-agents route to specific sinks (e.g. Discord webhook, TTS, file). See [concepts/modules/output](../concepts/modules/output.md).

## MCP servers per creature

```yaml
mcp_servers:
  - name: sqlite
    transport: stdio
    command: mcp-server-sqlite
    args: ["/var/db/my.db"]
  - name: docs_api
    transport: http
    url: https://mcp.example.com/sse
```

MCP tools are surfaced to the controller through meta-tools (`mcp_list`, `mcp_call`). Full walkthrough: [MCP](mcp.md).

## Compaction

```yaml
compact:
  enabled: true
  threshold: 0.8             # fraction of max_tokens to trigger
  target: 0.5                # compression target
  keep_recent_turns: 5
  compact_model: gpt-4o-mini  # override for the summarizer pass
```

Compaction runs in the background and does not block the controller. See [Sessions](sessions.md) and [concepts/modules/memory-and-compaction](../concepts/modules/memory-and-compaction.md).

## Plugins

Attach lifecycle/prompt plugins to this creature only:

```yaml
plugins:
  - name: tool_timer
    type: custom
    module: ./plugins/tool_timer.py
    class: ToolTimer
  - name: project_rules
    type: package             # pulled from an installed package's manifest
```

See [Plugins](plugins.md).

## Packaging a creature for reuse

Wrap your creature folder in a package:

```
my-creatures/
  kohaku.yaml
  creatures/
    my-agent/
      config.yaml
      prompts/...
```

`kohaku.yaml`:

```yaml
name: my-creatures
version: "0.1.0"
description: "My shared creatures"
creatures:
  - name: my-agent
```

Install locally (editable) or publish via git:

```bash
kt install ./my-creatures -e
# then:
kt run @my-creatures/creatures/my-agent
```

Push the repo to git and anyone can `kt install <url>`. Full workflow: [Packages](packages.md).

## Troubleshooting

- **Agent ignores tool call syntax.** Check `tool_format`. If you set `native`, the underlying provider must support it.
- **System prompt has two copies of the tool list.** You inlined one in `system.md`. Remove it — the aggregator adds it automatically.
- **Inherited creature overrides everything.** Expected for scalars. To preserve a base list, don't redeclare it at the child level.
- **`base_config: "@pkg/..."` fails to resolve.** `kt list` to confirm the package is installed; package refs live under `~/.kohakuterrarium/packages/`.

## See also

- [Configuration](configuration.md) — task-oriented recipes.
- [Custom Modules](custom-modules.md) — writing your own tools/inputs/outputs/triggers/sub-agents.
- [Plugins](plugins.md) — hook behaviour without forking modules.
- [Packages](packages.md) — shipping creatures for reuse.
- [Reference / configuration](../reference/configuration.md) — every field.
