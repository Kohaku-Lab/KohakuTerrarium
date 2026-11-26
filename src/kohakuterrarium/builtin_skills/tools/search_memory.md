---
name: search_memory
description: 'Search this session''s history by keyword or meaning. Use it to recall prior turns and explore context via anchors. Not for files - use grep.'
category: builtin
tags: [memory, search]
---

# search_memory

Searches the recorded event log of the current session.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| query | string | conditional | Search text; supply exactly one of query or anchor |
| anchor | string | conditional | Opaque cursor from a preceding search/context window |
| before / after | integer | no | At most 10 neighboring events on either side (default 3 each) |
| offset | integer | no | Continue reading a long anchored event from a character offset |
| k | integer | no | Maximum results (default 5) |
| mode | string | no | `auto` (default), `fts`, `semantic`, or `hybrid` |
| agent | string | no | Filter results by agent name |

## Behavior

- `fts` never initializes or calls an embedding model. `auto` and `hybrid`
  fall back to keywords when embedding fails or exceeds its wait limit;
  the result reports the fallback. Explicit `semantic` returns an error.
- Model work does not block session history, persistence, or shutdown.
- Only the current session is searched, and only what was recorded before now.

- Verified results carry Anchors; pass one back with before/after to inspect
  bounded context and continue walking.

## Limits

- Compaction summarizes rather than deletes, so older turns stay findable here
  after they leave your context.

## Reference

To inspect one search hit's context, call
`search_memory(anchor=..., before=N, after=N, offset=N)` and follow the
returned continuation Anchors. Navigation is restricted to the selected indexed
branch; a legacy index must first be upgraded by the operator via
`kt memory rebuild SESSION`, after closing its writers.
Stale, invalid and unverifiable source references return explicit errors.

- Search calls and their own tool-result echoes do not take up the ranked
  results, although their recorded events remain intact.
- A background tool's output might be recorded after a conversational round
  closes and not be directly searchable. When a tool-call result is found,
  use its Anchor with `before=0, after=3` to inspect the original outcome,
  then follow neighboring Anchors as needed. Do not infer missing source text
  from a negative direct search.
- For a long event whose context includes `next_offset=N`, use the **same
  event's anchor** with `before=0, after=0, offset=N` for the next slice.
