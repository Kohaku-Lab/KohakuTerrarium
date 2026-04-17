# Session persistence

## The problem this solves

A creature's history has three consumers with different needs:

1. **Resume.** After a crash (or `kt resume --last`), we need to
   reconstruct the agent's state fast. We want the minimum we can
   serialise.
2. **Human search.** A user runs `kt search <session> <query>` and
   expects keyword + semantic search over every detail.
3. **Agent-side RAG.** A running agent calls `search_memory` during
   a turn and expects the same.

A single store has to serve all three. Pick the wrong shape and one
of them becomes expensive or impossible.

## Options considered

- **Conversation-only logs.** Cheap to resume; terrible for search
  (no tool activity, no trigger fires, no sub-agent outputs).
- **Full event log, no snapshot.** Great for search; slow to resume
  (must replay every event).
- **Snapshot only.** Fast resume; no search history.
- **Dual store: append-only event log + per-turn conversation
  snapshot.** What we do.

## What we actually do

A `.kohakutr` file is a SQLite database (managed through KohakuVault)
with tables:

- `events` — append-only log of every event (text chunk, tool call,
  tool result, trigger fire, channel message, token usage). Never
  rewritten.
- `conversation` — one row per (agent, turn-boundary) snapshot of the
  message list (via msgpack, preserves tool-call structures).
- `state` — scratchpad and per-agent counters.
- `channels` — channel message history.
- `subagents` — conversation snapshots for spawned sub-agents, saved
  before destruction.
- `jobs` — tool/subagent execution records (status, args, result).
- `meta` — session metadata, config path, run identifiers.
- `fts` — SQLite FTS5 index over events (keyword search).
- Vector index (optional, under the same store) — built by
  `kt embedding` when requested.

### Resume path

1. Load `meta` → session id, config path, creature list.
2. Load `conversation[agent]` snapshot → rebuild the agent's
   `Conversation` object.
3. Load `state[agent]:*` → restore scratchpad.
4. Load events with `type == "trigger_state"` → re-create triggers via
   `from_resume_dict`.
5. Replay events to the output module's `on_resume` → paints
   scrollback for TTY users.
6. Load `subagents[parent:name:run]` → reattach sub-agent convos.

### Search path

- FTS mode: `events` FTS5 match → return blocks in order.
- Semantic mode: vector search → nearest events.
- Hybrid mode: rank-fuse.
- Auto mode: semantic if vectors exist, else FTS.

### Agent-side RAG

The `search_memory` builtin tool calls the same search layer the CLI
does, filters by agent name if requested, truncates hits, and returns
them as the tool result.

## Invariants preserved

- **Events are immutable.** They are only appended.
- **Snapshots are per-turn.** Not per-event. Resume is O(1) against
  the snapshot, not O(N) against history.
- **Non-serialisable state is rebuilt from config.** Sockets, pywebview
  handles, LLM provider sessions — recreated, not restored.
- **One file per session.** Portable; copyable; `.kohakutr` extension
  lets tooling recognise it.
- **Resume is opt-out.** `--no-session` disables the store entirely.

## Where it lives in the code

- `src/kohakuterrarium/session/store.py` — `SessionStore` API.
- `src/kohakuterrarium/session/output.py` — `SessionOutput` records
  events via the `OutputModule` protocol, so nothing special is
  needed at the controller layer.
- `src/kohakuterrarium/session/resume.py` — the rebuild path.
- `src/kohakuterrarium/session/memory.py` — FTS and vector queries.
- `src/kohakuterrarium/session/embedding.py` — embedding providers.

## See also

- [Memory and compaction](../modules/memory-and-compaction.md) — the
  conceptual picture.
- [reference/cli.md — kt resume, kt search, kt embedding](../../reference/cli.md) — user surfaces.
