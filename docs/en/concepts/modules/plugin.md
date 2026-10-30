---
title: Plugin
summary: Code that modifies the connections between modules without forking them. One BasePlugin class: lifecycle hooks plus optional prompt contributions.
tags:
  - concepts
  - module
  - plugin
---

# Plugin

## What it is

A **plugin** modifies the *connections between modules*, not the
modules themselves. Modules are the blocks. Plugins are what runs at
the seams.

A plugin is one `BasePlugin` subclass that does either or both of:

- **Hook runtime events**: before/after an LLM call, before/after tool
  dispatch/execution, before/after a sub-agent spawn, and at lifecycle
  checkpoints like compaction and interrupts.
- **Contribute prompt prose**: text the aggregator places in the system
  prompt when the controller builds it.

Plugins are the main way to add behaviour *without forking any module*.

## Why it exists

Most useful agent behaviours are not a new tool and not a new LLM;
they are a rule that runs between them. Examples:

- "Before every bash call, check it against a safety policy."
- "After every LLM call, count tokens for billing."
- "Before every LLM call, retrieve relevant past events and inject
  them into the messages."
- "Always prepend a project-specific instruction section to the system
  prompt."

Each of these could be done by subclassing a module. That is invasive
and fragile: you fork, someone upstream ships a change, you rebase.
Plugins let you hook the seams without touching the blocks.

## How we define it

### Prompt contributions

A `BasePlugin` subclass with:

- a `name` and `priority` (lower = earlier among plugin contributions),
- a `get_prompt_content(context) → str | None` that returns a section of
  prompt text (or `None` to contribute nothing),
- optionally `presentation_contribution(context)` for the
  `## Presenting your answer` section.

The aggregator (`prompt/aggregator.py`) asks the plugin manager for the
contributions in priority order and places them in the system prompt.
The tool inventory, tool guidance, skills and group blocks are framework
sections, not plugins.

### Lifecycle hooks

A `BasePlugin` subclass with any of these hooks:

- `on_load(context)`, `on_unload()`
- `should_apply(context) -> bool` or declarative `applies_to = {agent_names, model_patterns}`
- `contribute_commands()` for custom `##command##` handlers
- `contribute_termination_check()` for pluggable stop conditions
- `pre_llm_call(messages, **kwargs) → list[dict] | None`
- `post_llm_call(messages, response, usage, **kwargs) → str | None`
- `pre_tool_dispatch(call, context) → ToolCallEvent | None`
- `pre_tool_execute(args, **kwargs) → dict | None`
- `post_tool_execute(result, **kwargs) → ToolResult | None`
- `pre_subagent_run(task, **kwargs) → str | None`
- `post_subagent_run(result, **kwargs) → Any | None`
- Fire-and-forget callbacks such as `on_agent_start`, `on_agent_stop`,
  `on_event`, `on_interrupt`, `on_task_promoted`, `on_compact_start`,
  `on_compact_end`.

A `pre_*` hook can raise `PluginBlockError("message")` to abort the
operation; the message becomes the tool result or a blocked
`tool_complete` event. `post_llm_call` rewrites are special: when a
plugin changes the final assistant text, the runtime emits an
`assistant_message_edited` activity marker so UIs can show that the text
was modified after generation.

## How we implement it

`PluginManager` filters plugins by enable/disable state and by
`should_apply(context)` before each hook call. `bootstrap/plugins.py`
loads config-declared plugins on agent start; package-declared plugins
are discoverable via `kohaku.yaml`.

Two newer extension points live alongside the hook surface:

- **Controller commands.** Plugins and packages can register custom
  `##name##` commands into the controller namespace.
- **Termination voters.** Plugins can contribute a checker that receives
  a `TerminationContext`; any checker can stop the run.

## What you can therefore do

- **Safety guards.** A `pre_tool_execute` plugin that rejects dangerous
  commands.
- **Token accounting.** `post_llm_call` counting tokens and writing to
  an external store.
- **Seamless memory.** `pre_llm_call` running an embedding lookup over
  past events and prepending relevant context, essentially RAG over
  session history without tool calls.
- **Smart guard.** A `pre_tool_execute` plugin that runs a small
  *nested agent* to decide whether the action is acceptable. Plugins
  are Python, and agents are Python, so this is legal. See
  [patterns](../patterns.md).
- **Prompt composition.** A plugin whose `get_prompt_content` injects dynamic
  instructions derived from scratchpad state or session metadata.

## Don't be bounded

Plugins are optional. A creature with no plugins works fine. But when
you find yourself thinking "I need a new kind of behaviour everywhere
in the loop," the answer is almost always a plugin, not a new module.

## See also

- [Controller](controller.md): where the hooks fire.
- [Prompt aggregation](../impl-notes/prompt-aggregation.md): how plugin contributions slot in.
- [Smart guard and seamless memory in Patterns](../patterns.md): agent-inside-plugin.
- [reference/plugin-hooks.md](../../reference/plugin-hooks.md): every hook signature.
