# Prompt aggregation

## The problem this solves

An agent's "system prompt" is not one string. It is a composition of:

- the creature's personality / role,
- a list of available tools (names + descriptions),
- how to actually call tools in this creature's chosen format,
- any channel topology (in a terrarium),
- a description of named outputs (so the LLM knows when to route to
  Discord vs stdout),
- plugin-contributed sections (project rules, environment info, etc.),
- optional full documentation for every tool (if in `static` skill
  mode) — or none of it (if in `dynamic` mode).

If you leave this to hand-written prompts, you ship bugs: stale tool
lists, wrong call syntax, duplicated sections. The framework
assembles the whole thing deterministically.

## Options considered

- **Hand-written prompts.** Fragile. Breaks whenever you add a tool.
- **Always-full static prompts.** Complete but huge — tool docs alone
  can be tens of kilotokens.
- **Load-on-demand docs.** Ship names only; let the agent pull full
  docs via the `info` framework command when needed.
- **Configurable.** Each creature picks the trade-off: `skill_mode:
  dynamic` or `skill_mode: static`. This is the actual choice.

## What we actually do

`prompt/aggregator.py:aggregate_system_prompt(...)` concatenates
sections in this order:

1. **Base prompt.** Rendered with Jinja2 (safe-undefined fallback);
   contains the creature's personality and any project context files
   declared under `prompt_context_files`.
2. **Tool section.**
   - `skill_mode: dynamic` → tool *index*: name + one-line description
     per tool. Agent loads full docs on demand via the `info` framework command.
   - `skill_mode: static` → full documentation for every tool inline.
3. **Channel topology section** (terrarium creatures only). Describes
   "you listen on X, Y; you can send on Z; here is who sits on the
   other side." Emitted by
   `terrarium/config.py:build_channel_topology_prompt`.
4. **Framework hints.** How to call tools in this creature's format
   (bracket / XML / native), how to use the inline framework commands
   (`read_job`, `info`, `jobs`, `wait`), and what the output
   protocol looks like.
5. **Named outputs section.** For each `named_outputs.<name>`, a short
   description of when to route text there.
6. **Prompt plugin sections.** Each registered prompt plugin (priority
   sorted, low→high) contributes one section. Built-ins:
   `ToolListPlugin`, `FrameworkHintsPlugin`, `EnvInfoPlugin`,
   `ProjectInstructionsPlugin`.

MCP tools, when connected, are injected as an extra section under
"Available MCP Tools" with per-server bullet lists.

## Invariants preserved

- **Deterministic.** Given the same config + registry + plugin set,
  the prompt is byte-stable.
- **Auto sections never duplicate hand-written ones.** If you put a
  tool list in your `system.md`, the aggregator's tool list is still
  added; the framework does not deduplicate by content.
- **Skill mode is a knob, not a policy.** Nothing else in the system
  changes based on `skill_mode` — it is exclusively a prompt-size
  trade-off.
- **Plugin order is explicit.** Priority sorted. Same priority → stable
  insertion order.

## Where it lives in the code

- `src/kohakuterrarium/prompt/aggregator.py` — the composition function.
- `src/kohakuterrarium/prompt/plugins.py` — built-in prompt plugins.
- `src/kohakuterrarium/prompt/templates.py` — Jinja safe rendering.
- `src/kohakuterrarium/terrarium/config.py` — channel topology block.
- `src/kohakuterrarium/core/agent.py` — `_init_controller()` calls the
  aggregator once on start.

## See also

- [Plugin](../modules/plugin.md) — writing prompt plugins.
- [Tool](../modules/tool.md) — how tool documentation is registered.
- [reference/configuration.md — skill_mode, tool_format, include_*](../../reference/configuration.md) — the knobs.
