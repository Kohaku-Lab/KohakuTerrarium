---
title: Built-in plugins
summary: Reference for the four runtime plugins shipped with the framework — sandbox, budget, permgate, compact.auto.
tags:
  - reference
  - plugins
  - builtin
---

# Built-in plugins

Four runtime plugins ship with KohakuTerrarium. None of them are
privileged in the framework — they use the same hooks user plugins
do. They are auto-discovered at agent start; configs activate them
under `plugins:` like any other plugin.

For the framework / plugin boundary lesson and a worked walkthrough
of the sandbox plugin, see
[guides/plugins — Worked example](../guides/plugins.md#worked-example-why-sandbox-is-a-plugin-not-a-framework-feature).

| Name | Priority | Hooks | Implements |
|---|---|---|---|
| [`sandbox`](#sandbox) | 1 | `pre_tool_execute`, `runtime_services`, `on_load` | Filesystem / network / subprocess capability gating. |
| [`budget`](#budget) | 5 | `pre_llm_call`, `post_llm_call`, `pre_tool_execute`, `pre_subagent_run`, `get_prompt_content` | Turn / tool-call / walltime budget accounting. |
| [`compact.auto`](#compactauto) | 30 | `post_llm_call`, `on_load` | Trigger context compaction after high-token LLM calls. |
| [`permgate`](#permgate) | 100 | `pre_tool_execute`, `on_load` | Interactive user approval for tool calls. |

Lower priority runs earlier on `pre_*` hooks. The order above is the
default registration order.

## `sandbox`

Hard capability gate. Restricts what tools and subprocesses can do
without modifying the tools themselves. Implementation:
`src/kohakuterrarium/builtins/plugins/sandbox/plugin.py`. Profile
presets: `src/kohakuterrarium/modules/sandbox/profile.py`.

### Options

| Option | Type | Values | Default | Purpose |
|---|---|---|---|---|
| `enabled` | bool | true / false | true | Master on / off. |
| `backend` | enum | `auto` / `audit` / `off` | `auto` | `auto` = block on violation; `audit` = log only; `off` = no enforcement. |
| `profile` | enum | `PURE` / `READ_ONLY` / `WORKSPACE` / `NETWORK` / `SHELL` | `WORKSPACE` | Capability preset. |
| `fs_read` | enum | `default` / `deny` / `workspace` / `broad` | from profile | File-read scope override. |
| `fs_write` | enum | `default` / `deny` / `workspace` / `broad` | from profile | File-write scope override. |
| `network` | enum | `default` / `deny` / `allow` | from profile | Network access override. |
| `syscall` | enum | `default` / `pure` / `fs` / `shell` / `any` | from profile | Subprocess capability level. |
| `env` | enum | `default` / `filtered` / `inherit` | from profile | Subprocess environment handling. |
| `tmp` | enum | `default` / `private` / `shared` | from profile | Temp directory isolation. |
| `fs_deny` | list[str] | paths | `[]` | Additional path deny-list (env-var expansion supported). |
| `network_allowlist` | list[str] | hostnames | `[]` | Allowed hosts when `network=allow`. Empty list = allow all when network is allowed. |
| `blocked_tools` | list[str] | tool names | `[]` | Tools the agent cannot call regardless of args. |

### Profile presets

| Profile     | fs_read | fs_write   | network | syscall | env      |
|-------------|---------|------------|---------|---------|----------|
| `PURE`      | deny    | deny       | deny    | pure    | filtered |
| `READ_ONLY` | broad   | deny       | default | fs      | default  |
| `WORKSPACE` | default | workspace  | allow   | fs      | default  |
| `NETWORK`   | deny    | deny       | allow   | default | default  |
| `SHELL`     | default | workspace  | allow   | shell   | filtered |

### Behaviour

- **Path scopes** — `default` allows under `cwd`; `workspace` allows
  under `cwd` and rejects parent traversal; `broad` allows anywhere
  except `fs_deny`; `deny` blocks all paths.
- **Network gating** — when `network=allow` and `network_allowlist`
  is set, only listed hosts pass the check; otherwise all hosts pass.
  When `network=deny`, all network tool calls (`web_fetch`,
  `web_search`) raise `PluginBlockError`.
- **Subprocess gating** — the plugin publishes a `subprocess_runner`
  service via `runtime_services()`. Tools that need to spawn
  subprocesses (`bash`, etc.) consume it from
  `ToolContext.runtime_services`. The runner checks syscall level
  (`pure` blocks all spawns, `fs` blocks network calls, `shell`
  allows everything) and the network allowlist before delegating to
  `asyncio.create_subprocess_exec`.
- **`backend=audit`** — violations are logged via the agent logger
  rather than raised. Useful for first-pass discovery on a new
  workload.
- **Hot reconfigure** — calling the plugin's `refresh_options()`
  rebuilds the internal capability struct; subsequent
  `pre_tool_execute` calls see the new policy without restart.

### Example configurations

```yaml
plugins:
  - name: sandbox
    options:
      profile: WORKSPACE
      network_allowlist: ["api.example.com", "github.com"]
      fs_deny: ["~/.ssh", "$HOME/.aws"]
```

```yaml
# Audit-only mode for safe rollout
plugins:
  - name: sandbox
    options:
      profile: SHELL
      backend: audit
```

```yaml
# Pure compute, no I/O at all
plugins:
  - name: sandbox
    options:
      profile: PURE
      blocked_tools: ["web_fetch", "web_search", "bash"]
```

## `budget`

Multi-axis budget accounting and enforcement.
Implementation: `src/kohakuterrarium/builtins/plugins/budget/plugin.py`.

### Options

| Option | Type | Default | Purpose |
|---|---|---|---|
| `turn_budget` | `[soft, hard]` ints or null | null | Cap on LLM turns. Soft = warn; hard = block. |
| `tool_call_budget` | `[soft, hard]` ints or null | null | Cap on total tool calls. |
| `walltime_budget` | `[soft, hard]` seconds or null | null | Cap on wall-clock seconds. |
| `subagent_turn_budget` | `[soft, hard]` ints or null | null | Per-sub-agent run turn cap. |
| `inject_alert` | bool | true | Inject a soft-limit alert into the next system / user message when the soft threshold is crossed. |

When the hard threshold is crossed on any axis, the next
`pre_tool_execute` / `pre_llm_call` / `pre_subagent_run` raises
`PluginBlockError` with a message naming the axis. When the soft
threshold is crossed, an alert is injected so the LLM can wind the
work down voluntarily before the hard cap fires.

### State

State is per-session and namespaced under `plugin:budget:*` in the
session store, so resume preserves accumulated counts.

### Example

```yaml
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

## `compact.auto`

Trigger background context compaction when LLM token usage crosses a
configurable threshold.
Implementation: `src/kohakuterrarium/builtins/plugins/compact/auto.py`.

### Options

| Option | Type | Default | Purpose |
|---|---|---|---|
| `threshold_ratio` | float | 0.7 | Trigger when prompt-token usage exceeds this fraction of the model's context window. |
| `min_messages` | int | 8 | Minimum message count before compaction is allowed. |

### Behaviour

After every LLM call, `post_llm_call` checks token usage against the
configured threshold. If exceeded, it calls
`context.compact_manager.trigger_compact()` which schedules
compaction asynchronously — the controller keeps running while the
summariser works, and the swap happens atomically between turns.
See [non-blocking compaction](../concepts/impl-notes/non-blocking-compaction.md).

### Pack alias

`auto-compact` is a built-in pack that expands to a single
`compact.auto` activation with default options. Configs that just
want the feature on can use `default_plugins: ["auto-compact"]`.

## `permgate`

Interactive user approval for tool calls. The agent emits a
confirmation event on the output bus, waits for the user's reply,
and either proceeds or aborts based on the answer.
Implementation: `src/kohakuterrarium/builtins/plugins/permgate/plugin.py`.

### Options

| Option | Type | Default | Purpose |
|---|---|---|---|
| `enabled` | bool | true | Master on / off. |
| `tools` | list[str] / `"all"` | `"all"` | Which tools require approval. `"all"` = every tool. |
| `whitelist` | list[str] | `[]` | Tools that bypass approval (added to a per-session "always allow" list when the user picks "always"). |
| `auto_approve_pattern` | str | "" | Regex pattern; matching tool args auto-approve without prompting. Use sparingly. |
| `prompt_template` | str | default | Customise the approval prompt shown to the user. |

### Behaviour

`pre_tool_execute` emits a `tool_approval_request` output event with
the tool name, args, and a request id. The agent waits on the output
bus for a `tool_approval_reply` event with a matching id; the reply
carries `decision ∈ {approve, deny, always}`. `always` adds the tool
to a per-session whitelist persisted in plugin state. `deny` raises
`PluginBlockError` so the agent sees the rejection as a tool failure.

The web UI and TUI render the approval as an inline prompt on the
output stream; CLI surfaces it via the same output bus and replies
through the input loop. Headless deployments without a user surface
should disable permgate or set `auto_approve_pattern: "^.*$"`.

### Example

```yaml
plugins:
  - name: permgate
    options:
      tools: ["bash", "write", "edit"]
      whitelist: ["read", "grep", "glob"]
```

## Activation summary

```yaml
# Maximally locked-down agent with a permission gate, budget, and
# auto-compaction.
default_plugins: ["auto-compact"]
plugins:
  - name: sandbox
    options:
      profile: WORKSPACE
      network_allowlist: ["api.example.com"]
  - name: permgate
    options:
      tools: "all"
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

## See also

- [guides/plugins](../guides/plugins.md) — how to write your own.
- [reference/plugin-hooks](plugin-hooks.md) — every hook signature.
- [concepts/modules/plugin](../concepts/modules/plugin.md) — design
  rationale.
