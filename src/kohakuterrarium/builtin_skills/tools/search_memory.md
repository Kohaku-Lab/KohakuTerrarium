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

Pass exactly one of `query` or `anchor`.

## Behavior

- Hits include `anchor: ...`; call `search_memory(anchor="...", before=3, after=3)`. Context has anchors and `has_before`/`has_after` flags.
- Anchors freeze lineage. Changed sources or expired anchors return errors.
- `fts`/anchor reads do not load embeddings. `auto`/`hybrid` fall back to FTS on embedding errors; `semantic` errors instead.
- Searches cover this session's selected lineage, not files. Use `grep` for files.

## Limits

- Each side has up to 10 events. Long text exposes content_slice and next_offset; use `search_memory(anchor="...", offset=N, before=0, after=0)`.
- Compaction keeps raw history; valid anchors survive.
