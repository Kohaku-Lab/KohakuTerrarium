---
title: Sub-agents
summary: Configure builtin and inline sub-agents, runtime budget plugins, and auto-compaction.
tags:
  - guides
  - sub-agent
  - budget
---

# Sub-agents

Sub-agents are vertical delegation inside one creature: the parent controller
calls a specialist as if it were a tool, and the specialist runs with its own
conversation, prompt, allowed tools, plugins, and budgets.

Use them when the parent should stay small and orchestration-focused while a
specialist explores, plans, edits, reviews, researches, summarizes, or writes the
final user-facing response.

## Builtin sub-agents

The framework ships builtin configs for:

- `explore`
- `plan`
- `worker`
- `critic`
- `research`
- `coordinator`
- `memory_read`
- `memory_write`
- `summarize`
- `response`

Reference them by name:

```yaml
subagents:
  - explore
  - plan
  - worker
```

The string shorthand above is equivalent to:

```yaml
subagents:
  - name: explore
    type: builtin
```

Builtin sub-agents already use auto-compaction and the unified runtime budget
plugin:

```yaml
default_plugins: ["auto-compact"]
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      # no walltime_budget
```

The tuple/list shape for each budget axis is `[soft, hard]`. The soft limit
injects an alarm into the sub-agent's next LLM turn; the hard limit gates further
tool/sub-agent dispatch so the specialist can finish in text instead of
continuing to spend work.

## Override a builtin budget

For builtin sub-agents, override `plugins` through the entry's `options` block:

```yaml
subagents:
  - name: worker
    type: builtin
    options:
      plugins:
        - name: budget
          options:
            turn_budget: [60, 90]
            tool_call_budget: [120, 180]
```

Only set `walltime_budget` if you really want wall-clock enforcement:

```yaml
subagents:
  - name: research
    type: builtin
    options:
      plugins:
        - name: budget
          options:
            turn_budget: [80, 120]
            tool_call_budget: [150, 220]
            walltime_budget: [300, 600]
```

Most long tasks should prefer turn/tool budgets over walltime, because model and
provider latency vary widely.

## Inline YAML-only sub-agents

Many sub-agents do not need a Python module. Use `type: custom` without
`module`/`config` and put `SubAgentConfig` fields directly in YAML:

```yaml
subagents:
  - name: dependency_mapper
    type: custom
    description: Map dependency edges without editing files
    system_prompt: |
      You map imports and runtime dependencies. Return a compact graph and
      cite files as path:line when possible.
    tools: [glob, grep, read, tree]
    can_modify: false
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [40, 60]
          tool_call_budget: [75, 100]
```

Inline configs support the same fields as `SubAgentConfig`, including:

- `system_prompt`, `prompt_file`, `extra_prompt`, `extra_prompt_file`
- `tools`, `can_modify`, `interactive`, `output_to`, `output_module`
- `default_plugins`, `plugins`
- `compact`
- `model`, `temperature`
- `budget_inherit`, `budget_allocation`

Runtime budget axes (`turn_budget`, `tool_call_budget`, and optional
`walltime_budget`) live under the `budget` plugin's `options`; they are not core
`SubAgentConfig` fields.

Use a Python module only when you want to share a config object across packages,
construct prompts programmatically, or subclass/replace runtime behaviour.

## Runtime plugins and packs

Budget enforcement is plugin-based. The built-in runtime surface is:

| Name | Kind | Use when |
|---|---|---|
| `budget` | plugin | You want turn/tool/walltime budget accounting and enforcement. Configure axes under `options`. |
| `compact.auto` | plugin | You configured `compact` and want automatic compaction checks after LLM turns. |
| `auto-compact` | pack | You want to opt into `compact.auto` through `default_plugins`. |

For custom or inline sub-agents, add budget and auto-compaction explicitly:

```yaml
subagents:
  - name: reviewer
    type: custom
    system_prompt: "Review the proposed change."
    tools: [read, grep]
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [40, 60]
          tool_call_budget: [75, 100]
```

User-declared plugins with the same `name` override defaults, so a sub-agent can
replace the builtin budget configuration with a larger or smaller one.

## Parent vs sub-agent budgets

The `budget` plugin's `turn_budget` and `tool_call_budget` options are
independent multi-axis budgets for that sub-agent run.

The older parent shared iteration budget still exists:

```yaml
max_iterations: 100
subagents:
  - name: explore
    type: builtin
    options:
      budget_inherit: true      # default when a parent iteration budget exists
  - name: critic
    type: builtin
    options:
      budget_allocation: 10     # isolated legacy turn slice
```

For new configs, prefer explicit sub-agent `plugins: [{name: budget, options:
...}]` plus `default_plugins: ["auto-compact"]`. Use `max_iterations` only when
you want a single global cap shared by parent and children.

## Auto-compaction for sub-agents

Sub-agents can also have their own compaction config:

```yaml
subagents:
  - name: long_research
    type: custom
    system_prompt: "Research deeply, then summarize."
    tools: [web_search, web_fetch, read]
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [80, 120]
          tool_call_budget: [150, 220]
    compact:
      max_tokens: 120000
      threshold: 0.75
      target: 0.40
      keep_recent_turns: 4
```

The `compact.auto` plugin checks usage after LLM turns and triggers compaction
when the configured threshold is crossed. Without `compact.auto` (or the
`auto-compact` pack), a `compact:` block alone configures the manager but does
not auto-trigger it.

## Quick checklist

For every non-builtin sub-agent you expect to do real work:

1. Give it only the tools it needs.
2. Add `default_plugins: ["auto-compact"]` if it has a `compact:` block.
3. Add the `budget` plugin with at least `turn_budget: [40, 60]` and
   `tool_call_budget: [75, 100]`.
4. Avoid `walltime_budget` unless wall-clock cutoff is truly important.
5. Keep the prompt specialist-focused and ask for a compact structured result.
