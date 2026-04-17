# Memory and compaction

## What it is

Two related systems:

- **Memory.** The `.kohakutr` session file is simultaneously runtime
  persistence and a searchable knowledge base. Every event gets
  indexed for full-text (FTS5) and, optionally, vector search.
  Agents can query this from inside themselves with the
  `search_memory` tool.
- **Compaction.** Long-running creatures blow the context window.
  Auto-compaction summarises old turns in the background without
  pausing the controller, so the agent keeps working while its past
  gets squeezed.

These are two sides of one question: *what do we do with a creature's
accumulated history?*

## Why it exists

### Memory

Most agent frameworks treat history as transient: it exists for the
current LLM call, maybe gets persisted for "resume," and otherwise
disappears. That throws away a lot of signal. The *same* logged events
can power:

- `kt resume` (rebuild the agent mid-work),
- `kt search` (a human looks at what happened),
- an agent-side RAG over its own history (`search_memory`).

A single store, three consumers.

### Compaction

Context windows grow but never fast enough. Without compaction, a
creature that runs for hours eventually hits the wall. Naive
compaction pauses the agent while it summarises — which in an agent
framework means "the controller is frozen while 50k tokens get turned
into 2k." That is unacceptable for ambient agents.

Non-blocking compaction summarises in a background task and splices
the result in atomically between turns. The controller never stops.

## How we define it

### Session store shape

`.kohakutr` is a SQLite file (via KohakuVault) with tables for:

- `meta` — session metadata, snapshots, config
- `events` — append-only event log
- `state` — scratchpad, counters, per-agent state
- `channels` — message history
- `conversation` — latest snapshot for fast resume
- `subagents` — conversation snapshots for sub-agents
- `jobs` — tool/subagent execution records
- `fts` — full-text index over events
- (vector index, optional, when embeddings are built)

### Compaction contract

A creature has a `compact` config block with: `enabled`, `max_tokens`
(or auto-derived), `threshold` (start compaction at N% of budget),
`target` (compact down to N%), `keep_recent_turns` (live zone that is
never summarised), optional `compact_model` (cheaper summariser).

At the end of every turn, if `prompt_tokens >= threshold * max_tokens`,
the compact manager kicks off a background task.

## How we implement it

- `session/store.py` — KohakuVault-backed persistent store.
- `session/output.py` — the output consumer that writes events.
- `session/resume.py` — replay into a freshly built agent.
- `session/memory.py` — FTS5 queries and vector search.
- `session/embedding.py` — model2vec / sentence-transformer / API
  providers for embeddings.
- `core/compact.py` — `CompactManager` with the atomic-splice trick.
  See [impl-notes/non-blocking-compaction](../impl-notes/non-blocking-compaction.md).

Embedding providers (`kt embedding`):

- **model2vec** (default, no torch needed; presets include `@tiny`,
  `@best`, `@multilingual-best`, …)
- **sentence-transformer** (requires torch)
- **api** (external embedding endpoints, e.g. jina-v5-nano)

## What you can therefore do

- **Resume anywhere.** `kt resume` / `kt resume --last` picks up a
  session that was interrupted hours ago.
- **Search sessions.** `kt search <session> <query>` — FTS, semantic,
  hybrid, or auto-detect mode.
- **Agent-side RAG.** An agent calls `search_memory` during a turn,
  gets relevant prior events, and continues with context.
- **Ambient long runs.** A creature running for days never hits the
  context wall: compaction keeps the rolling summary on top of the
  latest N turns.
- **Cross-session memory.** A sophisticated setup can pull the session
  store path from config and share it across related creatures.

## Don't be bounded

Session persistence is opt-out (`--no-session`). Embeddings are opt-in.
Compaction is opt-out per-creature. A creature can run with none of
these — memory is a convenience, not a requirement.

## See also

- [impl-notes/session-persistence](../impl-notes/session-persistence.md) — dual-store details.
- [impl-notes/non-blocking-compaction](../impl-notes/non-blocking-compaction.md) — atomic-splice algorithm.
- [reference/cli.md — kt embedding, kt search, kt resume](../../reference/cli.md) — command surfaces.
- [guides/memory.md](../../guides/memory.md) — how-to guide.
