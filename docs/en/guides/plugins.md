---
title: Plugins
summary: Plugins hook runtime events and can contribute prompt prose. What they hook, how they compose, and when to use them.
tags:
  - guides
  - plugin
  - extending
---

# Plugins

For readers adding behaviour at the *seams* between modules without forking any module.

Plugins modify the connections between controller, tools, sub-agents, and LLM, not the modules themselves. A plugin is one class, `BasePlugin`. It hooks runtime events (pre/post LLM, pre/post tool dispatch/execution, sub-agent runs, compaction, interrupts, and more) and may also contribute prose to the system prompt.

Concept primer: [plugin](../concepts/modules/plugin.md), [patterns](../concepts/patterns.md).

## When to write a plugin vs a tool vs a module

- A *tool* is a thing the LLM can call by name.
- A *module* (input/output/trigger/sub-agent) is a whole runtime surface.
- A *plugin* is a rule that runs *between* them: guard, accounting, prompt injection, memory retrieval.

If your answer is "before/after every X, do Y," the answer is almost always a plugin.

## Prompt contributions

Contract:

- Subclass `BasePlugin`.
- Set `name` and `priority` (lower = earlier among plugin contributions).
- Implement `get_prompt_content(context) -> str | None`.

```python
# plugins/project_header.py
from kohakuterrarium.modules.plugin.base import BasePlugin


class ProjectHeaderPlugin(BasePlugin):
    name = "project_header"
    priority = 35

    def __init__(self, text: str = ""):
        super().__init__()
        self.text = text

    def get_prompt_content(self, context) -> str | None:
        if not self.text:
            return None
        return f"## Project Header\n\n{self.text}"
```

The prompt aggregator collects these contributions in priority order into
the `## Plugin contributions` position of the system prompt, for both parent
agents and sub-agents. The framework's own sections (tool inventory, tool
guidance, skills, group blocks, execution model) are framework blocks, not
plugins; see [prompt aggregation](../concepts/impl-notes/prompt-aggregation.md).

A plugin that teaches the model a form its answers can take (inline
components, full-page apps) implements
`presentation_contribution(context) -> str | None` instead. The parts are
collected in priority order into `## Presenting your answer`, placed right
after the creature's base prompt; the section is absent when no plugin
returns a part. When a part depends on what the attached clients can draw,
list those renderer ids in the class attribute `prompt_renderers`
(`frozenset({"kt.ui"})`): the prompt is rebuilt when one of them comes or
goes, as it is for tools gated by `requires_renderers`.

Separate from plugins, tools themselves contribute short prompt-guidance
paragraphs via `prompt_contribution()`. Those land in the aggregated
`## Tool guidance` section.

Lower priority runs earlier. Pick your plugin's priority to slot it correctly.

## Lifecycle hooks

Subclass `BasePlugin` and implement any of these hooks. All are async unless noted.

| Hook | Signature | Effect |
|---|---|---|
| `on_load(context)` | setup at agent start | (none) |
| `on_unload()` | teardown at stop | (none) |
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
| `get_tool_visibility(context)` | return `ToolVisibility \| None` | Restrict the tool catalog the model sees, in native schemas and in the prompt (`allowed_tools` / `allowed_subagents`; `None` = unrestricted) |
| `contribute_tools()` | return `list[BaseTool]` | Add tools that exist while the plugin is enabled |

`pre_tool_execute` / `post_tool_execute` receive keyword arguments that
include `tool_name`, `tool` (the tool instance) and, in the main agent,
`job_id` and `context`. Always accept `**kwargs`; new keywords may be added.

Fire-and-forget callbacks (no return value, no ability to modify):

- `on_agent_start`, `on_agent_stop`
- `on_event`, `on_interrupt`, `on_task_promoted`
- `on_compact_start`, `on_compact_end`

You can also declare a cheap static gate with `applies_to = {agent_names: [...], model_patterns: [...]}`.

### Persistent plugin state

Use `context.get_state(key)` and `context.set_state(key, value)` for state that
should survive session resume. New writes are private to the creature name and
plugin name, even when several creatures share one session store. Without an
attached store, reads return `None` and writes are no-ops.

Older sessions used one record per plugin and key for the whole store. If no
creature-scoped record exists, reads still return that legacy value. New writes
leave the legacy record unchanged; an explicitly saved `None` also overrides
the fallback. Legacy records do not identify their original owner, so values
already overwritten by another creature cannot be recovered automatically.

### Contributing tools

`contribute_tools()` returns tool instances that belong to the plugin. It is
called once per plugin instance. The tools join the registry, the executor
and the system prompt when the plugin is loaded or enabled, and leave when it
is disabled or removed; enabling it again restores the same instances.

```python
class AppsPlugin(BasePlugin):
    name = "apps"

    def __init__(self, allowed_hosts: list[str] | None = None):
        super().__init__()
        self.allowed_hosts = allowed_hosts or []

    def contribute_tools(self) -> list[BaseTool]:
        return [ShowAppTool(allowed_hosts=self.allowed_hosts)]
```

A contributed name that is already taken (by a configured tool or by another
plugin) raises `PluginToolConflictError` and the load or enable fails without
changing the registry. A contributed tool can declare `requires_renderers` to
stay hidden until a client that can draw its UI is attached (see
[custom modules](custom-modules.md#tools-that-need-a-ui-renderer)).

### Documentation topics (`info("prefix:topic")`)

A plugin can serve its own documentation through `info`. Set `doc_prefix`
and implement `doc_topics` and `doc_topic`; `info("<prefix>:<topic>")`
returns the topic, from the native `info` tool and the text-format `info`
command alike. `info("<prefix>:")` lists the topics, and an unknown topic
returns an error that lists them.

```python
class GlossaryPlugin(BasePlugin):
    name = "glossary"
    doc_prefix = "gloss"
    TERMS = {"creature": "A configured agent.", "terrarium": "The runtime engine."}

    def doc_topics(self, context):
        return list(self.TERMS)

    def doc_topic(self, topic, context):
        return self.TERMS.get(topic)  # None means "no such topic"

    def get_prompt_content(self, context):
        return "Project terms: call `info` with `gloss:<term>` for a definition."
```

- A prefix is lowercase: a letter, then letters, digits, `_`, `.` or `-`
  (at most 32 characters). Invalid prefixes are ignored with a warning.
- Only enabled, applicable plugins own a prefix. When two plugins claim the
  same prefix, the one that runs first by priority owns it.
- Tell the model about your prefix in your own prompt content: `info` lists
  no plugin topics by itself. Use it to keep rarely needed reference out of
  the always-loaded prompt; `ui.declarative` names its less common
  components and serves their props as `ui:<name>`.

### Pinned context

`context.pin_message(key, role, content)` places a message right after the
system prompt in every model request. `role` is `"user"` or `"system"`;
`content` is a string or a list of content parts, so a pin can carry images.
Pinning an existing key replaces that pin in place; `context.unpin_message(key)`
removes it.

Pins are kept outside the conversation history:

- compaction never summarizes or drops them; after a compaction the request
  is system prompt, pins, summary, recent turns;
- edit, regenerate, rewind, branch switching and `/clear` keep them, and
  message indices used by those operations do not count them;
- they are saved in session state and restored on resume and in forked
  sessions, once;
- forked sub-agents and side conversations inherit them as part of the
  parent's context.

Pins are sent on every request, so keep them small; with provider prompt
caching their fixed position keeps the cached prefix stable.

```python
class Persona(BasePlugin):
    name = "persona"

    async def on_load(self, context: PluginContext) -> None:
        self.ctx = context

    async def on_agent_start(self) -> None:
        self.ctx.pin_message(
            "persona",
            "user",
            [TextPart(text="This is your appearance."), ImagePart(url=data_url)],
        )
```

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

    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        if kwargs.get("tool_name") != "bash":
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

Raising `PluginBlockError` aborts the operation; the message becomes the tool result.

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

A `pre_llm_call` plugin that retrieves relevant past events and prepends them to the messages. You can call a small nested agent to decide what's relevant; plugins are plain Python, so agents are legal inside them. See [concepts/python-native/agent-as-python-object](../concepts/python-native/agent-as-python-object.md).

## Built-in runtime plugins

Four cross-cutting concerns ship as ordinary plugins. None of them
have any special status in the framework; they use the same hooks
your own plugins do. Full reference:
[reference/builtin-plugins](../reference/builtin-plugins.md).

| Name | Kind | Hooks | Use |
|---|---|---|---|
| `sandbox` | plugin | `pre_tool_execute`, `runtime_services` | Hard capability gate: filesystem read / write scope, network allowlist, subprocess syscall level. Capability profile (`PURE` / `READ_ONLY` / `WORKSPACE` / `NETWORK` / `SHELL`) plus per-axis overrides. Hot-reconfigurable. |
| `budget` | plugin | `pre_llm_call`, `post_llm_call`, `pre_tool_execute`, `pre_subagent_run`, `get_prompt_content` | Multi-axis budget accounting and enforcement (turns, tool calls, walltime). Configure axes under `options`. |
| `permgate` | plugin | `pre_tool_execute`, `on_load` | Interactive user approval for tool calls; emits a confirmation prompt to the output bus and waits on the reply. |
| `compact.auto` | plugin | `post_llm_call`, `on_load` | Trigger context compaction after high-token LLM calls. |
| `ui.apps` | plugin | `contribute_tools`, `presentation_contribution` | Adds `show_app`: the agent can show interactive HTML apps in the web UI. See [Chat widgets](chat-widgets.md). |
| `ui.declarative` | plugin | `presentation_contribution`, `pre_llm_call` | Inline UI: the agent writes tables, charts, diagrams and controls in its replies. See [Chat widgets](chat-widgets.md#inline-ui). |
| `auto-compact` | pack | (none) | Expands to `compact.auto`. The only built-in runtime pack. |

Use them in a creature:

```yaml
default_plugins: ["auto-compact"]
plugins:
  - name: sandbox
    options:
      profile: WORKSPACE
      backend: auto                # or "audit" to log without blocking
      network_allowlist: ["api.example.com"]
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

Builtin sub-agents already use `auto-compact` and the `budget` plugin
with these minimum options. See [Sub-agents](sub-agents.md) for the
full guide.

## Worked example: why `sandbox` is a plugin, not a framework feature

Security gating is the kind of concern many frameworks bake in as a
core feature. KohakuTerrarium ships it as an ordinary plugin. The
sandbox plugin is the clearest single illustration of where the
framework / plugin boundary lives, and why keeping the framework
lean works.

### What sandbox actually does

Sandbox declares a **capability profile** for an agent:

| Profile     | fs read | fs write | network | syscall |
|-------------|---------|----------|---------|---------|
| `PURE`      | deny    | deny     | deny    | pure    |
| `READ_ONLY` | broad   | deny     | default | fs      |
| `WORKSPACE` | default | workspace | allow  | fs      |
| `NETWORK`   | deny    | deny     | allow   | default |
| `SHELL`     | default | workspace | allow  | shell  |

Each axis is also overridable individually (`fs_read: deny`,
`fs_write: workspace`, `network: allow`, …) and the plugin accepts
a deny list, a network allowlist, and a list of hard-blocked tool
names. Set `backend: audit` and violations are logged without being
blocked, which is useful for first-pass discovery.

### How sandbox plugs in

Sandbox uses two hooks:

- **`pre_tool_execute`**: runs before every tool call. It inspects
  the args (paths for file tools, URLs for `web_fetch` / `web_search`)
  and raises `PluginBlockError` when a path is denied or a URL is
  off-allowlist.
- **`runtime_services`**: publishes a per-call subprocess runner
  service. Tools that need to spawn subprocesses (`bash`, etc.) read
  this from `ToolContext.runtime_services` and use it instead of
  spawning directly. The runner enforces syscall-level policy
  before delegating to `asyncio.create_subprocess_exec`.

That is all. The framework's tool executor knows nothing about
sandbox semantics. It calls `pre_tool_execute` on every plugin in
priority order; if any plugin raises `PluginBlockError`, the message
becomes the tool result.

### Why this matters

If sandbox were a framework feature instead of a plugin:

- The `Agent` class would carry sandbox-specific fields and special-
  case sandbox checks in the tool executor.
- Composition would be hard. Sandbox + budget + permgate would each
  need their own special-case path.
- Third parties couldn't drop in their own variant (a
  compliance-audit plugin, an approval-by-Slack plugin, an
  organisation-specific permission system) without forking.
- Hot-reconfiguring the policy at runtime would require new framework
  surface area; with the plugin, `refresh_options()` just rebuilds
  the internal capability struct and the next `pre_tool_execute`
  call sees the new policy.

By keeping sandbox at the seam between framework and plugins, all
four built-ins (`sandbox`, `budget`, `permgate`, `compact.auto`)
coexist without any of them being privileged in the framework. They
run in priority order, each returning `None` to pass-through or
raising `PluginBlockError` to veto.

The same logic applies to anything that sounds like a framework
feature but really is a *cross-cutting policy*: rate limiting,
output filtering, content moderation, audit trails, cost tracking.
None of those need to be framework features. All of them want to be
plugins for the same reason sandbox does.

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

- **Plugin class not found.** Check `class` (not `class_name`; plugins use `class`). The config loader accepts both, but the package manifest uses `class`.
- **Hook never fires.** Confirm the hook name; typos in `pre_llm_call` vs `pre_tool_execute` silently do nothing.
- **`PluginBlockError` raised but call still executes.** The error was raised from a `post_*` hook. Use `pre_tool_execute` to block.
- **Order-sensitive stacking misbehaves.** `pre_*` hooks run in registration order; rearrange the `plugins:` list in config.

## See also

- [examples/plugins/](../../examples/plugins/): one example per hook category.
- [Custom Modules](custom-modules.md): writing the modules plugins hook around.
- [Reference / plugin hooks](../reference/plugin-hooks.md): every hook signature.
- [Concepts / plugin](../concepts/modules/plugin.md): design rationale.
