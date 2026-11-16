---
name: search_memory
description: 'Search earlier session events or inspect nearby history using an anchor. Use it to recover dropped context. Not for searching files - use grep.'
category: builtin
tags: [memory, search]
---

# search_memory

Search session events and inspect adjacent context.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| query | string | search | Keyword or natural-language query |
| anchor | string | navigate | Opaque token from search/context |
| before | integer | no | Earlier events, 0–10 (default 3) |
| after | integer | no | Later events, 0–10 (default 3) |
| k | integer | no | Search results (default 5) |
| mode | string | no | `auto`, `fts`, `semantic`, `hybrid` |
| agent | string | no | Search an agent in this session |

Pass exactly one of `query` or `anchor`.

## Behavior

- Search hits include `anchor: ...`; call `search_memory(anchor="...", before=3, after=3)` for adjacent events. Context events have anchors and `has_before`/`has_after` flags.
- Anchors freeze the matched branch lineage. Later branch switches do not change their context. Changed sources and expired anchors return explicit errors.
- `fts`/anchor reads do not load embeddings. `auto`/`hybrid` fall back to FTS on embedding errors; `semantic` errors instead.
- Searches include the selected branch history of the current session, not files. Use `grep` for files.

## Limits

- Read windows have at most 10 events each way, bounded text per event and a bounded history scan.
- Compaction retains raw events; anchors survive unchanged source history.
