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

`PluginContext` gives the plugin access to the agent, its config,
scratchpad, and a logger. Detailed shape is in
`kohakuterrarium.modules.plugin.context`.

---

## LLM hooks

| Hook | Signature | Fired when | Return semantics |
|---|---|---|---|
| `pre_llm_call` | `async pre_llm_call(messages: list[dict], **kwargs) -> list[dict] \| None` | Before every LLM request (controller, sub-agent, compact). | `None` keeps the list; a new list replaces it. May raise `PluginBlockError`. |
| `post_llm_call` | `async post_llm_call(response: ChatResponse) -> ChatResponse \| None` | After an LLM response is assembled. | `None` keeps the response; a new `ChatResponse` replaces it. |

---

## Tool hooks

| Hook | Signature | Fired when | Return semantics |
|---|---|---|---|
| `pre_tool_execute` | `async pre_tool_execute(name: str, args: dict) -> dict \| None` | Before a tool is dispatched to the executor. | `None` keeps `args`; a new dict replaces them. May raise `PluginBlockError`. |
| `post_tool_execute` | `async post_tool_execute(name: str, result: ToolResult) -> ToolResult \| None` | After a tool completes (including error results). | `None` keeps the result; a new `ToolResult` replaces it. |

---

## Sub-agent hooks

| Hook | Signature | Fired when | Return semantics |
|---|---|---|---|
| `pre_subagent_run` | `async pre_subagent_run(name: str, ctx: SubAgentContext) -> dict \| None` | Before a sub-agent is spawned and started. | `None` keeps the spawn context; a dict merges overrides. May raise `PluginBlockError`. |
| `post_subagent_run` | `async post_subagent_run(name: str, output: str) -> str \| None` | After a sub-agent completes (its output is about to be delivered as a `subagent_output` event). | `None` keeps the output; a new string replaces it. |

---

## Callback hooks

All callbacks are fire-and-forget. Their return value is ignored. They
run concurrently via the plugin scheduler; slow callbacks do not block
the agent.

| Hook | Signature | Fired when |
|---|---|---|
| `on_tool_start` | `async on_tool_start(name: str, args: dict) -> None` | Tool execution is about to begin. |
| `on_tool_end` | `async on_tool_end(name: str, result: ToolResult) -> None` | Tool execution completed. |
| `on_llm_start` | `async on_llm_start(messages: list[dict]) -> None` | LLM request sent. |
| `on_llm_end` | `async on_llm_end(response: ChatResponse) -> None` | LLM response received. |
| `on_processing_start` | `async on_processing_start() -> None` | Agent enters a processing turn. |
| `on_processing_end` | `async on_processing_end() -> None` | Agent exits a processing turn. |
| `on_startup` | `async on_startup() -> None` | Agent `start()` completed. |
| `on_shutdown` | `async on_shutdown() -> None` | Agent `stop()` is running. |
| `on_compact_start` | `async on_compact_start(reason: str) -> None` | Compaction begins. |
| `on_compact_complete` | `async on_compact_complete(summary: str) -> None` | Compaction finishes. |
| `on_event` | `async on_event(event: TriggerEvent) -> None` | Any event is injected into the controller. |

---

## Prompt plugins (separate category)

Prompt plugins run during system prompt assembly in
`prompt/aggregator.py`. They are loaded independently from lifecycle
plugins.

`BasePlugin` (in `kohakuterrarium.prompt.plugins`) has:

```python
priority: int       # lower = earlier
name: str
async def get_content(self, context: PromptContext) -> str | None
```

- `get_content(context) -> str | None` — Return the text block to
  insert, or `None` to contribute nothing.
- `priority` — ordering key. Built-ins sit at 50/45/40/30.

Built-in prompt plugins are listed in
[builtins.md — Prompt plugins](builtins.md#prompt-plugins).

Register custom prompt plugins via the `plugins` field of a creature
config (same as lifecycle plugins); the framework dispatches based on
whether a plugin class subclasses the lifecycle `Plugin` protocol or
the prompt `BasePlugin`.

---

## Writing a plugin

Minimal lifecycle plugin:

```python
from kohakuterrarium.modules.plugin import BasePlugin, PluginBlockError

class GuardPlugin(BasePlugin):
    async def pre_tool_execute(self, name, args):
        if name == "bash" and "rm -rf" in args.get("command", ""):
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
[builtins.md — User commands](builtins.md#user-commands)) or the HTTP
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
