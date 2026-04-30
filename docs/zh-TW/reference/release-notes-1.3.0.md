---
title: v1.3.0 發布說明
summary: KohakuTerrarium v1.3.0 的最終發布說明。
tags:
  - reference
  - release
  - changelog
---

# KohakuTerrarium v1.3.0 Release Notes

Release date: 2026-04-30

v1.3.0 is the largest KohakuTerrarium release so far. It promotes the new
runtime hierarchy to the public API, adds Studio as the management layer above
Terrarium, hardens provider/session behavior, and refreshes the docs around the
final v1.3 configuration shapes.

Full comparison: [`v1.2.0...v1.3.0`](https://github.com/Kohaku-Lab/KohakuTerrarium/compare/v1.2.0...v1.3.0)

## Highlights

### New public hierarchy: Studio → Terrarium → Creature

The framework is now documented and exposed around three clear layers:

- **Creature** — a standalone agent runtime with its own controller, tools,
  sub-agents, triggers, memory, plugins, and I/O.
- **Terrarium** — the no-LLM engine that hosts creatures in graph topology. A
  solo creature is a one-creature graph; teams are connected graphs wired by
  channels.
- **Studio** — the management facade above the engine for catalog, identity,
  active sessions, saved-session persistence, attach streams, and editors.

Primary imports now include:

```python
from kohakuterrarium import Studio, Terrarium, Creature, EngineEvent, EventFilter
```

Programmatic embedding docs now lead with `Studio`, `Terrarium`, and
`Creature.chat()`, while lower-level `Agent` access remains available for custom
output handlers and direct event control.

### Unified Terrarium engine

The Terrarium runtime now handles both solo and multi-creature workloads through
the same graph engine.

New and expanded capabilities include:

- `Terrarium.with_creature(...)` for a one-creature graph.
- `Terrarium.from_recipe(...)` for multi-creature recipes.
- `Terrarium.resume(...)` / `adopt_session(...)` for saved-session adoption.
- Runtime topology changes: hot-plug, connect, disconnect, graph merge/split.
- Channel and output wiring at engine level.
- Engine event subscription for lifecycle, text, activity, channels, topology,
  processing boundaries, session forks, and errors.

### Studio management facade

`kohakuterrarium.studio` is now the central management surface used by the API,
web dashboard, and embedding code.

Studio namespaces cover:

- `studio.catalog` — packages, built-ins, workspace creatures/modules, templates,
  introspection.
- `studio.identity` — API keys, Codex OAuth, LLM profiles/backends/defaults,
  native-tool settings, MCP, UI preferences.
- `studio.sessions` — active session lifecycle, per-creature chat/control/state,
  topology, wiring, plugins, model switching, memory search.
- `studio.persistence` — saved-session list/resolve/resume/fork/history/viewer,
  artifacts, exports.
- `studio.attach` — live chat, channel observer, logs, trace, files, and PTY
  attach policies.
- `studio.editors` — workspace creature/module CRUD, scaffolding, validators,
  templates, YAML helpers.

### Session viewer, live trace, and persistence improvements

The session stack gained a much richer viewer and stronger persistence behavior:

- Session overview summaries for turns, tokens, cost, tool calls, errors,
  compactions, forks, and attached agents.
- Conversation replay and event/trace views.
- Turn rollups, including derived rollups for older sessions.
- Markdown/HTML/JSONL export.
- Diff and lineage views.
- Artifact routes for provider-generated files.
- Live trace/event stream UI in the dashboard.
- Corrected sub-agent token/cost accounting, cancelled sub-agent usage, distinct
  sub-agent token runs, replay deduplication, and compact snapshot replay.
- Event cache flush gates and turn-end flushes so out-of-process readers see
  current logs.
- KVault scans bypass the default 10k-key limit for long sessions and studio
  persistence paths.

### Native Anthropic provider

v1.3.0 adds a native Anthropic Messages API provider using the official
`anthropic` SDK.

Notable behavior:

- `backend_type: anthropic` is now a canonical backend type.
- Built-in Anthropic profiles use the native Messages API instead of Anthropic's
  OpenAI-compatible endpoint.
- OpenAI-shaped internal messages are converted at the provider boundary.
- Native Anthropic content blocks and tool-use blocks round-trip through KT
  message/tool-call records.
- Streaming text, thinking deltas, tool-use JSON deltas, usage accounting, and
  prompt-cache markers are supported.
- Extra Anthropic fields such as `thinking`, `service_tier`, `top_k`, `top_p`,
  `stop_sequences`, `tool_choice`, and `extra_headers` can pass through.

### Provider retry and recovery hardening

The LLM layer now shares retry/recovery primitives across providers.

Improvements include:

- Shared `RetryPolicy` with configurable retry classes, delays, jitter, and max
  retries.
- Error classification for user errors, overflow, rate limits, server errors,
  transient transport failures, and unknown failures.
- Retry support for OpenAI-compatible providers, native Anthropic, and Codex
  OAuth streaming.
- Retry of no-status/httpx transport failures, 429s, full 5xx ranges, and Codex
  truncated streams such as `RemoteProtocolError`.
- Emergency context-drop recovery for oversized tool-output rounds.
- Streaming surrogate sanitization for provider output.
- OpenAI request sanitization so KT-internal image metadata is not sent upstream.

### Notebook tools

Two new built-in tools support Jupyter notebooks:

- `notebook_read`
- `notebook_edit`

They provide guarded notebook inspection and ordered cell edits with support for
cell offsets, specific IDs, output rendering modes, replace/insert/delete edits,
strict/partial/best-effort policies, bounded diffs, and default output clearing
for changed code cells.

### Runtime budget plugin final shape

Runtime budgets are now owned by one opt-in built-in plugin named `budget`.
Docs and examples were updated to the final v1.3 shape:

```yaml
default_plugins: ["auto-compact"]
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

The old split names (`budget.ticker`, `budget.alarm`, `budget.gate`), the
`default-runtime` pack name, and top-level sub-agent budget fields are not the
v1.3 public API. `compact.auto` remains the plugin name and `auto-compact`
remains the built-in pack.

### Tool runtime and MCP updates

- `bash` and `python` support per-call `timeout`; `timeout: 0` disables the
  configured timeout for that call.
- Tool output normalization is centralized at the executor boundary.
- Text output is UTF-8 byte-budgeted.
- Multimodal tool outputs are rendered safely for logs/context.
- Raw image data URLs are persisted to session artifacts where possible.
- Native tool option persistence/validation was hardened, especially around
  private options and `image_gen`.
- MCP supports stdio, SSE, and streamable HTTP transport normalization, plus
  connect timeouts and native MCP tool schemas.

### Frontend and dashboard updates

The Vue dashboard now includes major session and studio improvements:

- Session viewer workspace.
- Overview, conversation, cost, diff, find, and trace tabs.
- Live event stream store/composable.
- Trace timeline, filters, event rows, and event detail.
- Engine-backed session APIs.
- Multimodal edit rerun and scoped canvas behavior.
- Chat branch resync after reruns.
- Native tool option validation.
- Expanded locale coverage across English, Simplified Chinese, Traditional
  Chinese, Japanese, Korean, and German UI strings.

## Compatibility and migration notes

### Python API

- Prefer `Studio`, `Terrarium`, and `Creature` for new embedding code.
- `Agent.from_path(...)` remains available for lower-level control.
- `Creature.chat(...)` is the preferred streaming text interface for a running
  engine-hosted creature.
- The compatibility serving wrappers remain present, but new API/dashboard code
  should use Studio/Terrarium services.

### Anthropic profiles

- `backend_type: anthropic` selects the native Anthropic provider.
- OpenAI-compatible providers should continue to use `backend_type: openai`.
- Legacy inline Anthropic/OpenAI-compatible configs are still handled for
  compatibility unless configured for native Anthropic behavior.

### Budget configuration

- Runtime budget behavior is plugin-owned.
- Use explicit `plugins[].options` on the `budget` plugin for turn, walltime, and
  tool-call axes.
- `max_iterations` remains a separate legacy iteration cap and is not the runtime
  `budget` plugin.

### API/web internals

- Public/legacy HTTP URLs used by existing frontend clients are preserved where
  needed.
- Route internals have moved toward catalog/identity/session/persistence/attach
  services.
- External integrations that import private API route modules should move to
  documented HTTP endpoints or the Python `Studio`/`Terrarium` APIs.

### Sessions

Existing `.kohakutr` sessions should benefit from improved replay, token/cost
accounting, viewer derivation, artifact handling, and long-session scans.

## Notable fixes

- Fixed session event flushing and long KVault scans.
- Fixed sub-agent plugin hooks leaking across parent/sub-agent shared tool
  instances.
- Fixed sub-agent default model sentinel handling so `subagent-default`,
  `default`, `inherit`, and `parent` inherit the parent LLM instead of reaching a
  provider as literal model IDs.
- Fixed transient Codex OAuth stream failures by adding provider retry handling.
- Fixed no-status/httpx provider retry classification.
- Fixed Unicode surrogate streaming crashes.
- Fixed Codex stream surrogate sanitization.
- Fixed OpenAI payload leakage of KT-internal image metadata.
- Fixed session viewer totals for sub-agent usage.
- Fixed replay duplication and compact snapshot preservation.
- Fixed distinct and cancelled sub-agent token runs.
- Fixed output-wiring self-trigger loops.
- Fixed rooted terrariums running through the engine.
- Fixed `kt serve` daemon bound-port reporting.
- Fixed package data so built-in sub-agent prompts and Studio editor templates
  ship in wheels.
- Fixed frontend branch/rerun syncing and multimodal canvas behavior.

## Packaging and release flow

- Python package metadata is `1.3.0`.
- Frontend package metadata is `1.3.0`.
- The release workflow is tag-driven: pushing `v1.3.0` runs GitHub Actions to
  build frontend assets, wheel, sdist, desktop artifacts, publish to PyPI, and
  create the GitHub release.
- Local `dist/` output is not a release source of truth.

## Validation performed during release prep

- `ruff check src/ tests/` — passed.
- `black --check src/ tests/` — passed; Black emitted the known Python
  3.13-versus-3.14 safety-check warning.
- `pytest` — `4012 passed, 13 skipped, 1 warning`.
- `git diff --check` — clean.
- `npm run format:check --prefix src/kohakuterrarium-frontend` — passed.
- `npm run build --prefix src/kohakuterrarium-frontend` — passed with existing
  Vite chunk-size/component-name warnings.
- `python -m build --wheel` — succeeded with the existing setuptools license
  deprecation warning.
