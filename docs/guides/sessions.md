# Sessions

For readers persisting, resuming, or archiving agent runs.

A session captures the operational state of a run — conversation, events, sub-agent conversations, channel history, scratchpad, jobs, resumable triggers, and config metadata — as a single `.kohakutr` file. You can stop a creature at any point and resume exactly where it left off.

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

The format is append-only for event data and versioned through KohakuVault's auto-pack. You can safely copy, archive, or email a `.kohakutr` file; there's nothing external it depends on.

## Where sessions live

```
~/.kohakuterrarium/sessions/<name>.kohakutr
```

`<name>` is auto-generated from the creature/terrarium name plus a timestamp. Override with `--session <path>` or opt out with `--no-session`.

## What persists

On each turn KohakuTerrarium records:

- **Conversation snapshots** — raw message dicts via msgpack. Preserves `tool_calls`, multimodal content, and metadata.
- **Event log** — one entry per chunk, tool call, sub-agent output, trigger fire, channel message, compact, interrupt, or error. This is the canonical history.
- **Sub-agent conversations** — saved before the sub-agent is destroyed, so you can inspect what it did after the fact.
- **Scratchpad and channel messages** — per-agent and per-channel.
- **Job records** — outputs of long-running tools and sub-agents.
- **Resumable triggers** — any `BaseTrigger` subclass with `resumable: True` serializes to `state` and restores on resume.
- **Config snapshot** — the fully-resolved config at run time, so resume can rebuild the agent even if the on-disk config changed.

## Resuming

```bash
kt resume --last            # most recent session
kt resume                   # interactive picker (10 most-recent shown)
kt resume my-agent_20240101 # by name prefix
kt resume ~/backup/run.kohakutr
```

Resume is auto-detected: agent sessions mount a single creature; terrarium sessions mount the full wiring and force TUI mode.

Flags the same as `kt run`: `--mode`, `--llm`, `--log-level`, plus `--pwd <dir>` to override the working directory.

What resume does:

1. Reads the config snapshot from `meta`.
2. Reloads the current on-disk config (so prompt/tool changes you made since take effect).
3. Merges: config snapshot provides the session identity; current config provides the running logic.
4. Rebuilds the agent, attaches the same `SessionStore`, reinjects the conversation snapshot, replays scratchpad/channel/trigger state.
5. Starts the controller fresh; previous events are in context.

This means small config drift is fine (swapping an LLM, changing a prompt). Structural drift (renaming the creature, removing a tool it was actively using) can cause replay errors — pin a session to its original config if you need perfect fidelity.

## Interrupt and resume workflow

```bash
kt run @kt-defaults/creatures/swe
# work... then Ctrl+C
# later:
kt resume --last
```

The agent exits gracefully on Ctrl+C: finishes the in-flight tool, flushes the session store, and prints a resume hint. Forced kills (SIGKILL) skip the final flush but most recent state is still on disk thanks to append-only writes.

## Copying or archiving sessions

```bash
# Backup
cp ~/.kohakuterrarium/sessions/swe_20240101.kohakutr ~/backups/

# Resume from a moved location
kt resume ~/backups/swe_20240101.kohakutr

# Inspect without a full resume (read-only CLI coming; for now use Python)
```

Programmatic inspection:

```python
from kohakuterrarium.session.store import SessionStore
store = SessionStore("~/backups/swe_20240101.kohakutr")
print(store.load_meta())
for agent, event in store.get_all_events():
    print(agent, event["type"])
store.close()
```

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

Compaction runs in the background (see [concepts/modules/memory-and-compaction](../concepts/modules/memory-and-compaction.md)) — the controller keeps running; when the new summary is ready, the conversation is swapped. Each compaction is logged as an event.

Manual compaction:

```
/compact
```

from the CLI/TUI prompt. Useful before handing off a long session or shipping it as context into another run.

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
kt run @kt-defaults/creatures/swe --no-session
```

No `.kohakutr` is created. This also disables compaction's ability to recover previous rounds from disk (it still compacts in memory).

## Troubleshooting

- **Compaction runs forever / OOMs.** The compact model is the same heavy controller model. Set `compact_model` to something cheap (`gpt-4o-mini`, `claude-haiku`).
- **Resume errors with `tool not registered`.** The creature config changed (a tool was removed) but the conversation still references it. Manually edit `config.yaml` to re-add the tool, or start a fresh session.
- **`kt resume` can't find a session I just saw.** Sessions are resolved by prefix against filenames in `~/.kohakuterrarium/sessions/`. If you renamed the file or moved it, pass the full path.
- **Large `.kohakutr` files.** The event log is append-only; long sessions grow. Archive old ones or split work across sessions. Compaction shrinks the live conversation but keeps the full event history for search.
- **Sub-agent output missing from resume.** Sub-agent conversations are saved when the sub-agent completes. If the parent was interrupted mid-sub-agent, the latest snapshot is whatever was persisted at the last checkpoint.

## See also

- [Memory](memory.md) — FTS, semantic, and hybrid search over session history.
- [Configuration](configuration.md) — compaction recipes and session flags.
- [Programmatic Usage](programmatic-usage.md) — `SessionStore` API for custom inspection.
- [Concepts / memory and compaction](../concepts/modules/memory-and-compaction.md) — how compaction works.
