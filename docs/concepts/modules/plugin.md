# Plugin

## What it is

A **plugin** modifies the *connections between modules*, not the
modules themselves. Modules are the blocks. Plugins are what runs at
the seams.

There are two flavours, each solving a different problem:

- **Prompt plugins** contribute content to the system prompt when the
  controller builds it.
- **Lifecycle plugins** hook into runtime events — before/after an LLM
  call, before/after a tool call, before/after a sub-agent spawn.

Together, plugins are the main way to add behaviour *without forking
any module*.

## Why it exists

Most useful agent behaviours are not a new tool and not a new LLM —
they are a rule that runs between them. Examples:

- "Before every bash call, check it against a safety policy."
- "After every LLM call, count tokens for billing."
- "Before every LLM call, retrieve relevant past events and inject
  them into the messages."
- "Always prepend a project-specific instruction section to the system
  prompt."

Each of these could be done by subclassing a module. That is invasive
and fragile — you fork, someone upstream ships a change, you rebase.
Plugins let you hook the seams without touching the blocks.

## How we define it

### Prompt plugins

A `BasePlugin` subclass with:

- a `name` and `priority` (lower = earlier in the prompt),
- a `get_content(context) → str | None` that returns a section of
  prompt text (or `None` to contribute nothing).

The aggregator (`prompt/aggregator.py`) sorts registered plugins by
priority and concatenates their outputs into the final system prompt.

Built-ins: `ToolListPlugin` (auto tool index), `FrameworkHintsPlugin`
(how to call tools / use `##commands##`), `EnvInfoPlugin` (working
dir, date, platform), `ProjectInstructionsPlugin` (loads
`CLAUDE.md` / `.claude/rules.md`).

### Lifecycle plugins

A `BasePlugin` subclass with any of these hooks:

- `on_load(context)`, `on_unload()`
- `pre_llm_call(messages, **kwargs) → list[dict] | None`
- `post_llm_call(response) → ChatResponse | None`
- `pre_tool_execute(name, args) → dict | None`
- `post_tool_execute(name, result) → ToolResult | None`
- `pre_subagent_run(name, context) → dict | None`
- `post_subagent_run(name, output) → str | None`
- Fire-and-forget: `on_tool_start`, `on_tool_end`, `on_llm_start`,
  `on_llm_end`, `on_processing_start`, `on_processing_end`,
  `on_startup`, `on_shutdown`, `on_compact_start`,
  `on_compact_complete`, `on_event`.

A `pre_*` hook can raise `PluginBlockError("message")` to abort the
operation — the message becomes the tool result or a blocked
`tool_complete` event.

## How we implement it

`PluginManager.notify(hook, **kwargs)` iterates registered, enabled
plugins and awaits each matching method. `bootstrap/plugins.py` loads
config-declared plugins on agent start; package-declared plugins are
discoverable via `kohaku.yaml`.

## What you can therefore do

- **Safety guards.** A `pre_tool_execute` plugin that rejects dangerous
  commands.
- **Token accounting.** `post_llm_call` counting tokens and writing to
  an external store.
- **Seamless memory.** `pre_llm_call` running an embedding lookup over
  past events and prepending relevant context — essentially RAG over
  session history without tool calls.
- **Smart guard.** A `pre_tool_execute` plugin that runs a small
  *nested agent* to decide whether the action is acceptable. Plugins
  are Python, and agents are Python, so this is legal. See
  [patterns](../patterns.md).
- **Prompt composition.** A prompt plugin that injects dynamic
  instructions derived from scratchpad state or session metadata.

## Don't be bounded

Plugins are optional. A creature with no plugins works fine. But when
you find yourself thinking "I need a new kind of behaviour everywhere
in the loop," the answer is almost always a plugin, not a new module.

## See also

- [Controller](controller.md) — where the hooks fire.
- [Prompt aggregation](../impl-notes/prompt-aggregation.md) — how prompt plugins slot in.
- [Patterns — smart guard, seamless memory](../patterns.md) — agent-inside-plugin.
- [reference/plugin-hooks.md](../../reference/plugin-hooks.md) — every hook signature.
