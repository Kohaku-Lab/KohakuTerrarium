---
title: Prompt aggregation
summary: What the framework injects into a turn, in what order, and under what condition.
tags:
  - concepts
  - impl-notes
  - prompt
---

# Prompt aggregation

## The problem this solves

An agent's "system prompt" is not one string, and it is not even one payload.
Two things reach the model every turn:

1. the assembled system prompt, and
2. the native tool schemas, which the provider carries separately.

For a 20-callable creature the schemas are the larger half. Any accounting that
looks only at the prompt is measuring the smaller one.

The framework owns both. What it injects is everything the creature author
cannot know: how dispatch works, what a background job means, which channels
exist right now, how arriving messages are tagged. The creature owns who it is.

## The governing rule

**Every framework section is gated, and a block whose subject does not exist is
not emitted.** An inapplicable block is worse than a missing one, because it
teaches the model something false — call syntax for a creature using native
function calling, or channel etiquette for a creature with no channels.

## Options considered

- **Hand-written prompts.** Fragile; breaks whenever a tool is added.
- **Always-full documentation.** Complete but unaffordable — tool docs alone ran
  to tens of kilotokens.
- **Load-on-demand.** Ship names and descriptions, let the agent pull the rest
  through `info`. This is the industry's progressive-disclosure pattern, and it
  is what `standard` mode does.
- **Per-tool choice.** The trade-off is not the same for every tool: a tool with
  three interacting policy flags is worth inlining, a file reader is not.

## What we actually do

### Three tiers, selected per creature and per tool

`tool_doc_mode` picks the default; a `tools:` entry may override it.

| Mode | Description | Usage tier | Parameter schema |
| --- | --- | --- | --- |
| `brief` | yes | no | present, prose stripped; first use gated on `info` |
| `standard` *(default)* | yes | no | present, full |
| `full` | yes | **inlined** | present, full |

The tiers map onto the documentation files themselves. Each file in
`builtin_skills/` splits at `## Reference`: everything above is the **usage**
tier that `full` inlines, everything below is reachable only through `info`, in
every mode.

```yaml
tool_doc_mode: standard          # creature default

tools:
  - { name: read,       type: builtin }
  - { name: multi_edit, type: builtin, doc_mode: full }
```

### Section order and gating

`prompt/aggregator.py:aggregate_system_prompt` emits these in a fixed order, so
provider-side prompt caching sees a stable prefix:

| # | Section | Emitted when |
| --- | --- | --- |
| 1 | Creature base prompt | always |
| 2 | `## Available Functions` | `tool_format != "native"` or `tool_doc_mode == "full"` |
| 3 | `## Function Documentation` | any tool resolves to `full` |
| 4 | `## Tool guidance` | some tool returns a `prompt_contribution()` |
| 5 | Plugin contributions | a registered plugin returns content |
| 6 | `## Skills` | the skill registry is non-empty |
| 7 | `## Working with the group` | the creature is in a graph with channels or wires |
| 8 | `## Growing the group` | the creature is privileged |
| 9 | `## Untrusted content` | `include_hints_in_prompt` |
| 10 | `## Calling functions` | `tool_format != "native"` |
| 11 | `## Output format` | `include_hints_in_prompt` and `tool_format != "native"` |
| 12 | `## Execution model` | `include_hints_in_prompt` |

Sections 7 and 8 are rendered by `terrarium/runtime_prompt.py` into a
sentinel-bounded block and refreshed on every topology change, so they cannot go
stale. A solo creature costs zero bytes for both; the moment it is wired, the
block appears.

### Schema construction

`llm/tools.py` states framework semantics once rather than per tool:

- `run_in_background` appears only on tools declaring `supports_background`.
  Mid-flight promotion (`core/backgroundify.py`) covers a direct call that turns
  out to be slow.
- Sub-agent context isolation is explained in `## Execution model`, not inside
  every `task` parameter.
- A tool `description` is capped at 160 characters and carries a "Not for …"
  clause wherever a confusable sibling exists. It is the always-loaded tier and
  the field the model routes on.

### Framework hints

Six canonical, overrideable blocks in `prompt/framework_hints.py`:
`framework.execution_model`, `framework.call_syntax`, `framework.output_model`,
`framework.untrusted_content`, `framework.group_model`,
`framework.group_growth`. Package-level `framework_hints:` in `kohaku.yaml`
merge under creature-level `framework_hint_overrides`; an empty string omits a
block entirely.

Call-syntax examples are **generated** from the active format definition rather
than written by hand, so they cannot drift from what the parser accepts.

## Invariants preserved

- **Deterministic.** Same config plus registry plus plugin set yields a
  byte-stable prompt.
- **No duplication across payloads.** Anything the provider already carries as
  schema is not repeated in prose.
- **Gates are two-way.** Each section's presence *and* absence is asserted in
  `tests/unit/prompt/test_aggregator.py`; a block firing when its subject does
  not exist is the failure mode this system had.
- **Budgeted.** `tests/integration/test_prompt_budget.py` fails the build if the
  framework payload for the reference creature exceeds its target.
- **Documentation cannot drift.** `tests/unit/test_tool_doc_shape.py` asserts
  each tool's class description equals its file's, and that every schema-backed
  tool has a doc file and vice versa.

## Where it lives in the code

- `src/kohakuterrarium/prompt/aggregator.py` — the composition function.
- `src/kohakuterrarium/prompt/framework_hints.py` — the canonical blocks.
- `src/kohakuterrarium/prompt/tool_contributions.py` — `## Tool guidance`.
- `src/kohakuterrarium/llm/tools.py` — native schema construction.
- `src/kohakuterrarium/modules/tool/doc_mode.py` — tier resolution.
- `src/kohakuterrarium/terrarium/runtime_prompt.py` — the live graph block.
- `src/kohakuterrarium/builtin_skills/` — the tiered documentation corpus.

## See also

- [Plugin](../modules/plugin.md): contributing a prompt section at runtime.
- [Tool](../modules/tool.md): how tool documentation is registered.
- [tool_doc_mode, tool_format, include_* in reference/configuration.md](../../reference/configuration.md)
