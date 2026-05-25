---
title: Session persistence
summary: The .kohakutr file format, what's stored per creature, and how resume rebuilds conversation state.
tags:
  - concepts
  - impl-notes
  - persistence
---

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
- `*.artifacts/` sibling directory — binary outputs such as generated
  images, stored next to the `.kohakutr` file rather than inside SQLite.

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
- **One logical bundle per session.** The SQLite file is the primary
  unit, but binary artifacts may live in a sibling `<session>.artifacts/`
  directory.
- **Resume is opt-out.** `--no-session` disables the store entirely.

## The listing sidecar

`.kohakutr` files are the source of truth, but they are the wrong
shape for *listing* — `GET /api/sessions` on a 1000-session install
must not open 1000 SQLite files just to render a sidebar. We layer
a write-through cache on top:

```
<session_dir>/.kt-index.kvault   ← one SQLite file, three tables:
    entries  (KVault)   filename → packed SessionIndexEntry
    search   (TextVault) FTS5 over name / preview / config_path /
                        agents / pwd  (BM25 ranked)
    meta     (KVault)   schema_version, bootstrap_completed, …
```

A `SessionIndexEntry` is a flat dict of the listing-shape fields
(`name`, `last_active`, `status`, `config_type`, `node_id`,
`terrarium_name`, `preview`, `agents`, `parent_session_id`,
`forked_children`, …) plus a `(mtime, size)` fingerprint pulled
from `stat()`. One row per session file. Cold listing is now one
file open + one table scan regardless of how many sessions exist;
search is a single FTS5 query.

### How the index stays honest

Three independent paths keep entries in sync with the files on
disk — none of them is load-bearing on its own:

1. **Push hook** (`session_index/hooks.py`). When the API server
   itself owns a `SessionStore`, a `SessionIndexHook` subscribes to
   its event stream and re-upserts the entry on a debounce
   (every 20 events or 5 seconds, whichever first). The same store
   instance both writes events and updates the index — no lag.

2. **Pull reconcile** (`session_index/reconcile.py`). Walks the
   session directory, fingerprints every file, opens only the ones
   whose `(mtime, size)` differs from the stored entry (or that have
   no entry yet), and re-reads their meta + preview. Files that
   have been deleted are dropped from the index. This is the fallback
   the API surfaces as `?refresh=true`. `?full_rescan=true` forces
   re-read of every file — use it after manually editing a
   `.kohakutr` on disk.

3. **Startup reconcile**. The `get_session_index_default` singleton
   runs reconcile on first open of a process. First-ever open does
   a full reconcile (bootstrap) and sets the `bootstrap_completed`
   flag; subsequent opens (server restart) run the incremental
   path so sessions produced by sibling processes (`kt run` in
   another terminal while the server was down) get picked up
   automatically. A failure here logs loudly but never blocks
   server startup — stale data is preferable to no service.

### Why a sidecar (not in-memory)

- **Survives restarts.** A long-running server that crashed mid-list
  rebuilds in ms via the fingerprint diff, not minutes via N file
  opens.
- **Survives moves.** `mv ~/.kohakuterrarium/sessions /backup` carries
  the sidecar along; the next listing on `/backup` is instant.
- **One open, one query.** Listing 1000 sessions is one SQLite open
  + one `ORDER BY last_active LIMIT 20` (or one FTS5 match). The
  pre-sidecar path opened 1000 files even to render the first page.

The sidecar is safe to delete; the next `get_session_index_default`
call rebuilds it. There is no migration path because there is no
schema in it that the source of truth (the `.kohakutr` files
themselves) does not already hold.

## Where it lives in the code

- `src/kohakuterrarium/session/store.py` — `SessionStore` API.
- `src/kohakuterrarium/session/output.py` — `SessionOutput` records
  events via the `OutputModule` protocol, so nothing special is
  needed at the controller layer.
- `src/kohakuterrarium/session/artifacts.py` — artifact path resolution
  and safe binary writes.
- `src/kohakuterrarium/session/resume.py` — the rebuild path.
- `src/kohakuterrarium/session/memory.py` — FTS and vector queries.
- `src/kohakuterrarium/session/embedding.py` — embedding providers.
- `src/kohakuterrarium/studio/persistence/session_index/` — listing
  sidecar: `entry.py` (row schema), `store.py` (KVault + TextVault
  wrapper), `reconcile.py` (fingerprint diff + parallel re-read),
  `hooks.py` (live push from a running SessionStore), `__init__.py`
  (process-wide singleton + startup reconcile).
- `src/kohakuterrarium/api/routes/persistence/saved.py` — the
  HTTP surface (`GET /api/sessions`, `DELETE /api/sessions/{name}`).

## See also

- [Memory and compaction](../modules/memory-and-compaction.md) — the
  conceptual picture.
- [Graph and sessions](graph-and-sessions.md) — how a session store
  is rebuilt across terrarium merges and splits, and why the recipe
  is the source of truth on resume.
- [reference/cli.md — kt resume, kt search, kt embedding](../../reference/cli.md) — user surfaces.
