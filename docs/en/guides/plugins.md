---
title: Plugins
summary: Prompt plugins and lifecycle plugins — what each hooks, how they compose, and when to use them.
tags:
  - guides
  - plugin
  - extending
---

# Plugins

For readers adding behaviour at the *seams* between modules without forking any module.

Plugins modify the connections between controller, tools, sub-agents, and LLM — not the modules themselves. Two flavours: **prompt plugins** contribute to the system prompt, **lifecycle plugins** hook into runtime events (pre/post LLM, pre/post tool dispatch/execution, sub-agent runs, compaction, interrupts, and more).

Concept primer: [plugin](../concepts/modules/plugin.md), [patterns](../concepts/patterns.md).

## When to write a plugin vs a tool vs a module

- A *tool* is a thing the LLM can call by name.
- A *module* (input/output/trigger/sub-agent) is a whole runtime surface.
- A *plugin* is a rule that runs *between* them — guard, accounting, prompt injection, memory retrieval.

If your answer is "before/after every X, do Y," the answer is almost always a plugin.

## Prompt plugins

Contract:

- Subclass `BasePlugin`.
- Set `name`, `priority` (lower = earlier in the final prompt).
- Implement `get_content(context) -> str | None`.

```python
# plugins/project_header.py
from kohakuterrarium.modules.plugin.base import BasePlugin


class ProjectHeaderPlugin(BasePlugin):
    name = "project_header"
    priority = 35          # before ProjectInstructionsPlugin (30)

    def __init__(self, text: str = ""):
        super().__init__()
        self.text = text

    def get_content(self, context) -> str | None:
        if not self.text:
            return None
        return f"## Project Header\n\n{self.text}"
```

Built-in prompt plugins (always present):

| Plugin | Priority | Purpose |
|---|---|---|
| `ProjectInstructionsPlugin` | 30 | Loads `CLAUDE.md` / `.claude/rules.md` |
| `EnvInfoPlugin` | 40 | Working dir, platform, date |
| `FrameworkHintsPlugin` | 45 | Tool-call syntax + framework command examples (`info`, `jobs`, `wait`, native tool usage) |
| `ToolListPlugin` | 50 | One-line description per tool |

Runtime plugins can also contribute prompt prose by implementing
`get_prompt_content(context) -> str | None`. Those contributions land in the
aggregated prompt before framework hints and work for both parent agents and
sub-agents.

Separate from prompt plugins, tools themselves can now contribute short
prompt-guidance paragraphs via `prompt_contribution()`. Those land in the
aggregated prompt before the framework hints.

Lower priority runs earlier. Pick your plugin's priority to slot it correctly.

## Lifecycle plugins

Subclass `BasePlugin` and implement any of these hooks. All are async unless noted.

| Hook | Signature | Effect |
|---|---|---|
| `on_load(context)` | setup at agent start | — |
| `on_unload()` | teardown at stop | — |
| `should_apply(context)` | return `bool` | Dynamic per-agent/model gating |
| `pre_llm_call(messages, **kwargs)` | return `list[dict] \| None` | Replace messages sent to LLM |
| `post_llm_call(messages, response, usage, **kwargs)` | return `str \| None` | Rewrite final assistant text |
| `pre_tool_dispatch(call, context)` | return rewritten call or raise `PluginBlockError` | Rewrite or veto a parsed tool call before executor submission |
| `pre_tool_execute(args, **kwargs)` | return `dict \| None`; or raise `PluginBlockError` | Replace args or block the call |
| `post_tool_execute(result, **kwargs)` | return `ToolResult \| None` | Replace tool result |
| `pre_subagent_run(task, **kwargs)` | return `str \| None` | Replace sub-agent task text |
| `post_subagent_run(result, **kwargs)` | return result or `None` | Replace sub-agent result |
| `contribute_commands()` | return `dict[name, BaseCommand]` | Add controller `##command##` handlers |
| `contribute_termination_check()` | return callable or `None` | Vote on whether the run should stop |

Fire-and-forget callbacks (no return value, no ability to modify):

- `on_agent_start`, `on_agent_stop`
- `on_event`, `on_interrupt`, `on_task_promoted`
- `on_compact_start`, `on_compact_end`

You can also declare a cheap static gate with `applies_to = {agent_names: [...], model_patterns: [...]}`.

## Example: tool guard

Blocks dangerous shell commands.

```python
# plugins/tool_guard.py
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError


class ToolGuard(BasePlugin):
    name = "tool_guard"

    def __init__(self, deny_patterns: list[str]):
        super().__init__()
        self.deny_patterns = deny_patterns

    async def pre_tool_execute(self, name: str, args: dict) -> dict | None:
        if name != "bash":
            return None
        command = args.get("command", "")
        for pat in self.deny_patterns:
            if pat in command:
                raise PluginBlockError(f"Blocked by tool_guard: {pat!r}")
        return None
```

Config:

```yaml
plugins:
  - name: tool_guard
    type: custom
    module: ./plugins/tool_guard.py
    class: ToolGuard
    options:
      deny_patterns: ["rm -rf /", "dd if=/dev/zero"]
```

Raising `PluginBlockError` aborts the operation — the message becomes the tool result.

## Example: token accounting

```python
class TokenAccountant(BasePlugin):
    name = "token_accountant"

    async def post_llm_call(self, messages, response, usage, **kwargs):
        my_db.record(
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
            cached=usage.get("cached_tokens", 0),
        )
        return None   # don't rewrite the response
```

## Example: seamless memory (agent inside plugin)

A `pre_llm_call` plugin that retrieves relevant past events and prepends them to the messages. You can call a small nested agent to decide what's relevant — plugins are plain Python, so agents are legal inside them. See [concepts/python-native/agent-as-python-object](../concepts/python-native/agent-as-python-object.md).

## Built-in runtime plugins

Sub-agent budgets and auto-compaction are ordinary lifecycle plugins:

| Name | Kind | Use |
|---|---|---|
| `budget` | plugin | Unified turn/tool/walltime budget accounting and enforcement. Configure axes under `options`. |
| `compact.auto` | plugin | Checks usage after LLM turns and triggers the configured compaction manager. |
| `auto-compact` | pack | Expands to `compact.auto`. This is the only built-in runtime pack. |

Use budget + auto-compaction in a parent creature:

```yaml
default_plugins: ["auto-compact"]
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      # walltime_budget: [300, 600]
```

or per sub-agent:

```yaml
subagents:
  - name: reviewer
    type: custom
    system_prompt: "Review the change."
    tools: [read, grep]
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [40, 60]
          tool_call_budget: [75, 100]
```

Builtin sub-agents already use `auto-compact` and the `budget` plugin with
these minimum options. See [Sub-agents](sub-agents.md) for the full guide.

## Managing plugins at runtime

Slash command:

```
/plugin list
/plugin enable tool_guard
/plugin disable tool_guard
/plugin toggle tool_guard
```

Plugins are loaded once at agent start; enable/disable is a runtime flag, not a reload. Re-enabling runs `on_load()` again through the manager's pending-load path. Configuration changes still require a restart.

## Distributing plugins

Bundle into a package:

```yaml
# my-pack/kohaku.yaml
name: my-pack
plugins:
  - name: tool_guard
    module: my_pack.plugins.tool_guard
    class: ToolGuard
```

Consumers enable it in their creature:

```yaml
plugins:
  - name: tool_guard
    type: package
    options: { deny_patterns: [...] }
```

See [Packages](packages.md).

## Hook ordering

When multiple plugins implement the same hook:

- `pre_*` hooks run in registration order; the first to return a non-`None` value wins.
- `post_*` hooks run in registration order, each receiving the previous plugin's output.
- Fire-and-forget hooks all run (errors are logged, not raised).

Raising `PluginBlockError` from any `pre_*` hook short-circuits the operation for all subsequent plugins.

## Testing plugins

```python
from kohakuterrarium.testing.agent import TestAgentBuilder

env = (
    TestAgentBuilder()
    .with_llm_script(["[/bash]@@command=rm -rf /\n[bash/]", "Stopped."])
    .with_builtin_tools(["bash"])
    .with_plugin(ToolGuard(deny_patterns=["rm -rf /"]))
    .build()
)
await env.inject("cleanup")
assert any("Blocked" in act[1] for act in env.output.activities)
```

## Troubleshooting

- **Plugin class not found.** Check `class` (not `class_name` — plugins use `class`). The config loader accepts both, but the package manifest uses `class`.
- **Hook never fires.** Confirm the hook name; typos in `pre_llm_call` vs `pre_tool_execute` silently do nothing.
- **`PluginBlockError` raised but call still executes.** The error was raised from a `post_*` hook. Use `pre_tool_execute` to block.
- **Order-sensitive stacking misbehaves.** `pre_*` hooks run in registration order; rearrange the `plugins:` list in config.

## See also

- [examples/plugins/](../../examples/plugins/) — one example per hook category.
- [Custom Modules](custom-modules.md) — writing the modules plugins hook around.
- [Reference / plugin hooks](../reference/plugin-hooks.md) — every hook signature.
- [Concepts / plugin](../concepts/modules/plugin.md) — design rationale.
