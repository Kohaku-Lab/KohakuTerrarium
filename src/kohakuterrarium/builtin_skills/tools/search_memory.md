---
name: search_memory
description: 'Search this session''s history by keyword or meaning. Explore context via anchors; scope=raw reads physical history. Not for files - use grep.'
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
| scope | string | no | Anchor context only: `selected` (default) or `raw` |
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
returned continuation Anchors. An Anchor verifies the physical event, not its
branch membership.

- `scope="selected"` follows the branch view pinned when the Anchor was
  issued. Unknown lineage can limit the returned context.
- `scope="raw"` reads the same Agent's physical context in recorded order,
  including other branches and events of unknown lineage. Turn/branch
  coordinates are displayed; these are not a claim of selected membership.
  Keep `scope="raw"` when following raw continuation Anchors.

Scope does not change query-mode search or its index. Legacy hits without
physical source references still require explicit offline
`kt memory rebuild SESSION`. Stale, invalid or misbound source references
return explicit errors in either scope.

- Search calls and their own tool-result echoes do not enter new Memory
  indexes and are filtered from existing indexes; recorded events remain intact.
- A context scan may return fewer neighbors than requested, with
  `has_before=unknown` or `has_after=unknown` when the scan or lineage-proof
  budget is reached. Try raw context to inspect events whose lineage cannot
  be established within that budget.
- If candidate filtering hits a backend limit, available matches are returned
  with a **nonfatal incomplete-results warning**. Even with zero matches, this
  means no eligible hits were found *within the scanned candidate window*.
- A background tool's output might be recorded after a conversational round
  closes and not be directly searchable. When a tool-call result is found,
  use its Anchor with `before=0, after=3` to inspect the original outcome,
  then follow neighboring Anchors as needed. Do not infer missing source text
  from a negative direct search.
- For a long event whose context includes `next_offset=N`, use the **same
  event's anchor** with `before=0, after=0, offset=N` and the same scope for
  the next slice.
