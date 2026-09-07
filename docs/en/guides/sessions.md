---
title: Sessions and resume
summary: How .kohakutr session files work, how to resume a creature, and how to replay conversation history.
tags:
  - guides
  - session
  - persistence
---

# Sessions

For readers persisting, resuming, or archiving agent runs.

A session captures the operational state of a run (conversation, events, sub-agent conversations, channel history, scratchpad, jobs, resumable triggers, and config metadata) as a single `.kohakutr` file. You can stop a creature at any point and resume exactly where it left off.

Concept primer: [memory and compaction](../concepts/modules/memory-and-compaction.md), [session and environment](../concepts/modules/session-and-environment.md).

## The `.kohakutr` file

`.kohakutr` is a SQLite database (via KohakuVault) with nine tables:

| Table | Purpose |
|---|---|
| `meta` | session metadata, config snapshot, terrarium topology |
| `state` | per-agent scratchpad, turn count, cumulative token usage, resumable triggers |
| `events` | append-only log of every text chunk, tool call, trigger, token usage event |
| `channels` | channel message history keyed by channel name |
| `subagents` | sub-agent conversation snapshots keyed by parent + name + run |
| `jobs` | tool and sub-agent job records |
| `conversation` | latest conversation snapshot per agent (for fast resume) |
| `fts` | FTS5 index over events (for `kt search`) |
| `vectors` | optional embedding column (populated by `kt embedding`) |

The format is append-only for event data and versioned through KohakuVault's auto-pack. Binary artifacts can also live in a sibling `<session>.artifacts/` directory, so when a run generated images or other binary outputs, archive the `.kohakutr` file and its artifacts directory together.

### Edit and regenerate after compaction

Compaction changes the live prompt and writes a fast-resume snapshot, but it does not erase the append-only event log. Studio identifies editable user messages with the persisted `event_id`, `turn_index`, and `branch_id`. Save & Rerun and Regenerate rebuild the new branch from the selected message's original event prefix, ignoring compact summaries and snapshots. The previous branch and every later event remain available for branch navigation and resume. If the locator is missing, ambiguous, points to injected mid-turn input, or conflicts with the selected branch, the operation fails without changing history.

## Where sessions live

```
~/.kohakuterrarium/sessions/<name>.kohakutr
```

`<name>` is auto-generated from the creature/terrarium name plus a timestamp.
`KT_SESSION_DIR` changes the root for CLI, Studio, and the web API; an explicit
`--session <path>` wins for that run. Generated media uses predictable sibling
paths: `generated_images/` for provider-generated images and
`generated_videos/` for videos. Generic binary tool output remains under
`tool_outputs/`. Existing artifact paths remain readable and are not migrated.
Deleting session data does not delete its retained artifacts. Use
`--no-session` to opt out of session persistence.

## What persists

On each turn KohakuTerrarium records:

- **Conversation snapshots**: raw message dicts via msgpack. Preserves `tool_calls`, multimodal content, and metadata.
- **Event log**: one entry per chunk, tool call, sub-agent output, trigger fire, channel message, compact, interrupt, or error. This is the canonical history.
- **Sub-agent conversations**: saved before the sub-agent is destroyed, so you can inspect what it did after the fact.
- **Scratchpad and channel messages**: per-agent and per-channel.
- **Job records**: outputs of long-running tools and sub-agents.
- **Resumable triggers**: any `BaseTrigger` subclass with `resumable: True` serializes to `state` and restores on resume.
- **Config snapshot**: the fully-resolved config at run time, so resume can rebuild the agent even if the on-disk config changed.
- **Binary artifacts**: generated images and similar binary outputs written under `<session>.artifacts/` beside the session file.

## Resuming

```bash
kt resume --last            # most recent session
kt resume                   # interactive picker (10 most-recent shown)
kt resume my-agent_20240101 # by name prefix
kt resume ~/backup/run.kohakutr
```

Resume is auto-detected: agent sessions mount a single creature; terrarium sessions mount the full wiring and force TUI mode.

Flags the same as `kt run`: `--mode`, `--llm`, `--log-level`, plus `--pwd <dir>` to override the working directory.

Programmatic resume mirrors this (see [Sessions from Python](#sessions-from-python)):

```python
from kohakuterrarium import Terrarium

# Fresh engine from a saved session...
engine = await Terrarium.resume("runs/swe_20240101.kohakutr", pwd="/work", llm=None)

# ...or adopt into an engine that's already running other graphs.
graph_id = await engine.adopt_session("runs/other.kohakutr")
```

Both accept a path or a `SessionStore`; `llm=` is an optional selector
string override. Modern graph sessions persist one working directory per
creature. Resume performs a read-only workspace preflight before acquiring a
writer store, constructing a runtime, registering lifecycle state, or adopting
a creature. If a saved directory is unavailable, callers must either provide a
replacement, open the read-only history, or cancel; there is no silent fallback
to the KohakuTerrarium process directory.

Use `workspace_overrides={"creature-id": "/new/path"}` for targeted
replacement. A grouped missing path may also be addressed by the preflight
`gap_id`, replacing only the members in that group. The scalar `pwd=` argument
is retained as an explicit compatibility override and broadcasts one directory
to every member. It is mutually exclusive with `workspace_overrides`.

After successful adoption, confirmed replacements are written back to the
authoritative graph manifest and legacy metadata projection, so later resumes
keep them. If the persistence checkpoint fails, resume restores the old
workspace metadata; an incomplete restoration is recorded as
`partial_dirty` instead of being reported as clean; later preflight and resume
fail closed until that session is repaired. Remote and clustered
sessions validate paths on the worker that will execute each member, and all
cluster members are preflighted before the first adoption. Cluster API callers
can scope replacements by member session ID, so identical creature or path-group
targets on different workers may resolve to different directories.

A file that exists but cannot be resumed (unknown saved-session type, missing
config path or unresolved workspace in the metadata) raises a typed session
resume error.

What resume does:

1. Reads the config snapshot from `meta`.
2. Reloads the current on-disk config (so prompt/tool changes you made since take effect).
3. Merges: config snapshot provides the session identity; current config provides the running logic.
4. Rebuilds the agent, attaches the same `SessionStore`, reinjects the conversation snapshot, replays scratchpad/channel/trigger state.
5. Starts the controller fresh; previous events are in context.

This means small config drift is fine (swapping an LLM, changing a prompt). Structural drift (renaming the creature, removing a tool it was actively using) can cause replay errors; pin a session to its original config if you need perfect fidelity.

## Open conversations in the web UI

The Conversations rail lists only conversations whose runtime is currently
attached. The persisted conversation lifecycle remains a separate concern:

- **Live and open:** a runtime is attached, so the conversation appears in the rail.
- **Dormant and open:** no runtime is attached. The saved conversation remains
  available from Sessions but is not rendered in the rail.
- **Ended:** the user explicitly ended the conversation. Its saved history remains
  available from Sessions.

Closing a Chat or Inspector tab only detaches that view. It never stops or ends
the conversation, so a still-live runtime remains in the rail. After the backend
reports a stopped or disconnected creature as inactive, the row disappears on
the next refresh. The Sessions history page remains read-only and keeps its
existing **View** and **Resume** actions.

Every newly persisted conversation has a stable `conversation_id`. Runtime graph
IDs may change after restart or resume, and filenames may be moved or renamed,
but those changes do not create duplicate records. `GET /api/sessions/open`
merges live runtimes with saved open markers using this identity and lets the
live row win; the rail renders only rows with `is_live: true`. Session indexes
and lookups are scoped to the authenticated session directory, so one user's
index cannot supply another user's rows.

The saved lifecycle marker is explicit. Sessions created before this marker was
introduced are still available in history. A normal runtime stop preserves the
open marker and records a paused/dormant state. Explicit **End** clears the
marker and records a terminal state. Local, remote, and clustered operations
synchronize this marker with the saved store before the rail refreshes.

Resume is singleflight per saved conversation: concurrent browser requests share
one server operation, while cancellation of one waiter does not cancel the
shared resume. A failed operation can be retried. Cluster resume is all-or-error:
if any member cannot resume or any saved link cannot be restored, the server
compensates already resumed members, removes partial runtime metadata, preserves
the original saved lifecycle, and returns a non-success response rather than a
degraded partial cluster.

## Interrupt and resume workflow

```bash
kt run @kt-biome/creatures/swe
# work... then Ctrl+C twice while idle (or Ctrl+D / /exit)
# later:
kt resume --last
```

In Rich CLI mode, Ctrl+C interrupts the active turn; when idle, pressing Ctrl+C twice (or Ctrl+D / `/exit`) exits gracefully, flushes the session store, and prints a resume hint. Forced kills (SIGKILL) skip the final flush but most recent state is still on disk thanks to append-only writes.

## Copying or archiving sessions

```bash
# Backup
cp ~/.kohakuterrarium/sessions/swe_20240101.kohakutr ~/backups/
cp -r ~/.kohakuterrarium/sessions/swe_20240101.artifacts ~/backups/   # if present

# Resume from a moved location
kt resume ~/backups/swe_20240101.kohakutr
```

Inspect without resuming via `SessionReader` (below); it opens the
file read-only, so inspection never touches `status` or `last_active`.

## Sessions from Python

### Creating sessions: engine-owned persistence

Persistence is a keyword on the engine; the framework writes and
validates the session metadata itself:

```python
from kohakuterrarium import Terrarium

# Autosession: every graph gets <session_dir>/<graph_id>.kohakutr
# automatically (merge/split children land there too).
engine = Terrarium(session_dir="runs/")

# Per-creature control via session=:
c = await engine.add_creature(
    "@kt-biome/creatures/general",
    session="runs/student-42.kohakutr",   # mint the store at this exact path
)
# session=True   -> mint in the default session dir
# session=False  -> no persistence, even under autosession
# session=<SessionStore> -> attach an existing store as-is
# session=None (default) -> follow the engine (autosession / graph store / off)

# Recipes: one terrarium-typed store for the whole graph.
await engine.apply_recipe("@kt-biome/terrariums/swe_team", session="runs/team.kohakutr")
```

`await engine.shutdown()` (or leaving the `async with` block) closes
every store the engine minted, so files no longer get stuck at
`status: "running"`.

### Resuming

- `await Terrarium.resume(store_or_path, *, pwd=None, llm=None)`:
  build a fresh engine and adopt the saved session.
- `await engine.adopt_session(store_or_path, *, pwd=None, llm=None)`:
  resume into a running engine; returns the new `graph_id`.

Resume reconstructs topology from the config path recorded in the
session metadata (`@pkg` refs included) and runs with a per-agent
working directory; it does not `os.chdir` your process.

### Reading: `SessionReader`

`SessionReader` is the read-only inspection surface over a
`.kohakutr` file. It opens via `SessionStore.open_readonly`, so
reading never bumps `last_active` or flips `status`:

```python
from kohakuterrarium import SessionReader

with SessionReader("~/backups/swe_20240101.kohakutr") as r:
    print(r.meta["status"], r.agents)

    for turn in r.turns():               # live-branch turns, reassembled
        tools = [tc["name"] for tc in turn.tool_calls]
        print(f"[{turn.source}] {turn.user_text!r} -> "
              f"{turn.assistant_text[:60]!r} tools={tools}")

    events = r.events()                  # the raw append-only log
    convo = r.conversation()             # final snapshot (message dicts)
    chan = r.channel_messages("tasks")   # one channel's history

    r.index()                            # ad-hoc FTS index, then:
    hits = r.search("score.json", k=5)
```

`turns()` skips regenerated / edited siblings, so you see the same live
branch every viewer shows. `search()` returns hits only for indexed
sessions (`kt embedding`, or `reader.index()` for FTS ad hoc).

If you need raw read-write access, `SessionStore(path)` is still
there, but for any listing / preview / viewer purpose use
`SessionStore.open_readonly(path)` (or just `SessionReader`): a plain
open + close marks the session paused and bumps `last_active`,
corrupting recency ordering.

## Compaction

Compaction shrinks the conversation when context fills up. Configure per creature:

```yaml
compact:
  enabled: true
  threshold: 0.8              # compact when context hits 80% of window
  target: 0.5                 # aim for 50% after compaction
  keep_recent_turns: 5        # always preserve the last N turns verbatim
  compact_model: gpt-4o-mini  # cheaper model for the summarization pass
```

Compaction runs in the background (see [concepts/modules/memory-and-compaction](../concepts/modules/memory-and-compaction.md)): the controller keeps running; when the new summary is ready, the conversation is swapped. Each compaction is logged as an event.

Manual compaction:

```
/compact
```

from the CLI/TUI prompt. Useful before handing off a long session or shipping it as context into another run.

## Listing and searching sessions

The `kt serve` web UI and `GET /api/sessions` are backed by a
sidecar index: one SQLite file at
`<session_dir>/.kt-index.kvault` that caches listing-shape metadata
(name, status, last-active timestamp, agents, preview, …) per
session and exposes BM25 search over the text columns. You never
interact with it directly; it stays consistent across server
restarts and `kt run` sessions started while the server was down.

Query parameters on `GET /api/sessions`:

| Param | Default | Notes |
|---|---|---|
| `limit` | `20` | page size |
| `offset` | `0` | page offset |
| `search` | `""` | FTS5 query over `name` / `preview` / `config_path` / `agents` / `pwd` |
| `sort` | `last_active` | `last_active` \| `created_at` \| `name` \| `status` \| `relevance` |
| `order` | `desc` | `desc` \| `asc` |
| `status` | (none) | exact match (`running`, `paused`, …) |
| `config_type` | (none) | exact match (`agent`, `terrarium`) |
| `node_id` | (none) | exact match; filter by which lab node ran the session |
| `refresh` | `false` | incremental reconcile before listing; re-reads only files whose `(mtime, size)` changed |
| `full_rescan` | `false` | force re-read of every file (use after manually editing a `.kohakutr` on disk) |

`sort=relevance` is only meaningful when `search` is set; with any
other sort, the FTS hit-set is collected first and then ordered by
the requested field.

How the index stays in sync without manual refresh:

- **Push.** While the API server is running, each `SessionStore` it
  owns pushes updates into the index on a debounce (every 20 events
  or 5 seconds, whichever first), so the index never falls behind
  during normal use.
- **Startup reconcile.** On every server start, the index does a
  fingerprint-diff pass over the session directory and re-reads only
  the files that changed. First-ever startup does a full read of
  every file (the *bootstrap* step) and remembers it succeeded.
- **`?refresh=true`.** Trigger the same incremental reconcile on
  demand; useful right after copying a backup `.kohakutr` into the
  session directory. Refreshes are single-flighted: a request that
  arrives while a scan is already running queues behind it and skips
  its own pass only once a scan started *after* the request arrived,
  so a burst of refreshes costs at most two scans and every refresh
  still reflects the changes that made the client ask.
  `?full_rescan=true` keeps its force-reread intent and never skips.

The sidecar is safe to delete: the next listing rebuilds it from
the `.kohakutr` files. Nothing inside the index is unique state.

Programmatic listing without the HTTP layer:

```python
from kohakuterrarium.studio.persistence.session_index import (
    get_session_index_default,
)

index = get_session_index_default()
page = index.list(search="auth bug", sort="relevance", limit=10)
for row in page.rows:
    print(row["name"], row["last_active"], row["preview"])
```

## Memory search

Sessions are also a searchable knowledge base. After building an index:

```bash
kt embedding ~/.kohakuterrarium/sessions/swe.kohakutr
kt search swe "auth bug"
```

The agent itself can search with the `search_memory` tool. Full walk-through: [Memory](memory.md).

## Disabling persistence

Sometimes you want a throwaway run:

```bash
kt run @kt-biome/creatures/swe --no-session
```

No `.kohakutr` is created. This also disables compaction's ability to recover previous rounds from disk (it still compacts in memory).

## Troubleshooting

- **Compaction runs forever / OOMs.** The compact model is the same heavy controller model. Set `compact_model` to something cheap (`gpt-4o-mini`, `claude-haiku`).
- **Resume errors with `tool not registered`.** The creature config changed (a tool was removed) but the conversation still references it. Manually edit `config.yaml` to re-add the tool, or start a fresh session.
- **`kt resume` can't find a session I just saw.** Sessions are resolved by prefix against filenames in `~/.kohakuterrarium/sessions/`. If you renamed the file or moved it, pass the full path.
- **Generated images are missing after copying a session.** Copy the sibling `<session>.artifacts/` directory too, not just the `.kohakutr` file.
- **Large `.kohakutr` files.** The event log is append-only; long sessions grow. Archive old ones or split work across sessions. Compaction shrinks the live conversation but keeps the full event history for search.
- **Sub-agent output missing from resume.** Sub-agent conversations are saved when the sub-agent completes. If the parent was interrupted mid-sub-agent, the latest snapshot is whatever was persisted at the last checkpoint.

## See also

- [Memory](memory.md): FTS, semantic, and hybrid search over session history.
- [Configuration](configuration.md): compaction recipes and session flags.
- [Programmatic Usage](programmatic-usage.md): driving agents and engines from Python.
- [Reference / Python API](../reference/python.md#sessions): `SessionReader` / `SessionStore` signatures.
- [Concepts / memory and compaction](../concepts/modules/memory-and-compaction.md): how compaction works.
