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

**`list_triggers`** — List active triggers with IDs. Direct.

**`stop_task`** — Cancel a running background task by job id. Direct.

- Args: `job_id`.

**`create_trigger`** — Install a trigger at runtime. Supports
`Timer`, `Scheduler`, `Channel` types. The controller is required to
read full docs via the `info` framework command (`info create_trigger`) before using it.

- Args: depend on the trigger type; see the skill manifest.

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

**`none`** — No input. For trigger-only agents.

**`whisper`** — Microphone + Silero VAD + `openai-whisper`. Options
include `model`, `language`, VAD thresholds. Requires FFmpeg.

**`asr`** — Abstract base for custom speech recognition.

Two further input types are resolved dynamically:

- `tui` — mounted by the Textual app when running under TUI mode.
- `custom` / `package` — loaded via `module` + `class_name` fields.

---

## Outputs

Shipped output modules under `src/kohakuterrarium/builtins/outputs/`.

**`stdout`** — Print to stdout. Options:
`prefix`, `suffix`, `stream_suffix`, `flush_on_stream`.

**`tts`** — Fish / Edge / OpenAI TTS (auto-detected). Supports
streaming and hard interruption.

Additional routed types:

- `tui` — renders into the Textual TUI widget tree.
- `custom` / `package` — loaded via module + class.

---

## User commands

Slash commands available inside input modules. Under
`src/kohakuterrarium/builtins/user_commands/`.

| Command | Aliases | Purpose |
|---|---|---|
| `/help` | `/h`, `/?` | List commands. |
| `/status` | `/info` | Model, message count, tools, jobs, compact state. |
| `/clear` | | Clear conversation (the session log retains history). |
| `/model [name]` | `/llm` | Show current model or switch profile. |
| `/compact` | | Manual context compaction. |
| `/regen` | `/regenerate` | Re-run the last assistant turn. |
| `/plugin [list\|enable\|disable\|toggle] [name]` | `/plugins` | Inspect or toggle plugins. |
| `/exit` | `/quit`, `/q` | Graceful exit. On web, a force flag may be required. |

---

## Framework commands

Inline directives the LLM can emit instead of a tool call. They talk
to the framework directly (no tool round-trip). Defined under
`src/kohakuterrarium/commands/`.

Framework commands use the **same syntax family** as tool calls — they follow the creature's configured `tool_format` (bracket / XML / native). The default bracket form with bare-identifier placeholders:

- `[/info]tool_or_subagent[info/]` — Load a tool's or sub-agent's full documentation on demand.
- `[/read_job]job_id[read_job/]` — Read output from a background job. Supports `--lines N` and `--offset M` in the body.
- `[/jobs][jobs/]` — List running jobs with IDs.
- `[/wait]job_id[wait/]` — Block the current turn until a background job finishes.

Command names share a namespace with tool names; the command for reading job output is called `read_job` to avoid colliding with the `read` file-reader tool. Defined under `src/kohakuterrarium/commands/`.

---

## LLM providers

Built-in provider types (backends):

| Provider | Transport | Notes |
|---|---|---|
| `codex` | OpenAI chat API over Codex OAuth | ChatGPT subscription auth; `kt login codex`. |
| `openai` | OpenAI chat API | API-key auth. |
| `openrouter` | OpenAI-compatible | API-key auth; routes to many models. |
| `anthropic` | Native Anthropic messages API | Dedicated client. |
| `gemini` | OpenAI-compatible endpoint on Google | API-key auth. |
| `mimo` | Xiaomi MiMo native | `kt login mimo`. |

Extra community providers referenced in configs:
`together`, `mistral`, `deepseek`, `vllm`, `generic`. See
`kohakuterrarium.llm.presets` for the canonical list.

## LLM presets

Shipped in `src/kohakuterrarium/llm/presets.py`. Use them as `llm:` or
`--llm` values. Aliases listed in parentheses.

### OpenAI via Codex OAuth

- `gpt-5.4` (alias: `gpt5`, `gpt54`)
- `gpt-5.3-codex` (`gpt53`)
- `gpt-5.1`
- `gpt-4o` (`gpt4o`)
- `gpt-4o-mini`

### OpenAI direct

- `gpt-5.4-direct`
- `gpt-5.4-mini-direct`
- `gpt-5.4-nano-direct`
- `gpt-5.3-codex-direct`
- `gpt-5.1-direct`
- `gpt-4o-direct`
- `gpt-4o-mini-direct`

### OpenAI via OpenRouter

- `or-gpt-5.4`
- `or-gpt-5.4-mini`
- `or-gpt-5.4-nano`
- `or-gpt-5.3-codex`
- `or-gpt-5.1`
- `or-gpt-4o`
- `or-gpt-4o-mini`

### Anthropic Claude via OpenRouter

- `claude-opus-4.6` (aliases: `claude-opus`, `opus`)
- `claude-sonnet-4.6` (aliases: `claude`, `claude-sonnet`, `sonnet`)
- `claude-sonnet-4.5`
- `claude-haiku-4.5` (aliases: `claude-haiku`, `haiku`)
- `claude-sonnet-4` (legacy)
- `claude-opus-4` (legacy)

### Anthropic Claude direct

- `claude-opus-4.6-direct`
- `claude-sonnet-4.6-direct`
- `claude-haiku-4.5-direct`

### Google Gemini

Via OpenRouter:

- `gemini-3.1-pro` (aliases: `gemini`, `gemini-pro`)
- `gemini-3-flash` (`gemini-flash`)
- `gemini-3.1-flash-lite` (`gemini-lite`)
- `nano-banana`

Direct (OpenAI-compat endpoint):

- `gemini-3.1-pro-direct`
- `gemini-3-flash-direct`
- `gemini-3.1-flash-lite-direct`

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

### Xiaomi MiMo

Via OpenRouter:

- `mimo-v2-pro` (`mimo`)
- `mimo-v2-flash`

Direct:

- `mimo-v2-pro-direct`
- `mimo-v2-flash-direct`

### GLM (Z.ai, via OpenRouter)

- `glm-5` (`glm`, via default alias)
- `glm-5-turbo` (`glm`)

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

Built-in preset merging also picks up `llm_presets` contributed by
installed packages; see
[configuration.md — Package manifest](configuration.md#package-manifest-kohakuyaml).

---

## Prompt plugins

Shipped prompt plugins (loaded by `prompt/aggregator.py`). Ordered by
priority (lower = earlier).

| Priority | Name | Emits |
|---|---|---|
| 50 | `ToolListPlugin` | Tool name + one-line description. |
| 45 | `FrameworkHintsPlugin` | Framework command examples (`info`, `read_job`, `jobs`, `wait`) and tool-call format examples. |
| 40 | `EnvInfoPlugin` | `cwd`, platform, date/time. |
| 30 | `ProjectInstructionsPlugin` | Loads `CLAUDE.md` and `.claude/rules.md`. |

Custom prompt plugins subclass `BasePlugin` and register via the
`plugins` field in a creature config. See
[plugin-hooks.md](plugin-hooks.md) for lifecycle and callback hooks.

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
`tools`, `plugins`, `llm_presets`, and `python_dependencies`.
`kt extension list` inventories them. Python modules resolve by
`module:class` refs; configs resolve via `@pkg/path`. See
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
