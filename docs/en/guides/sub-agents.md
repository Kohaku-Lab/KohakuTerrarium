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

Builtin sub-agents already opt into the default runtime pack:

```yaml
default_plugins: ["default-runtime"]
```

and currently use the same minimal default budget:

```yaml
turn_budget: [40, 60]
tool_call_budget: [75, 100]
# no walltime_budget
```

The tuple/list shape is `[soft, hard]`. The soft limit injects an alarm into the
sub-agent's next LLM turn; the hard limit gates further tool/sub-agent dispatch
so the specialist can finish in text instead of continuing to spend work.

## Override a builtin budget

Override fields inline on the sub-agent entry:

```yaml
subagents:
  - name: worker
    type: builtin
    turn_budget: [60, 90]
    tool_call_budget: [120, 180]
```

Only set `walltime_budget` if you really want wall-clock enforcement:

```yaml
subagents:
  - name: research
    type: builtin
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
    default_plugins: ["default-runtime"]
    turn_budget: [40, 60]
    tool_call_budget: [75, 100]
```

Inline configs support the same fields as `SubAgentConfig`, including:

- `system_prompt`, `prompt_file`, `extra_prompt`, `extra_prompt_file`
- `tools`, `can_modify`, `interactive`, `output_to`, `output_module`
- `default_plugins`, `turn_budget`, `tool_call_budget`, `walltime_budget`
- `compact`
- `model`, `temperature`
- `budget_inherit`, `budget_allocation`

Use a Python module only when you want to share a config object across packages,
construct prompts programmatically, or subclass/replace runtime behaviour.

## Runtime plugin packs

Budget enforcement is plugin-based. The built-in packs are:

| Pack | Expands to | Use when |
|---|---|---|
| `budget` | `budget.ticker`, `budget.alarm`, `budget.gate` | You want turn/tool/walltime budget accounting and enforcement. |
| `auto-compact` | `compact.auto` | You configured `compact` and want automatic compaction checks after LLM turns. |
| `default-runtime` | all of the above | Normal sub-agent runtime safety defaults. |

For custom or inline sub-agents, add the pack explicitly:

```yaml
subagents:
  - name: reviewer
    type: custom
    system_prompt: "Review the proposed change."
    tools: [read, grep]
    default_plugins: ["default-runtime"]
    turn_budget: [40, 60]
    tool_call_budget: [75, 100]
```

User-declared plugins with the same `name` override defaults, so you can replace
one budget plugin while keeping the rest of the pack.

## Parent vs sub-agent budgets

`turn_budget` and `tool_call_budget` on a sub-agent are independent multi-axis
budgets for that sub-agent run.

The older parent shared iteration budget still exists:

```yaml
max_iterations: 100
subagents:
  - name: explore
    type: builtin
    budget_inherit: true      # default when a parent iteration budget exists
  - name: critic
    type: builtin
    budget_allocation: 10     # isolated legacy turn slice
```

For new configs, prefer explicit sub-agent `turn_budget` / `tool_call_budget`
plus `default_plugins: ["default-runtime"]`. Use `max_iterations` only when you
want a single global cap shared by parent and children.

## Auto-compaction for sub-agents

Sub-agents can also have their own compaction config:

```yaml
subagents:
  - name: long_research
    type: custom
    system_prompt: "Research deeply, then summarize."
    tools: [web_search, web_fetch, read]
    default_plugins: ["default-runtime"]
    turn_budget: [80, 120]
    tool_call_budget: [150, 220]
    compact:
      max_tokens: 120000
      threshold: 0.75
      target: 0.40
      keep_recent_turns: 4
```

The `compact.auto` plugin checks usage after LLM turns and triggers compaction
when the configured threshold is crossed. Without `compact.auto` (or
`default-runtime`), a `compact:` block alone configures the manager but does not
auto-trigger it.

## Quick checklist

For every non-builtin sub-agent you expect to do real work:

1. Give it only the tools it needs.
2. Add `default_plugins: ["default-runtime"]`.
3. Set at least `turn_budget: [40, 60]` and `tool_call_budget: [75, 100]`.
4. Avoid `walltime_budget` unless wall-clock cutoff is truly important.
5. Keep the prompt specialist-focused and ask for a compact structured result.
