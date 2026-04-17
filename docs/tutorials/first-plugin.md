# First Plugin

**Problem:** you need a behaviour that does not belong to any single
module — inject context into every LLM call, or block a tool call
pattern everywhere it appears. A new tool is the wrong shape. A new
output module is the wrong shape. A plugin is the right shape.

**End state:** two working plugins wired into a creature via
`config.yaml`:

1. A **context injector** that adds the current UTC time to every LLM
   call as a short system message.
2. A **tool guard** that blocks any `bash` call containing `rm -rf`
   with an informative error the model can read.

**Prerequisites:** [First Creature](first-creature.md) and ideally
[First Custom Tool](first-custom-tool.md) — you should be comfortable
editing a creature's `config.yaml` and dropping Python files next to it.

A plugin modifies the **connections between modules**, not the modules
themselves. See [plugin concept](../concepts/modules/plugin.md) for
why this boundary exists.

## Step 1 — Pick a folder

Reuse a creature you already have, or make a fresh one:

```text
creatures/tutorial-creature/
  config.yaml
  plugins/
    utc_injector.py
    bash_guard.py
```

```bash
mkdir -p creatures/tutorial-creature/plugins
```

Both plugins below are lifecycle plugins — they subclass
`BasePlugin` from `kohakuterrarium.modules.plugin.base`. That is the
class wired through the `plugins:` section of a creature config.

> Note: the framework also has *prompt plugins*
> (`kohakuterrarium.prompt.plugins.BasePlugin`) that contribute
> sections to the system prompt at build time. They are a lower-level
> primitive and not config-wired. For "add something to every call",
> a `pre_llm_call` lifecycle plugin (as below) is the right on-ramp.

## Step 2 — Write the context-injector plugin

`creatures/tutorial-creature/plugins/utc_injector.py`:

```python
"""Inject current UTC time into every LLM call."""

from datetime import datetime, timezone

from kohakuterrarium.modules.plugin.base import BasePlugin, PluginContext


class UTCInjectorPlugin(BasePlugin):
    name = "utc_injector"
    priority = 90  # Late — run after other pre_llm_call plugins.

    async def on_load(self, context: PluginContext) -> None:
        # Nothing to do here; defined to show the lifecycle hook.
        return

    async def pre_llm_call(
        self, messages: list[dict], **kwargs
    ) -> list[dict] | None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        injection = {
            "role": "system",
            "content": f"[utc_injector] Current UTC time: {now}",
        }

        # Insert after the first system message so the agent's real
        # personality prompt stays first.
        modified = list(messages)
        insert_at = 1
        for i, msg in enumerate(modified):
            if msg.get("role") == "system":
                insert_at = i + 1
                break
        modified.insert(insert_at, injection)
        return modified
```

Notes:

- `pre_llm_call` receives the full `messages` list that will be sent.
  Return a modified list to replace it, or `None` to leave it alone.
- `priority` is an int. Lower runs earlier in `pre_*` hooks, later in
  `post_*` hooks. `90` puts us after the framework's own hooks.
- The `[utc_injector]` prefix is a convention so you can see which
  plugin contributed what when you log messages.

## Step 3 — Write the tool-guard plugin

`creatures/tutorial-creature/plugins/bash_guard.py`:

```python
"""Block `bash` calls that contain dangerous patterns."""

from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)

DANGEROUS_PATTERNS = ("rm -rf",)


class BashGuardPlugin(BasePlugin):
    name = "bash_guard"
    priority = 1  # First — block before anything else runs.

    async def on_load(self, context: PluginContext) -> None:
        return

    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        tool_name = kwargs.get("tool_name", "")
        if tool_name != "bash":
            return None  # Not our concern.

        command = args.get("command", "") or ""
        for pattern in DANGEROUS_PATTERNS:
            if pattern in command:
                raise PluginBlockError(
                    f"bash_guard: blocked — command contains "
                    f"'{pattern}'. Use a safer approach (explicit paths, "
                    f"trash instead of delete)."
                )
        return None  # Allow.
```

Notes:

- `pre_tool_execute` receives `args` and keyword arguments including
  `tool_name` and `job_id`. Filter on `tool_name` before inspecting
  args — this hook fires for *every* tool.
- Raise `PluginBlockError(message)` to abort the call. The message
  becomes the tool result the LLM sees, so make it informative enough
  for the model to choose a different action.
- Return `None` to allow the call unchanged. Return a modified dict
  to rewrite args (e.g. force a safer flag) before execution.

## Step 4 — Wire both into the creature config

`creatures/tutorial-creature/config.yaml`:

```yaml
name: tutorial_creature
version: "1.0"
base_config: "@kt-defaults/creatures/general"

system_prompt_file: prompts/system.md

plugins:
  - name: utc_injector
    type: custom
    module: ./plugins/utc_injector.py
    class: UTCInjectorPlugin

  - name: bash_guard
    type: custom
    module: ./plugins/bash_guard.py
    class: BashGuardPlugin
```

Fields mirror the custom-tool wiring from the previous tutorial:

- `type: custom` — load from a local file.
- `module` — relative to the agent folder.
- `class` — the plugin class to instantiate. (Both `class` and
  `class_name` are accepted.)

Options are passed via `options:` (a dict) and received as
`__init__(self, options=...)`. The examples above take no options, so
the block is omitted.

## Step 5 — Run and confirm

```bash
kt run creatures/tutorial-creature --mode cli
```

### Confirm the injector

Ask the agent a question whose answer depends on the current time:

```text
> what time is it right now, in UTC, to the nearest minute?
```

The creature should answer with a time close to *now* even though it
has no native clock. (If your log level is `DEBUG`, you will see the
injected system message directly.)

### Confirm the guard

Ask the agent to delete something recursively:

```text
> run: rm -rf /tmp/tutorial-test-dir
```

The controller dispatches the tool call, the guard raises
`PluginBlockError`, and the model receives the error text as the tool
result — typically reacting with "I cannot run that" and suggesting an
alternative. No files are touched.

## Step 6 — Know the rest of the hook surface

The two hooks used above are just the most common pair. The full
lifecycle-plugin surface is:

- Lifecycle: `on_load`, `on_unload`, `on_agent_start`, `on_agent_stop`
- LLM: `pre_llm_call`, `post_llm_call`
- Tools: `pre_tool_execute`, `post_tool_execute`
- Sub-agents: `pre_subagent_run`, `post_subagent_run`
- Callbacks: `on_event`, `on_interrupt`, `on_task_promoted`,
  `on_compact_start`, `on_compact_end`

`pre_*` hooks can transform input or raise `PluginBlockError` to
abort. `post_*` hooks can transform results. Callbacks are
fire-and-forget observation. See the
[plugins guide](../guides/plugins.md) for the full signatures and more
examples, and `examples/plugins/` in the repo for worked samples of
every hook.

## What you learned

- A plugin adds behaviour *between* modules — the seams, not the
  blocks. The two most useful hooks are `pre_llm_call` (inject
  context) and `pre_tool_execute` (gate / rewrite).
- `PluginBlockError` is how a plugin says "no" in a way the model can
  read and react to.
- `plugins:` in `config.yaml` wires one the same way `tools:` wires a
  custom tool — `type: custom`, `module:`, `class:`.
- Priority is an int; lower runs earlier in `pre_*`, later in
  `post_*`.

## What to read next

- [Plugin concept](../concepts/modules/plugin.md) — why plugins exist
  and what they unlock, including agent-inside-a-plugin patterns.
- [Plugins guide](../guides/plugins.md) — full hook reference with
  examples.
- [Composing patterns](../concepts/patterns.md) — "smart guard" and
  "seamless memory" patterns that scale these ideas up.
