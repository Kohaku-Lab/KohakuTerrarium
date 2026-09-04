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
| limit | integer | no | Maximum results |
| mode | string | no | `keyword`, `semantic`, or `hybrid` |

## Behavior

- Keyword search is always available; semantic search needs an embedding
  provider and degrades to keyword when none is configured.
- Only the current session is searched, and only what was recorded before now.

## Limits

- Compaction summarizes rather than deletes, so older turns stay findable here
  after they leave your context.
