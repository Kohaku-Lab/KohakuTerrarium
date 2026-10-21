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

A session records a run's conversation, events, sub-agent conversations, channel history, scratchpad, job records, resumable triggers, and config metadata in a `.kohakutr` file. Resume reconstructs a creature from these records; it does not snapshot a running process or roll back external resources.

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

Compaction changes the live prompt and writes a fast-resume snapshot, but it does not erase the append-only event log. Studio identifies editable user messages with the persisted `event_id`, `turn_index`, and `branch_id`. Save & Rerun and Regenerate build a new branch from the target history, reusing only summaries and snapshots whose source history remains compatible. Representations that depend on modified content are not reused. The previous branch and every later event remain available for branch navigation and resume. If the locator is missing, ambiguous, points to injected mid-turn input, or conflicts with the selected branch, the operation fails without changing history.

`/branch <turn_index> <branch_id>` selects a coherent recorded branch without generating a new turn. It reuses compatible checkpoints and summaries, restores branch coordinates and compaction state, and saves the selection for resume. `/branch latest` selects the latest **compatible** branches along one lineage, not independent maxima for every turn. Both use the existing reconstruction budget and retention checks, and refuse switching while a turn, compaction, or background job is active. `/branch` without arguments lists alternatives and the current selection.

#### Local completion of older checkpoints

A compatible checkpoint can lack some tool-call relationships without requiring the entire history to be expanded. When the selected prefix and summary are provably reusable, reconstruction can preserve them and insert only missing complete canonical assistant rounds with their uniquely owned, settled results at proven positions. Restored rounds use the same canonical identity and ordering rules as subsequent compaction. It does not rerun those tools or claim that the summary consumed the restored records. Incomplete old consumption records may leave some factual overlap between the summary and these traceable additions; zero additional messages is not guaranteed.

Placement and replacement coverage have different proof requirements. A retained checkpoint message may omit a tool call whose result a retained summary explicitly consumed. Placement accepts that reduction only when the remaining full payload and canonical identity match after removing precisely those proven-consumed calls; it does not reinstall the removed calls or claim new compact coverage. Internal event, branch and job-context metadata is retained during this check and stays out of provider wire messages.

Local completion rejects a candidate when:

- its source lineage conflicts with the selected branch or the target boundary;
- a round owner, call/job identity, or settled result is ambiguous;
- the original canonical round or its required result is missing;
- a whole round cannot be restored without duplicating a present call, reviving explicitly covered or cleared content, or violating the target/order boundary.

Other proven checkpoints may be tried before raw reconstruction. Once a rejected candidate carries a clear cutoff, an alternative must preserve that cutoff; a raw fallback cannot revive the cleared prefix. Reconstructed prompts are checked before installation against the existing retention limit and configured context budget (`compact.max_tokens`, not the auto-compaction threshold). Admission failure raises `ConflictError` without installing that candidate or opening its edit branch. The token check is a message-text estimate, not a provider-exact count. This is not an extra gate for ordinary turns or unexpanded reruns, nor a guarantee that a later provider call will succeed.

### Multimodal edit, regenerate, and branch

When history is stable, editing and regenerating a user turn rebuild its original text, image, and file parts from the event log. Regenerating reuses those parts; an edit supplies the new part list and opens a sibling branch. A default tail retry after settled-history refresh records the input associated with the refreshed message and binds that message to the new branch's user-event identity; it does not persist a stale pre-refresh task banner as the original input. Continuing an older branch uses that branch's history and compatible snapshots, including after resume. In Studio, editing only the text retains attachments; deleting or replacing one affects only the new branch.

Saved assistant messages and settled tool results retain their structured content and call/result pairing. A failed tool may return diagnostic media or partial files; its saved output remains structured, with error and exit status recorded separately. Replaying a settled result does not execute the tool again. Different generated image bytes use different artifact paths, so a new sibling does not overwrite the earlier generated image. A settled background result is available to compatible branches that retain its call, but not to a branch that excludes that call; saving a result does not itself continue an unselected branch.

Inline bytes are immutable copies: data URLs and inline file contents, including binary `FilePart.data_base64`, are retained in saved history, so mutating a derived workspace copy of an extracted file does not change later replay. Binary files are not reduced to display placeholders before storage. Request preparation still uses the existing file reader and provider capabilities; saving bytes does not imply that every MIME type is readable by the model, and display previews continue to omit base64 payloads. Live workspace paths (`file://` and similar) remain dynamic and are reread from disk when a request is prepared.

Generated artifacts live beside the session file in `<session>.artifacts/` (`generated_images/`, `generated_videos/`, `tool_outputs/`). An explicit `--session` path, programmatic `session=`, or `Terrarium(session_dir=...)` owns those artifacts even when `KT_SESSION_DIR` points at a different default directory. Outgoing provider requests resolve stored artifact URLs to data URLs at the provider boundary; the conversation snapshot keeps the compact session-relative reference. Local images are resolved only for the content the provider selects: images removed by an existing request budget, or from roles the provider does not send as images, do not need to be readable for that request.

New `read` image results capture the validated bytes at read time. They use
session artifacts when storage is available and retain inline bytes otherwise.
Deleting or overwriting the source image does not alter an earlier result;
reading the path again captures the new contents. Existing saved `file://`
references are unchanged and remain live references.

Known limits in this release:

- Studio rejects inline edit of user messages that contain part types other than text, image, and file.
- A user message with more than one text part can still be rerun, but Studio does not rewrite those texts as a single merged field.
- Actual history switching, edit/regenerate, and rewind are rejected while a turn or compaction is active, or background work has not settled. Ordinary same-branch input keeps its existing queueing behavior; it does not become a history switch.
- Snapshots restore stored messages and references, not missing artifact files, processes, or external side effects. Archive the session file together with its artifacts directory. Required missing local images fail request preparation instead of silently disappearing; remote URL availability is not guaranteed.
- These guarantees cover the framework's existing text/image/file representations and the selected provider's capabilities. They do not promise lossless visual summaries or cross-provider history migration.

### Stable multimodal history with compaction and checkpoints

An edit to an uncompressed tail can reuse a summary whose source inputs are unchanged. Editing a turn consumed by that summary instead rebuilds the target prefix without the dependent summary. Returning to an earlier branch or resuming its compatible checkpoint uses that branch's effective context, including retained media and call/result relationships. Restoration alone does not rerun the original tools or summarization.

Compaction sends a textual projection to the summarizer, not the original images for visual analysis. Media already replaced by the selected summary need not reappear in a later provider request, although the original events and available artifact files remain in the saved history. A later tool result that the summary did not consume is not covered merely because its call came from an ancestor turn; existing provenance and context-elision rules still apply.

A checkpoint can contain already-elided tool output, while raw reconstruction may rehydrate that output before the current elision policy is reapplied. These representations need not be identical dictionaries. Compare effective context under the same summary, retained-user, and elision policy; legitimate rehydration is different from restoring media that the selected summary already consumed. Local-media availability is checked only for the final effective provider input, so excluded media do not block continuation and required missing media still fail explicitly. These rules apply to stable history, not history operations during active compaction or background work.

Summaries are bound to canonical rounds and tool results at capture time. Reuse excludes the retained tail and any re-attached user message, so a summary covers only the rounds it actually replaced. When canonical captured history cannot be bound unambiguously, compaction is skipped with `capture_unproven`: no summarizer call runs and the live conversation is unchanged. Unknown input provenance is separate from structural replacement coverage: a compatible, proven completion can pass its coverage into a subsequent compaction while the new summary's source remains unknown. It remains checkpoint-based rather than becoming a general raw-replay replacement. Framework-generated job-status prefixes are recorded on new messages and checked using the same composition as the producer; default job reporting need not be disabled to compact settled history.

### Older session formats

Some older writers recorded canonical messages with event, turn, and branch IDs
without updating the session format marker. Migration preserves those event logs,
snapshots, and compaction references rather than translating them again.

If a session mixes canonical history with legacy history, snapshot-only agents,
or agents with only observability events, migration refuses before creating a
destination. Duplicate or invalid event IDs also require explicit repair. The
original session remains intact; migration does not guess new identities or
automatically repair a previously migrated file.

## Where sessions live

```
~/.kohakuterrarium/sessions/<name>.kohakutr
```

`<name>` is auto-generated from the creature/terrarium name plus a timestamp.
`KT_SESSION_DIR` changes the root for CLI, Studio, and the web API; an explicit
`--session <path>` wins for that run. Generated media is stored beside the
session file that owns the run — including a custom `session=` path or a
`Terrarium(session_dir=...)` run — not under a global `KT_SESSION_DIR` when
those differ. Predictable sibling paths: `generated_images/` for
provider-generated images and `generated_videos/` for videos. Generic binary
tool output remains under `tool_outputs/`. Existing artifact paths remain
readable and are not migrated. Deleting session data does not delete its
retained artifacts. Use `--no-session` to opt out of session persistence.

### Runtime storage boundaries

Keep session storage directories and their ancestor directories in place while the
process is running. Deleting, renaming, or moving them at runtime is undefined
behavior and the caller's responsibility, including through Web file management,
Lab, editors, shell commands, or external programs. The framework does not add a
filesystem monitor or permission gate to enforce this requirement.

Likewise, directly editing, replacing, deleting, or renaming `.kohakutr` databases
or related SQLite files (such as `-wal` and `-shm`) through generic file APIs,
write/edit tools, or raw database access is not a supported runtime maintenance
operation. Use the session APIs for normal recording, Memory updates, migration,
forking, and session deletion. Ordinary unrelated project-file editing is unaffected.

The metadata-read coordination described below covers supported framework operations
in one process on one machine. It does not coordinate external processes or arbitrary
custom code that bypasses those operations, and it does not redesign concurrency
between existing legacy readers and writers.

### Inspecting an offline session from a checkout

From a checkout with its dependencies available, run:

```bash
python scripts/inspect_session.py /path/to/saved.kohakutr
python scripts/inspect_session.py /path/to/saved.kohakutr --all --search "query"
```

The inspector owns a `SessionStore.open_readonly()` handle and closes only that
handle, including when output fails or is interrupted. It does not change session
status or recency and does not persist reconstructed event counters on close.
It uses the existing Store resource enrollment: an in-process fast reader delays
opening, and source-release uncertainty prevents opening.

This is **business-read-only**, not SQLite `mode=ro`: native table initialization
and SQLite coordination can still modify schema or filesystem bytes. Use it on an
offline session, not alongside another process writing that file. It neither borrows
nor finalizes a running owner's Store, and adds no cross-process coordination.

Summary counts enumerate keys rather than decoding event/channel/subagent payloads;
explicit detail and search options still read their contents. Opening an old session
without a persisted event counter can still scan event values during Store construction.
The key-only summary optimization does not remove that separate legacy startup cost.

### Saved Conversation statistics on demand

Opening or refreshing a saved Session Viewer's **Conversation** tab loads the
history index, tree and history page without computing the statistics summary.
The index's `viewer_agents` lists main and attached agents in the same default
order as an unfiltered summary **of the same store**, not a cluster-wide merged
summary. History indexes still address one store; `targets` remains the unchanged
set of legal Conversation targets, including channels. Attached discovery does not make an
attached namespace a new Conversation target.

Switching to Trace, Overview or Cost requests the current summary if needed;
successful or pending summaries are reused within that data refresh. Trace still
waits for the unfiltered summary's default choice unless the user selects an
agent explicitly. Live Inspector summaries and header totals keep refreshing,
even on Conversation. Key-only agent enumeration does not remove old Store
initialization scans when persisted counters are missing.

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

### Web resume preflight metadata reads

`POST /api/sessions/{name}/resume/preflight` automatically attempts a selective,
read-only metadata query for its **local metadata-reading suffix**. No configuration
flag or request parameter is required. The query observes committed SQLite WAL data,
materializes metadata rows, and releases source resources before decoding those rows
in a private in-memory decoder. It does not migrate, checkpoint, or update business
state on the source.

Best effort means **admission-controlled optimization**, not silent error recovery:

- If related enrolled legacy resources (for example, a live Store or Memory index)
  occupy the source, or another fast reader wins admission, the local read uses the
  existing copy-based path. The earlier path-resolution phase can itself wait for an
  active fast reader before reaching this decision.
- Once candidate source SQL has started, a read or decode failure is reported, not
  retried through a copy. Local candidate SQL/decode failures return HTTP 500 with
  `detail.code = session_metadata_read_failed`.
- If source release cannot be established, related enrolled access remains blocked
  for that file identity in the process. The preflight resource-error mapping returns
  HTTP 503 with `detail.code = session_metadata_resource_unavailable`, including when
  a subsequent request encounters that state during path resolution. This is not a
  normal busy response to retry blindly. Stop related work, let workers finish, and
  shut down/restart the process cleanly; restarting is not database repair. Do not
  manually clear occupancy while a source handle or worker may still be alive.
- A private decoder failure after proven source release does not quarantine the source.

This optimization does not extend to shared classification or version-probe reads,
remote/cluster preflight branches, history browsing, other metadata consumers, or the
actual resume operation. It introduces no cross-process safety guarantee. The
[runtime storage boundaries](#runtime-storage-boundaries) above are part of its
supported operating conditions.

### CLI resume

```bash
kt resume --last            # most recent session
kt resume                   # interactive picker (10 most-recent shown)
kt resume my-agent_20240101 # by name prefix
kt resume ~/backup/run.kohakutr
```

Resume is auto-detected: agent sessions mount a single creature; terrarium sessions mount the full wiring and force TUI mode.

Flags the same as `kt run`: `--mode`, `--llm`, `--log-level`, plus `--pwd <dir>` to override the working directory.

After graphs merge, resuming a retired source file follows its recorded successor
to the current graph and its latest conversation. If that graph is already
running in the same engine, resume returns it without rebuilding its creatures.
History viewing and explicit forks still use the file you selected. A missing
successor, unfinished merge checkpoint, or ambiguous later split stops resume
with an error; select the current session explicitly instead of reopening stale
history. Retain referenced successor files when archiving a merged run.

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

## Finding saved sessions in Studio

The recent-session list, Sessions page, and Resume dialog display the saved
terrarium name or primary creature name, with the storage key as a secondary
label. File keys remain unchanged for resume, history, and deletion. Open chat
and inspector tabs refresh their names when runtime metadata arrives, without
closing the tab.

Listing previews use the latest nonempty user message in the primary creature's
saved active conversation, excluding tool feedback. Legacy sessions without a
readable snapshot fall back to their first user-input event. An empty saved
conversation has an empty preview. This is a short text preview, not an AI summary;
compacted snapshots may no longer contain older user messages. The derived index
rebuilds on upgrade; session files are not rewritten for this change.

Use search, session type, and last-active or creation-time sorting on the Sessions
page. The Resume dialog searches the server index and pages through results, so
older sessions are reachable beyond the initial recent page.

## HTTP history pagination

History target endpoints return one bounded page, with a default and maximum
`limit` of 400 records. Clients upgrading from full-history responses must
retain `history_page` alongside the records; a response is not the complete log.

- Live creature: `GET /api/sessions/{session_id}/creatures/{creature_id}/history`.
- Saved target: `GET /api/sessions/{session_name}/history/{target}`.
- Use `stream=events` (the default) for physical event rows in `events`.
  Use `stream=snapshot` for conversation message rows in `messages`. Channels
  return `stream=channel` and place channel records in `messages`.

Saved-session metadata and the available target list come from
`GET /api/sessions/{session_name}/history`; target pages omit the legacy `meta`
field.

Start without a cursor. To load older records, send `before` from the oldest
loaded page; to catch up, send `after` from the newest loaded page. Include the
returned `history_id` and `stream` on continuations. Cursors are opaque and must
be URL-encoded, not parsed or replaced with numeric event IDs. Never send both
`before` and `after`. Continue in each direction while `has_older` or
`has_newer` is true. If `reset_required` is true, discard that stream's cached
range and restart without a cursor or history ID.

`paged=false` and non-positive limits, including `limit=0`, now return HTTP 400.
The live endpoint also rejects `since_event_id`; replace it with the opaque
`after` cursor. A snapshot-only session may return `stream=snapshot` even when
an initial events page was requested; always inspect the response stream.

Keep previously loaded pages when refreshing the head of the same history.
Treat token totals computed from an incomplete loaded range as partial. Large
records may be previews with `_history_truncated` and `_history_detail`; fetch
`history/detail` on the live creature endpoint or `{target}/detail` on the saved
history endpoint with `stream`, `history_id`, and `ref=_history_detail` to expand
one record. For full offline inspection, use `SessionReader` below.

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
