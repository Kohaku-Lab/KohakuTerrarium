---
title: Plugin hooks
summary: Every lifecycle hook plugins can register, when it fires, and what payload it receives.
tags:
  - reference
  - plugin
  - hooks
---

# Plugin hooks

Every lifecycle, LLM, tool, sub-agent, and callback hook exposed to
plugins. Hooks are defined by the `Plugin` protocol in
`kohakuterrarium.modules.plugin`; `BasePlugin` gives you default
no-op implementations. Wired in `bootstrap/plugins.py`.

For the mental model, read [concepts/modules/plugin](../concepts/modules/plugin.md).
For task-oriented walkthroughs, see
[guides/plugins](../guides/plugins.md) and
[guides/custom-modules](../guides/custom-modules.md).

## Return-value semantics

- **Transform hooks** (`pre_*`, `post_*`): return `None` to keep the
  value unchanged, or return a new value to replace the input going
  into the next plugin / the framework.
- **Callback hooks** (`on_*`): return value is ignored; they are
  fire-and-forget.

## Blocking

Any `pre_*` hook may raise `PluginBlockError` to short-circuit the
operation. The framework surfaces the error, the request does not
proceed, and the matching `post_*` hook is **not** fired. Callback
hooks cannot block.

---

## Lifecycle hooks

| Hook | Signature | Fired when | Return |
|---|---|---|---|
| `on_load` | `async on_load(ctx: PluginContext) -> None` | Plugin is loaded into an agent. | ignored |
| `on_unload` | `async on_unload() -> None` | Plugin is unloaded or agent stops. | ignored |
| `should_apply` | `def should_apply(ctx: PluginContext) -> bool` | Evaluated before each hook call. | `False` skips this plugin for that context. |
| `contribute_commands` | `def contribute_commands() -> dict[str, BaseCommand]` | After load, during controller wiring. | Mapping of controller command names. |
| `contribute_termination_check` | `def contribute_termination_check() -> Callable[[TerminationContext], TerminationDecision \| None] \| None` | During termination wiring. | Checker function or `None`. |

`PluginContext` gives the plugin access to the host agent, session store,
scratchpad, registry, controller, subagent manager, and helper methods like
`switch_model(...)`, `inject_event(...)`, and `inject_message_before_llm(...)`.
Plugins can also declare a static filter with `applies_to = {agent_names, model_patterns}`.

---

## LLM hooks

| Hook | Signature | Fired when | Return semantics |
|---|---|---|---|
| `pre_llm_call` | `async pre_llm_call(messages: list[dict], **kwargs) -> list[dict] \| None` | Before every LLM request (controller, sub-agent, compact). | `None` keeps the list; a new list replaces it. May raise `PluginBlockError`. |
| `post_llm_call` | `async post_llm_call(messages: list[dict], response: str, usage: dict, **kwargs) -> str \| None` | After the final assistant message is assembled. | `None` keeps the text; a returned string rewrites it for the next plugin / final output. |

When a `post_llm_call` rewrite changes the final assistant text, the runtime emits an `assistant_message_edited` activity marker so UIs can audit that rewrite.

---

## Tool visibility contributions

Plugins can restrict the catalog exposed to the model without touching
the registry. `PluginManager` intersects multiple contributions per
category, so each plugin can only narrow the catalog.

| Hook | Signature | Fired when | Return semantics |
|---|---|---|---|
| `get_tool_visibility` | `def get_tool_visibility(context: PluginContext) -> ToolVisibility \| None` | Before each native request's tool schemas and provider-native tools are built, and whenever the system prompt is assembled. | `None` keeps the catalog unrestricted. A `ToolVisibility(allowed_tools=..., allowed_subagents=...)` keeps only the named members; an empty `frozenset` hides the whole category. |

`ToolVisibility` fields:

- `allowed_tools: frozenset[str] | None` — `None` means unrestricted.
- `allowed_subagents: frozenset[str] | None` — `None` means unrestricted.

The resulting catalog is intersected with renderer availability (tools
declaring `requires_renderers` are hidden while no attached output reports
those renderers). It governs the native schemas and every prompt section
that names tools: `## Available Functions`, `## Function Documentation`,
`## Tool guidance`, call examples and the `{{ tools }}` template variable.
The prompt is assembled at construction and on refresh, so a contribution
that changes from request to request only reaches the prompt on the next
refresh.

## Tool contributions

| Hook | Signature | Fired when | Return semantics |
|---|---|---|---|
| `contribute_tools` | `def contribute_tools() -> list[BaseTool]` | Once per plugin instance, when the agent is built or the plugin is added, enabled or loaded. | The tools are registered while the plugin is enabled and removed when it is disabled or unregistered. A name already taken raises `PluginToolConflictError`. |

---

## Tool hooks

| Hook | Signature | Fired when | Return semantics |
|---|---|---|---|
| `pre_tool_dispatch` | `async pre_tool_dispatch(call: ToolCallEvent, context: PluginContext) -> ToolCallEvent \| None` | After parsing, before executor submission. | `None` keeps the call; a returned event rewrites tool name/args. May raise `PluginBlockError`. |
| `pre_tool_execute` | `async pre_tool_execute(args: dict, **kwargs) -> dict \| None` | Just before tool execution. | `None` keeps `args`; a new dict replaces them. May raise `PluginBlockError`. `kwargs` include `tool_name`, `job_id`. |
| `post_tool_execute` | `async post_tool_execute(result: ToolResult, **kwargs) -> ToolResult \| None` | After a tool completes (including error results). | `None` keeps the result; a new `ToolResult` replaces it. `kwargs` include `tool_name`, `job_id`, `args`. |

---

### Fork authorization policies

Parent plugins do not automatically become child plugins. A plugin may set
`inherit_fork_policy = True` (default: `False`) to contribute a pre-tool
authorization guard to context forks. `fork_tool_policy() -> Callable[..., Any]`
returns the guard for one child; the default implementation returns
`self.pre_tool_execute`. Stateful policies should override this factory to
allocate child-private state rather than mutate the parent.

The runtime checks the parent's live enabled/applicable plugin set for every
child tool call, after the child's argument transformations. The guard receives
`args`, `tool_name`, and execution keyword arguments and may be sync or async.
Return `None` or unchanged arguments to allow execution; raise `PluginBlockError`
to deny it. Argument mutations, rewritten return values, and unexpected guard
exceptions block the call. These inherited guards authorize, not transform.
If the applicable parent policy identities or options change while an async
approval is pending, the invocation is blocked with a retry error rather than
executed under stale approval. Parent budget accounting, context hooks, and
lifecycle hooks are not inherited.

The builtin `sandbox` and `permgate` opt in. `permgate` honors live parent policy
and existing parent approvals, but new approvals granted within a fork stay
private to that child; they do not approve tools for the parent or sibling forks.
This is an authorization contract for tools the guards understand, not an
OS-level sandbox or automatic containment of arbitrary custom tool code.

---

## Sub-agent hooks

| Hook | Signature | Fired when | Return semantics |
|---|---|---|---|
| `pre_subagent_run` | `async pre_subagent_run(task: str, **kwargs) -> str \| None` | Before a sub-agent is spawned and started. | `None` keeps the task; a returned string replaces it. May raise `PluginBlockError`. `kwargs` include `name`, `job_id`, `is_background`. |
| `post_subagent_run` | `async post_subagent_run(result: Any, **kwargs) -> Any \| None` | After a sub-agent completes (its output is about to be delivered as a `subagent_output` event). | `None` keeps the result; a returned value replaces it. `kwargs` include `name`, `job_id`. |

---

## Callback hooks

All callbacks are fire-and-forget. Their return value is ignored.

| Hook | Signature | Fired when |
|---|---|---|
| `on_agent_start` | `async on_agent_start() -> None` | `agent.start()` completed. |
| `on_agent_stop` | `async on_agent_stop() -> None` | `agent.stop()` begins. |
| `on_event` | `async on_event(event: TriggerEvent) -> None` | Any event is injected into the controller. |
| `on_interrupt` | `async on_interrupt() -> None` | The user interrupts the agent. |
| `on_task_promoted` | `async on_task_promoted(job_id: str, tool_name: str) -> None` | A direct task is promoted to background. |
| `on_compact_start` | `async on_compact_start(context_length: int) -> bool \| None` | Before compaction. Return `False` to veto this compaction cycle. |
| `on_compact_end` | `async on_compact_end(summary: str, messages_removed: int) -> None` | After compaction finishes. |

---

## Prompt contributions

`BasePlugin` (in `kohakuterrarium.modules.plugin.base`) can also add prose
to the system prompt that `prompt/aggregator.py` assembles:

```python
priority: int       # lower = earlier
name: str
prompt_renderers: frozenset[str]   # renderer ids whose attach/detach changes the text
def get_prompt_content(self, context: PluginContext) -> str | None
def presentation_contribution(self, context: PluginContext) -> str | None
```

- `get_prompt_content(context) -> str | None`: Return the text block to
  insert, or `None` to contribute nothing.
- `presentation_contribution(context) -> str | None`: Return this plugin's
  part of `## Presenting your answer`, or `None`.
- `priority`: ordering key among plugin contributions.

The framework's own sections are listed in
[Prompt sections in builtins.md](builtins.md#prompt-sections). Custom
plugins register via the `plugins` field of a creature config, the same as
any other plugin.

---

## Writing a plugin

Minimal lifecycle plugin:

```python
from kohakuterrarium.modules.plugin import BasePlugin, PluginBlockError

class GuardPlugin(BasePlugin):
    async def pre_tool_execute(self, args, **kwargs):
        if kwargs.get("tool_name") == "bash" and "rm -rf" in args.get("command", ""):
            raise PluginBlockError("unsafe command")
        return None  # keep args unchanged
```

Register in a creature config:

```yaml
plugins:
  - name: guard
    type: custom
    module: ./plugins/guard.py
    class: GuardPlugin
```

Enable/disable at runtime via `/plugin toggle guard` (see
[User commands in builtins.md](builtins.md#user-commands)) or the HTTP
plugin toggle endpoint.

---

## See also

- Concepts:
  [plugin](../concepts/modules/plugin.md),
  [patterns](../concepts/patterns.md).
- Guides:
  [plugins](../guides/plugins.md),
  [custom modules](../guides/custom-modules.md).
- Reference: [python](python.md), [configuration](configuration.md),
  [builtins](builtins.md).
