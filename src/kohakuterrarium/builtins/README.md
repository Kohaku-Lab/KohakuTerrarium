# builtins/ (Built-in Components)

Ready-to-use tools, sub-agents, inputs, outputs, terminal UIs, and user
commands that ship with the framework. Agents reference these by name in
their `config.yaml`.

## Tools (30 registered names across 21 implementation files)

Core general-purpose tools:

| Name | Description |
|------|-------------|
| `bash` | Execute shell commands (auto-detects platform shell) |
| `python` | Execute Python code via subprocess |
| `read` | Read file contents with optional line range |
| `write` | Create or overwrite files |
| `edit` | Single-diff edit of a file |
| `multi_edit` | Atomic or policy-driven batch of ordered search/replace edits on one file |
| `glob` | Find files by glob pattern |
| `grep` | Search file contents with regex and type filtering |
| `tree` | List directory structure with frontmatter summaries |
| `scratchpad` | Session-scoped key-value working memory |
| `ask_user` | Request human input mid-execution |
| `json_read` / `json_write` | Read and modify JSON files with path expressions |
| `send_message` | Send a message to a named channel |
| `info` | Load full documentation for a tool or sub-agent on demand |
| `stop_task` | Cancel a running background tool, sub-agent, or trigger by id |
| `add_timer` / `watch_channel` / `add_schedule` | Setup-able trigger tools (opt-in via `type: trigger`) — see `modules/trigger/callable.py` |
| `search_memory` | FTS5 + semantic search over the current session's event log |
| `web_fetch` | Fetch and clean a web page (crawl4ai → trafilatura → jina → naive fallback) |
| `web_search` | DuckDuckGo web search (optional dep) |

Terrarium management tools (registered lazily via `terrarium/tool_registration`,
intended for a root agent):

| Name | Description |
|------|-------------|
| `terrarium_create` | Create and start a new terrarium from a config path |
| `terrarium_status` | Read status (creatures, channels, running state) |
| `terrarium_stop` | Stop a running terrarium |
| `terrarium_send` | Send a message to a channel inside a terrarium |
| `terrarium_observe` | Non-destructively observe a channel |
| `terrarium_history` | Read a channel's message history |
| `creature_start` / `creature_stop` / `creature_interrupt` | Lifecycle control for individual creatures |

MCP meta-tools (registered from `mcp/tools.py`): `mcp_list`, `mcp_call`,
`mcp_connect`, `mcp_disconnect` — see `../mcp/README.md`.

## Sub-agents (10)

| Name | Description |
|------|-------------|
| `explore` | Search and explore codebase (read-only) |
| `plan` | Create implementation plans (read-only) |
| `worker` | Implement code changes, fix bugs, refactor (read-write) |
| `critic` | Review and critique code, plans, or outputs |
| `summarize` | Summarize long content into concise summaries |
| `research` | Research topics using files and web access |
| `coordinator` | Coordinate multiple agents via channels |
| `memory_read` | Search and retrieve from memory |
| `memory_write` | Store information to memory (can create files) |
| `response` | Generate user-facing responses (output sub-agent) |

## Inputs and Outputs

**Inputs:** `cli` (blocking and non-blocking), `tui` (shared TUI session),
`none` (trigger-only, no input). Audio/ASR examples live under `examples/` and
can be loaded explicitly as custom modules.

**Outputs:** `stdout` (plain and prefixed), `tui` (shared TUI session),
`tts` (console TTS with config).

## Subpackages

| Package | Purpose |
|---------|---------|
| `tools/` | 21 tool implementation files, 30 registered tool names |
| `subagents/` | 10 sub-agent configs |
| `inputs/` | CLI, TUI registry, None input modules |
| `outputs/` | Stdout, TTS output modules |
| `tui/` | Full-screen Textual terminal UI (session, input, output, widgets) |
| `cli_rich/` | Inline rich CLI app (prompt_toolkit + rich) — see `cli_rich/README.md` |
| `user_commands/` | Slash commands (`/help`, `/clear`, `/compact`, `/model`, `/plugin`, `/regen`, `/status`, `/exit`) |

## Catalogs

Tool and sub-agent registration logic lives in catalog modules at the
builtins root:

| File | Purpose |
|------|---------|
| `tool_catalog.py` | `register_builtin`, `get_builtin_tool`, `list_builtin_tools`, `is_builtin_tool` |
| `subagent_catalog.py` | `get_builtin_subagent_config`, `list_builtin_subagents` |

## Usage

```python
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool
from kohakuterrarium.builtins.subagent_catalog import get_builtin_subagent_config

tool = get_builtin_tool("bash")
config = get_builtin_subagent_config("explore")
```

## Adding Custom Builtins

```python
from kohakuterrarium.builtins.tool_catalog import register_builtin
from kohakuterrarium.modules.tool.base import BaseTool, ToolResult

@register_builtin("my_tool")
class MyTool(BaseTool):
    @property
    def tool_name(self) -> str: return "my_tool"
    @property
    def description(self) -> str: return "Does something useful"
    async def execute(self, args: dict) -> ToolResult:
        return ToolResult(output="done")
```

## See also

- `tools/README.md` — per-file breakdown of tool implementations
- `cli_rich/README.md` — rich inline CLI architecture
- `user_commands/README.md` — slash command layer and UI payloads
- `../modules/tool/` — `BaseTool`, `ToolResult`, `ExecutionMode`
