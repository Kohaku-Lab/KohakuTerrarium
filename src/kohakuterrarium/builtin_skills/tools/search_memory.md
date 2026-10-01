---
name: search_memory
description: Search this session's earlier events by keyword or meaning. Use to recall details already dropped from context. Not for searching files - use grep.
category: builtin
tags: [memory, search]
---

# search_memory

Searches the recorded event log of the current session.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| query | string | yes | Search text |
| k | integer | no | Maximum results (default 5) |
| mode | string | no | `auto` (default), `fts`, `semantic`, or `hybrid` |
| agent | string | no | Filter results by agent name |

## Behavior

- `fts` never initializes or calls an embedding model. `auto` and `hybrid`
  fall back to keywords when embedding fails or exceeds its wait limit;
  the result reports the fallback. Explicit `semantic` returns an error.
- Model work does not block session history, persistence, or shutdown.
- Only the current session is searched, and only what was recorded before now.

## Limits

- Compaction summarizes rather than deletes, so older turns stay findable here
  after they leave your context.
