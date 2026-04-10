# Plugin Examples

Educational plugin examples demonstrating the KohakuTerrarium plugin API.
Each file is a standalone, runnable plugin that covers specific hook types.

## Quick Start

Add any plugin to your creature's `config.yaml`:

```yaml
plugins:
  - name: hello
    type: custom
    module: examples.plugins.hello_plugin
    class: HelloPlugin
```

## Examples

| File | Hooks Demonstrated | Difficulty |
|------|--------------------|------------|
| `hello_plugin.py` | `on_load`, `on_agent_start`, `on_agent_stop` | Beginner |
| `tool_timer.py` | `pre_tool_execute`, `post_tool_execute`, state persistence | Beginner |
| `tool_guard.py` | `pre_tool_execute`, `PluginBlockError` | Intermediate |
| `prompt_injector.py` | `pre_llm_call` (message modification) | Intermediate |
| `response_logger.py` | `post_llm_call`, `on_event`, `on_interrupt`, `on_compact_end` | Intermediate |
| `budget_enforcer.py` | `post_llm_call`, `pre_llm_call` (blocking), state persistence | Advanced |
| `subagent_tracker.py` | `pre_subagent_run`, `post_subagent_run`, `on_task_promoted` | Advanced |
| `webhook_notifier.py` | All callbacks, `inject_event`, `switch_model` | Advanced |

## Plugin Hook Reference

### Pre/Post Hooks (pipeline — can transform or block)

- **`pre_llm_call(messages, **kw)`** → modified `messages` or `None`
- **`post_llm_call(messages, response, usage, **kw)`** → observation only
- **`pre_tool_execute(args, **kw)`** → modified `args` or `None` (raise `PluginBlockError` to block)
- **`post_tool_execute(result, **kw)`** → modified `result` or `None`
- **`pre_subagent_run(task, **kw)`** → modified `task` or `None` (raise `PluginBlockError` to block)
- **`post_subagent_run(result, **kw)`** → modified `result` or `None`

### Callbacks (fire-and-forget — observe only)

- **`on_agent_start()`** / **`on_agent_stop()`**
- **`on_event(event)`** — every incoming trigger event
- **`on_interrupt()`** — user pressed Escape
- **`on_task_promoted(job_id, tool_name)`** — direct task → background
- **`on_compact_start(context_length)`** / **`on_compact_end(summary, messages_removed)`**

### PluginContext Methods

- `get_state(key)` / `set_state(key, value)` — persist plugin data in session
- `inject_event(event)` — push a TriggerEvent into the agent's queue
- `switch_model(name)` — change the LLM model at runtime
