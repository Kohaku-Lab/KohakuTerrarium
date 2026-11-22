---
name: search_memory
description: 'Search earlier session events or inspect nearby history using an anchor. Use it to recover dropped context. Not for searching files - use grep.'
category: builtin
tags: [memory, search]
---

# search_memory

Search indexed session history and nearby events.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| query | string | search | Keyword or natural-language query |
| anchor | string | navigate | Opaque token from search/context |
| before | integer | no | Earlier events, 0–10 (default 3) |
| after | integer | no | Later events, 0–10 (default 3) |
| offset | integer | no | Character offset within anchored event |
| k | integer | no | Search results (default 5) |
| mode | string | no | `auto`, `fts`, `semantic`, `hybrid` |
| agent | string | no | Search an agent in this session |
| include_search_history | boolean | no | Include own calls/results for debugging (default false) |

Pass exactly one of `query` or `anchor`.

## Behavior

- Hits have anchors. Expand if needed; `has_before`/`has_after` permit navigation from the first/last event anchor.
- Anchors freeze lineage. Changed sources or expired anchors return errors.
- FTS/anchor reads need no embeddings; `auto`/`hybrid` can fall back to FTS; `semantic` errors.
- Search only this session's selected lineage, not files; use `grep` for files.

## Limits

- Each side has up to 10 events. Long text exposes content_slice and next_offset; use `search_memory(anchor="...", offset=N, before=0, after=0)`.
- Tool results index the first 50,000 chars; anchors read the full raw event.
