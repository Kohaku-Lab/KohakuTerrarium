---
title: Built-ins
summary: The bundled tools, sub-agents, triggers, inputs, and outputs — argument shapes, behaviours, and defaults.
tags:
  - reference
  - builtins
---

# Built-ins

Every built-in tool, sub-agent, input, output, user command, framework
command, LLM provider, and LLM preset shipped with KohakuTerrarium.

For the shape of tools vs sub-agents, read
[concepts/modules/tool](../concepts/modules/tool.md) and
[concepts/modules/sub-agent](../concepts/modules/sub-agent.md).
For task-oriented help, see [guides/creatures](../guides/creatures.md)
and [guides/custom-modules](../guides/custom-modules.md).

## Tools

Built-in tool classes live in
`src/kohakuterrarium/builtins/tools/`. Register them in a creature
config under `tools:` by bare name.

### Shell and scripting

**`bash`** — Run a shell command. Picks the first available of `bash`,
`zsh`, `sh`, `fish`, `pwsh`. Respects `KT_SHELL_PATH`. Captures stdout
and stderr, truncated to a cap. Direct execution.

- Args: `command` (str), `working_dir` (str, optional),
  `timeout` (float, optional).

**`python`** — Run a Python subprocess. Respects `working_dir` and
`timeout`. Direct.

- Args: `code` (str), `working_dir`, `timeout`.

### File operations

**`read`** — Read text, image, or PDF content. Records read-state per
file. Images are returned as `base64` data URLs. PDF support requires
`pymupdf`. Direct.

- Args: `path` (str), `offset` (int, optional), `limit` (int, optional).

**`write`** — Create or overwrite a file. Creates parent directories.
Blocks overwrites unless the file was read first (unless `new`). Direct.

- Args: `path`, `content`, `new` (bool, optional).

**`edit`** — Auto-detects unified-diff (`@@`) or search/replace form.
Refuses binary files. Direct.

- Args: `path`, `old_text`/`new_text` or `diff`, `replace_all` (bool).

**`multi_edit`** — Apply an ordered list of edits to one file. Atomic
per file. Modes: `strict` (every edit must apply), `best_effort` (skip
failures), default (partial apply with report). Direct.

- Args: `path`, `edits: list[{old, new}]`, `mode`.

**`glob`** — mtime-sorted glob. Respects `.gitignore`. Early-terminates.
Direct.

- Args: `pattern`, `root` (optional), `limit` (optional).

**`grep`** — Regex search across files. Supports `ignore_case`. Skips
binaries. Direct.

- Args: `pattern`, `path` (optional), `ignore_case` (bool),
  `max_matches`.

**`tree`** — Directory listing with YAML-frontmatter summaries for
markdown files. Direct.

- Args: `path`, `depth`.

### Structured data

**`json_read`** — Read a JSON document by dot-path. Direct.

- Args: `path`, `query` (dot-path).

**`json_write`** — Assign a value at a dot-path. Creates nested objects
as needed. Direct.

- Args: `path`, `query`, `value`.

### Web

**`web_fetch`** — Fetch a URL as markdown. Tries `crawl4ai` →
`trafilatura` → Jina proxy → `httpx + html2text`. 100k-char cap, 30s
timeout. Direct.

- Args: `url`.

**`web_search`** — DuckDuckGo search returning markdown-formatted
results. Direct.

- Args: `query`, `max_results` (int), `region` (str).

### Provider-native media

**`image_gen`** — Generate or edit an image through the provider's own
native image backend. Currently auto-injected for Codex-backed creatures
unless opted out with `disable_provider_tools: [image_gen]`. The executor
never runs it; the provider returns structured image content and the
session store persists the generated file into the session artifacts dir.

- Args: `prompt` plus provider-specific knobs when explicitly wired:
  `output_format`, `size`, `quality`, `action`, `background`.

### Interactive and memory

**`ask_user`** — Prompt the user over stdin (CLI or TUI only).
Stateful.

- Args: `question`.

**`think`** — No-op; preserves reasoning as a tool event for the event
log. Direct.

- Args: `thought`.

**`scratchpad`** — Session-scoped KV store. Shared across agents in a
session.

- Args: `action` (`get` | `set` | `delete` | `list`), `key`, `value`.

**`search_memory`** — FTS / semantic / auto search over the session's
indexed events. Per-agent filter.

- Args: `query`, `mode` (`auto`/`fts`/`semantic`/`hybrid`), `k`,
  `agent`.

### Communication

**`send_message`** — Emit a message to a channel. Resolves creature-
local channels first, then the environment's shared channels. Direct.

- Args: `channel`, `content`, `sender` (optional).

### Introspection

**`info`** — Load on-demand documentation for any tool or sub-agent.
Delegates to skill manifests under
`src/kohakuterrarium/builtin_skills/` and per-agent overrides. Direct.

- Args: `target` (tool or sub-agent name).

**`stop_task`** — Cancel a running background task or trigger by id. Direct.

- Args: `job_id` (job id from any tool call; or the trigger id returned by `add_timer`/`watch_channel`/`add_schedule`).

### Setup-able triggers (exposed as tools via `type: trigger`)

Each universal trigger class is wrapped as its own tool via
`modules/trigger/callable.py:CallableTriggerTool`. A creature opts in by
listing the trigger's `setup_tool_name` under `tools:` with
`type: trigger`. The tool's description is prefixed with
`**Trigger** — ` so the LLM knows calling it installs a long-lived
side-effect. All three return immediately with the installed trigger
id; the trigger itself runs in the background.

**`add_timer`** (wraps `TimerTrigger`) — Install a periodic timer.

- Args: `interval` (seconds, required), `prompt` (required), `immediate` (bool, default false).

**`watch_channel`** (wraps `ChannelTrigger`) — Listen on a named channel.

- Args: `channel_name` (required), `prompt` (optional, supports `{content}`), `filter_sender` (optional).
- The agent's own name is auto-set as `ignore_sender` to prevent self-triggering.

**`add_schedule`** (wraps `SchedulerTrigger`) — Clock-aligned schedule.

- Args: `prompt` (required); exactly one of `every_minutes`, `daily_at` (HH:MM), `hourly_at` (0-59).

### Terrarium (root-only)

**`terrarium_create`** — Start a new terrarium instance. Root-only.

**`terrarium_send`** — Send to a channel in the root's terrarium.

**`creature_start`** — Hot-plug a creature at runtime.

**`creature_stop`** — Stop a creature at runtime.

---

## Sub-agents

Shipped sub-agent configs under
`src/kohakuterrarium/builtins/subagents/`. Reference them in a creature
config under `subagents:` by name.

All builtin sub-agents load `default_plugins: ["default-runtime"]` and use
minimal runtime budgets: turn soft/hard `40/60`, tool-call soft/hard `75/100`,
and no walltime budget.

| Name | Tools | Purpose |
|---|---|---|
| `worker` | `read`, `write`, `bash`, `glob`, `grep`, `edit`, `multi_edit` | Fix bugs, refactor, run validations. |
| `coordinator` | `send_message`, `scratchpad` | Decompose → dispatch → aggregate. |
| `explore` | `glob`, `grep`, `read`, `tree`, `bash` | Read-only exploration. |
| `plan` | `explore` tools + `think` | Read-only planning. |
| `research` | `web_search`, `web_fetch`, `read`, `write`, `think`, `scratchpad` | External research. |
| `critic` | `read`, `glob`, `grep`, `tree`, `bash` | Code review. |
| `response` | `read` | User-facing copy generator. Typically `output_to: external`. |
| `memory_read` | `tree`, `read`, `grep` over the memory folder | Recall from agent memory. |
| `memory_write` | `tree`, `read`, `write` | Persist findings into memory. |
| `summarize` | (no tools) | Condense conversation for handoff or reset. |

---

## Inputs

Shipped input modules under `src/kohakuterrarium/builtins/inputs/`.

**`cli`** — Stdin prompt. Options: `prompt`, `exit_commands`.

**`cli_nonblocking`** — Same surface as `cli` but returns control to
the event loop between keystrokes (useful when triggers fire during
input).

**`none`** — No input. For trigger-only agents.

Audio/ASR implementations are not built-ins. The conversational example ships
opt-in `ASRModule`/Whisper custom input files under
`examples/agent-apps/conversational/custom/`; load them with `type: custom`.

Two further input types are resolved dynamically:

- `tui` — mounted by the Textual app when running under TUI mode.
- `custom` / `package` — loaded via `module` + `class` fields.

---

## Outputs

Shipped output modules under `src/kohakuterrarium/builtins/outputs/`.

**`stdout`** — Print to stdout. Options:
`prefix`, `suffix`, `stream_suffix`, `flush_on_stream`.

**`stdout_prefixed`** — `stdout` with a per-line prefix, useful for
tagging side outputs.

**`console_tts`** — Console-only TTS shim that prints the synthesized
text character-by-character with a configurable `char_delay`. Intended
for demos and testing — no audio backend.

**`dummy_tts`** — Silent TTS that fires the usual TTS lifecycle
events without any output. Useful in tests.

Additional routed types:

- `tui` — renders into the Textual TUI widget tree.
- `custom` / `package` — loaded via `module` + `class`.

There is no plain `tts` registry key. Real TTS backends (Fish, Edge,
OpenAI, etc.) are shipped as custom/package outputs that subclass
`TTSModule`.

---

## User commands

Slash commands available inside input modules. Under
`src/kohakuterrarium/builtins/user_commands/`.

| Command | Aliases | Purpose |
|---|---|---|
| `/help` | `/h`, `/?` | List commands. |
| `/status` | `/info` | Model, message count, tools, jobs, compact state. |
| `/clear` | | Clear conversation (the session log retains history). |
| `/model [name]` | `/llm` | Show current model or switch profile. Accepts canonical `provider/name[@variations]`. |
| `/compact` | | Manual context compaction. |
| `/regen` | `/regenerate`, `/retry` | Re-run the last assistant turn as a sibling branch. |
| `/edit <message_index> <new content>` | — | Edit a past user message and re-run from that point as a new branch. |
| `/branch [<turn> <branch_id>\|latest]` | `/br` | List or switch the live branch for regen/edit alternatives. |
| `/fork [event_id] [--name name]` | — | Copy the current session into a new `.kohakutr` file for alternate exploration. |
| `/plugin [list\|enable\|disable\|toggle] [name]` | `/plugins` | Inspect or toggle plugins. |
| `/skill [list\|enable\|disable\|toggle\|show] [name]` | `/skills` | Inspect or toggle procedural skills. |
| `/<skill-name> [args]` | — | User-invoke path for an enabled procedural skill when no built-in slash command shadows that name. |
| `/exit` | `/quit`, `/q` | Graceful exit. On web, a force flag may be required. |

---

## Framework commands

Inline directives the LLM can emit instead of a tool call. They talk
to the framework directly (no tool round-trip). Defined under
`src/kohakuterrarium/commands/`.

Framework commands use the **same syntax family** as tool calls — they follow the creature's configured `tool_format` (bracket / XML / native). The default bracket form with bare-identifier placeholders:

- `[/info]tool_or_subagent[info/]` — Load a tool's, sub-agent's, or procedural skill's documentation on demand.
- `[/read_job]job_id[read_job/]` — Read output from a background job. Supports `--lines N` and `--offset M` in the body.
- `[/jobs][jobs/]` — List running jobs with IDs.
- `[/wait]job_id[wait/]` — Block the current turn until a background job finishes.
- `[/skill]skill_name [args][skill/]` — Return a procedural skill body to the model for explicit invocation.

Command names share a namespace with tool names; the command for reading job output is called `read_job` to avoid colliding with the `read` file-reader tool. Defined under `src/kohakuterrarium/commands/`.

---

## LLM providers

Built-in provider types (backends):

| Provider | Backend type | Transport | Notes |
|---|---|---|---|
| `codex` | `codex` | Codex OAuth (ChatGPT subscription) | `kt login codex`; routed via `CodexOAuthProvider`. Ships provider-native tool support such as `image_gen`. |
| `openai` | `openai` | OpenAI `/chat/completions` | API-key auth (`OPENAI_API_KEY`). |
| `openrouter` | `openai` | OpenAI-compat against OpenRouter | API-key auth (`OPENROUTER_API_KEY`); unified `reasoning` param. |
| `anthropic` | `anthropic` | Anthropic-compatible Messages API | API-key auth (`ANTHROPIC_API_KEY`). Uses the official `anthropic` SDK. Claude-specific knobs go through `extra_body` (`thinking.*`, `output_config.*`). Prompt-caching markers are auto-applied unless disabled. |
| `gemini` | `openai` | Google's OpenAI-compat endpoint | API-key auth (`GEMINI_API_KEY`). |
| `mimo` | `openai` | Xiaomi MiMo | `kt login mimo`. |

Canonical backend types are `openai`, `anthropic`, and `codex`. Legacy
`codex-oauth` backend type values are silently migrated on read (see
[configuration reference](configuration.md#llm-profiles-kohakuterrariumllm_profilesyaml)).

## LLM presets

Shipped in `src/kohakuterrarium/llm/presets.py`. Use them as `llm:` or
`--llm` values.

Naming convention (post-2026-04 refactor):

- Direct / native-API variants are the primary name
  (`claude-opus-4.7`, `gemini-3.1-pro`, `mimo-v2-pro`).
- OpenRouter-routed variants use the `-or` suffix
  (`claude-opus-4.7-or`).
- OpenAI is an exception: `gpt-5.4` stays bound to the **Codex OAuth**
  provider; the direct OpenAI API variant uses `-api`, OpenRouter uses
  `-or`.
- Legacy names (`claude-opus-4.6-direct`, `or-gpt-5.4`,
  `gemini-3.1-pro-direct`, `mimo-v2-pro-direct`, …) survive as aliases
  so existing configs keep working.

### OpenAI via Codex OAuth

- `gpt-5.5`
- `gpt-5.4` (aliases: `gpt5`, `gpt54`)
- `gpt-5.3-codex` (`gpt53`)
- `gpt-5.1`
- `gpt-4o-codex` (aliases: `gpt4o`, `gpt-4o`)
- `gpt-4o-mini-codex` (alias: `gpt-4o-mini`)

### OpenAI Direct API (`-api` suffix)

- `gpt-5.4-api` (legacy alias: `gpt-5.4-direct`)
- `gpt-5.4-mini-api` (`gpt-5.4-mini-direct`)
- `gpt-5.4-nano-api` (`gpt-5.4-nano-direct`)
- `gpt-5.3-codex-api` (`gpt-5.3-codex-direct`)
- `gpt-5.1-api` (`gpt-5.1-direct`)
- `gpt-4o-api` (`gpt-4o-direct`)
- `gpt-4o-mini-api` (`gpt-4o-mini-direct`)

### OpenAI via OpenRouter (`-or` suffix)

- `gpt-5.4-or` (legacy alias: `or-gpt-5.4`)
- `gpt-5.4-mini-or` (`or-gpt-5.4-mini`)
- `gpt-5.4-nano-or` (`or-gpt-5.4-nano`)
- `gpt-5.3-codex-or` (`or-gpt-5.3-codex`)
- `gpt-5.1-or` (`or-gpt-5.1`)
- `gpt-4o-or` (`or-gpt-4o`)
- `gpt-4o-mini-or` (`or-gpt-4o-mini`)

### Anthropic Claude Direct (no suffix — primary)

Routed through the native Anthropic-compatible Messages API. Effort via
`extra_body.output_config.effort`.

- `claude-opus-4.7` (aliases: `claude-opus`, `opus`)
- `claude-opus-4.6` (legacy alias: `claude-opus-4.6-direct`)
- `claude-sonnet-4.6` (aliases: `claude`, `claude-sonnet`, `sonnet`; legacy: `claude-sonnet-4.6-direct`)
- `claude-haiku-4.5` (aliases: `claude-haiku`, `haiku`; legacy: `claude-haiku-4.5-direct`)

### Anthropic Claude via OpenRouter (`-or` suffix)

- `claude-opus-4.7-or`
- `claude-opus-4.6-or`
- `claude-sonnet-4.6-or`
- `claude-sonnet-4.5-or`
- `claude-haiku-4.5-or`
- `claude-sonnet-4-or` (legacy alias: `claude-sonnet-4`)
- `claude-opus-4-or` (legacy alias: `claude-opus-4`)

### Google Gemini Direct (OpenAI-compat)

- `gemini-3.1-pro` (aliases: `gemini`, `gemini-pro`; legacy: `gemini-3.1-pro-direct`)
- `gemini-3-flash` (`gemini-flash`; legacy: `gemini-3-flash-direct`)
- `gemini-3.1-flash-lite` (`gemini-lite`; legacy: `gemini-3.1-flash-lite-direct`)

### Google Gemini via OpenRouter (`-or` suffix)

- `gemini-3.1-pro-or`
- `gemini-3-flash-or`
- `gemini-3.1-flash-lite-or`
- `nano-banana` (image-generation model, OpenRouter)

### Google Gemma (OpenRouter)

- `gemma-4-31b` (aliases: `gemma`, `gemma-4`)
- `gemma-4-26b`

### Qwen (OpenRouter)

- `qwen3.5-plus` (`qwen`)
- `qwen3.5-flash`
- `qwen3.5-397b`
- `qwen3.5-27b`
- `qwen3-coder` (`qwen-coder`)
- `qwen3-coder-plus`

### Moonshot Kimi (OpenRouter)

- `kimi-k2.5` (`kimi`)
- `kimi-k2-thinking`

### MiniMax (OpenRouter)

- `minimax-m2.7` (`minimax`)
- `minimax-m2.5`

### Xiaomi MiMo Direct (no suffix — primary)

- `mimo-v2-pro` (`mimo`; legacy alias: `mimo-v2-pro-direct`)
- `mimo-v2-flash` (legacy alias: `mimo-v2-flash-direct`)

### Xiaomi MiMo via OpenRouter (`-or` suffix)

- `mimo-v2-pro-or`
- `mimo-v2-flash-or`

### GLM (Z.ai, OpenRouter)

- `glm-5`
- `glm-5-turbo` (alias: `glm`)

### xAI Grok (OpenRouter)

- `grok-4` (`grok`)
- `grok-4.20`
- `grok-4.20-multi`
- `grok-4-fast` (`grok-fast`)
- `grok-4.1-fast`
- `grok-code-fast` (`grok-code`)
- `grok-3`
- `grok-3-mini`

### Mistral (OpenRouter)

- `mistral-large-3` (aliases: `mistral`, `mistral-large`)
- `mistral-medium-3.1` (`mistral-medium`)
- `mistral-medium-3`
- `mistral-small-4` (`mistral-small`)
- `mistral-small-3.2`
- `magistral-medium` (`magistral`)
- `magistral-small`
- `codestral`
- `devstral-2` (`devstral`)
- `devstral-medium`
- `devstral-small`
- `pixtral-large`
- `ministral-3-14b` (`ministral`)
- `ministral-3-8b`

Preset token windows (`max_context` / `max_output`) are set per preset —
see `src/kohakuterrarium/llm/presets.py` for the exact values, or
`kt config llm show <name>`. `controller.max_tokens` overrides
`max_output`; to adjust the compaction window, set `compact.max_tokens`.

Built-in preset merging also picks up `llm_presets` contributed by
installed packages; see
[configuration.md — Package manifest](configuration.md#package-manifest-kohakuyaml).

## Variation groups

A variation group lets one preset expose multiple knobs without
duplicating the entry. Select with the `preset@group=option` shorthand
in `llm:` / `--llm`, or via `controller.variation_selections`; see
[configuration reference — Variation selector](configuration.md#variation-selector).

Presets not listed here have no variation group — their defaults are
fixed.

### OpenAI — Codex OAuth

| Preset | Group | Options |
|---|---|---|
| `gpt-5.5` | `reasoning` | `none`, `low`, `medium`, `high`, `xhigh` |
| `gpt-5.5` | `speed` | `normal`, `fast` (maps to `service_tier: priority`) |
| `gpt-5.4` | `reasoning` | `none`, `low`, `medium`, `high`, `xhigh` |
| `gpt-5.4` | `speed` | `normal`, `fast` (maps to `service_tier: priority`) |
| `gpt-5.3-codex` | `reasoning` | `none`, `low`, `medium`, `high`, `xhigh` |
| `gpt-5.1` | `reasoning` | `none`, `low`, `medium`, `high`, `xhigh` |

### OpenAI — Direct API (`-api` suffix)

Patches `extra_body.reasoning.effort`.

| Preset | Group | Options |
|---|---|---|
| `gpt-5.4-api`, `gpt-5.4-mini-api`, `gpt-5.4-nano-api`, `gpt-5.3-codex-api`, `gpt-5.1-api` | `reasoning` | `none`, `low`, `medium`, `high`, `xhigh` |

### OpenAI — OpenRouter (`-or` suffix)

Patches `extra_body.reasoning.effort` via OpenRouter's unified param.

| Preset | Group | Options |
|---|---|---|
| `gpt-5.4-or`, `gpt-5.4-mini-or`, `gpt-5.4-nano-or`, `gpt-5.3-codex-or`, `gpt-5.1-or` | `reasoning` | `minimal`, `low`, `medium`, `high`, `xhigh` |

### Anthropic — Direct

Patches `extra_body.output_config.effort` via the compat layer.

| Preset | Group | Options |
|---|---|---|
| `claude-opus-4.7` | `reasoning` | `low`, `medium`, `high`, `xhigh`, `max` |
| `claude-opus-4.6`, `claude-sonnet-4.6` | `reasoning` | `low`, `medium`, `high`, `max` |

Haiku 4.5 uses the older extended-thinking (`budget_tokens`) and has
no variation group.

### Anthropic — OpenRouter (`-or` suffix)

Patches `extra_body.reasoning.effort`. `xhigh` is only honoured by
Opus 4.7.

| Preset | Group | Options |
|---|---|---|
| `claude-opus-4.7-or` | `reasoning` | `minimal`, `low`, `medium`, `high`, `xhigh` |
| `claude-opus-4.6-or`, `claude-sonnet-4.6-or`, `claude-sonnet-4.5-or`, `claude-opus-4-or`, `claude-sonnet-4-or` | `reasoning` | `minimal`, `low`, `medium`, `high` |
| `claude-haiku-4.5-or` | `reasoning` | `off`, `low`, `medium`, `high` |

### Google Gemini — Direct

Patches `extra_body.google.thinking_config.thinking_level`.

| Preset | Group | Options |
|---|---|---|
| `gemini-3.1-pro` | `thinking` | `low`, `medium`, `high` |
| `gemini-3-flash`, `gemini-3.1-flash-lite` | `thinking` | `minimal`, `low`, `medium`, `high` |

### Google Gemini — OpenRouter

| Preset | Group | Options |
|---|---|---|
| `gemini-3.1-pro-or`, `gemini-3-flash-or`, `gemini-3.1-flash-lite-or` | `reasoning` | `minimal`, `low`, `medium`, `high` |

### Gemma / Qwen / Kimi / MiMo / GLM — OpenRouter

Share the same OpenRouter unified reasoning group (unless noted).

| Preset | Group | Options |
|---|---|---|
| `gemma-4-31b`, `gemma-4-26b` | `reasoning` | `minimal`, `low`, `medium`, `high` |
| `qwen3.5-plus`, `qwen3.5-flash`, `qwen3.5-397b`, `qwen3.5-27b`, `qwen3-coder`, `qwen3-coder-plus` | `reasoning` | `minimal`, `low`, `medium`, `high` |
| `kimi-k2.5` | `reasoning` | `minimal`, `low`, `medium`, `high` |
| `mimo-v2-pro`, `mimo-v2-flash`, `mimo-v2-pro-or`, `mimo-v2-flash-or` | `reasoning` | `minimal`, `low`, `medium`, `high` |
| `glm-5`, `glm-5-turbo` | `reasoning` | `minimal`, `low`, `medium`, `high` |

`kimi-k2-thinking` has always-on thinking — no variation group.

### Mistral — OpenRouter

| Preset | Group | Options |
|---|---|---|
| `mistral-small-4` | `reasoning` | `none`, `high` |

Other Mistral presets (`mistral-large-3`, `mistral-medium-*`,
`mistral-small-3.2`, `codestral`, `devstral-*`, `pixtral-large`,
`ministral-*`) are not reasoning models. `magistral-medium` and
`magistral-small` have always-on reasoning — no variation group.

### Grok / MiniMax — OpenRouter

Grok 4.x (`grok-4`, `grok-4.20`, `grok-4.20-multi`, `grok-4-fast`,
`grok-4.1-fast`, `grok-code-fast`) has mandatory non-configurable
reasoning. `grok-3` / `grok-3-mini` are legacy non-reasoning models.
`minimax-m2.7` / `minimax-m2.5` have mandatory reasoning. None expose
a variation group.

---

## Prompt plugins

Shipped prompt plugins (loaded by `prompt/aggregator.py`). Ordered by
priority (lower = earlier).

| Priority | Name | Emits |
|---|---|---|
| 50 | `ToolListPlugin` | Tool name + one-line description. |
| 45 | `FrameworkHintsPlugin` | Framework command examples (`info`, `read_job`, `jobs`, `wait`, native tool usage) and tool-call format examples. |
| 40 | `EnvInfoPlugin` | `cwd`, platform, date/time. |
| 30 | `ProjectInstructionsPlugin` | Loads `CLAUDE.md` and `.claude/rules.md`. |

Custom prompt plugins subclass `BasePlugin` and register via the
`plugins` field in a creature config. See
[plugin-hooks.md](plugin-hooks.md) for lifecycle and callback hooks.

Separate from prompt plugins, tools may also contribute one-line or short
paragraph guidance into the aggregated `## Tool guidance` section.

---

## Compose algebra

Operator precedence: `* > | > & > >>`.

| Operator | Meaning |
|---|---|
| `a >> b` | Sequence (auto-flatten). `>> {key: fn}` forms a Router. |
| `a & b` | Product (`asyncio.gather`; broadcast input). |
| `a \| b` | Fallback (catch exception, try next). |
| `a * N` | Retry (N additional attempts). |

Factories: `Pure`, `Sequence`, `Product`, `Fallback`, `Retry`,
`Router`, `Iterator`. Wrapping helpers: `agent(config_path)` for
persistent agents, `factory(config)` for ephemeral per-call agents.
`effects.Effects()` provides a side-effect logging handle.

Runnable methods: `.map(f)` (post-transform output),
`.contramap(f)` (pre-transform input),
`.fails_when(pred)` (raise on a predicate).

---

## MCP surface

Built-in MCP meta-tools (exposed when `mcp_servers` is configured):

- `mcp_list` — list connected servers and their tools.
- `mcp_call` — invoke a tool on a specific server.
- `mcp_connect` — connect to a server declared in config.
- `mcp_disconnect` — tear down a connection.

Server tools are surfaced in the system prompt under
`## Available MCP Tools`. Transports: `stdio` (subprocess) and
`http`/SSE.

Python surface: `MCPServerConfig`, `MCPClientManager` in
`kohakuterrarium.mcp`.

---

## Extensions

A package's `kohaku.yaml` may contribute `creatures`, `terrariums`,
`tools`, `plugins`, `io`, `triggers`, `skills`, `commands`,
`user_commands`, `prompts`, `framework_hints`, `llm_presets`, and
`python_dependencies`. `kt extension list` inventories them. Python modules
resolve by `module:class` refs; configs resolve via `@pkg/path`. See
[configuration.md — Package manifest](configuration.md#package-manifest-kohakuyaml).

---

## See also

- Concepts: [tool](../concepts/modules/tool.md),
  [sub-agent](../concepts/modules/sub-agent.md),
  [channel](../concepts/modules/channel.md),
  [patterns](../concepts/patterns.md).
- Guides: [creatures](../guides/creatures.md),
  [custom modules](../guides/custom-modules.md),
  [plugins](../guides/plugins.md).
- Reference: [configuration](configuration.md),
  [plugin-hooks](plugin-hooks.md), [python](python.md), [cli](cli.md).
