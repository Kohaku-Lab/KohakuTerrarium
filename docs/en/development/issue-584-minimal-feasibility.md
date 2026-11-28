# Issue #584 — source-bound Search Anchors and index rebuild

Status: scoped integration on feat/issue-584-memory-anchors, targeting #605.
The earlier 48-commit, all-branch Draft #586 was superseded. Mainline
selected-lineage Search remains the source of truth for what is searchable.

## Scope and source contracts

The builtin search_memory tool returns opaque, process-local Anchors for hits
whose physical source event and projected block have been independently
verified, regardless of whether selected-lineage membership can be established.
A creature can read bounded preceding/following context and follow continuation
Anchors. Stale, invalid, or unprovable source data is never
silently reinterpreted as a neighboring event.

- New FTS and Vector index records carry exact physical event keys and source
  fingerprints. Both live indexing and the existing offline embedding
  builder use the same sourced-events adapter.
- Anchor issuance checks the exact event and canonical block extractor.
  Expansion uses bounded physical key probes; branch selection is a context
  policy rather than an Anchor admission requirement.
  A matching paragraph proves only *which physical event* to read. Context
  then paginates the **whole original event text**, even when Search indexed
  it as several separate paragraph blocks.
- Physical event reads share the Store reader; strict source decoding for
  rebuild is independent of lineage evidence for Anchors. Non-monotonic
  unique logical event IDs do not invalidate physical provenance.
- Peer hits require only a read of the indexed physical source, without a
  peer history scan or index update. No long-lived lineage evidence is kept.
- The selected scope uses the pinned index view and the mainline ancestry
  compatibility rule. Legacy ancestry is resolved only on demand within the
  first 256 physical key positions, with one request-local prefix projection.
  Beyond that budget, lineage may remain unknown. The raw scope reads
  physical neighbors, including other branches, without lineage inference.
  Both scopes preserve physical event identity and chronological order.
- An active Anchor is promoted ahead of neighbor-token allocation to prevent
  self-eviction at the 2048-token cache limit.
- Expansion is capped to 10 neighboring events per direction, 256 examined
  keys per side, and 900 characters per displayed event slice. Exhausted
  scan budgets return verified content with unknown continuity, not errors.
- New projections omit search_memory call/result echoes; existing indexes
  retain read-time filtering and bounded candidate widening.
- Legacy unannotated index records remain ordinary Search hits but do not
  receive unproven physical Anchors (ANCHOR_REBUILD_REQUIRED). A source-bound
  hit with an unavailable selected view can still be navigated in raw scope.
- No new all-branch Search feature, Replay, Resume, /clear, Compaction or
  Session-format contract is introduced.

## Session-level index maintenance

The previous metadata-only, Agent-scoped migration attempted to prove that
all old indexed rows match today's selected-lineage extraction exactly.
A real Session indexed under the former #586 all-branch prototype disproved
that assumption: 2,087 raw events, 512 FTS rows vs 438 current selected
rows, and 74 old extra tool_result blocks. Its old Vector index contained
414 rows, 20 without Source Refs; its old index manifest marked Vector
invalid. The old migration incorrectly reported one of those FTS blocks
as "not selected", even though its saved physical source was valid.

The replacement user-facing entry point is Session-level:

~~~bash
kt memory rebuild SESSION
kt memory rebuild SESSION --dry-run
kt memory rebuild SESSION --vectors drop
kt memory rebuild SESSION --vectors rebuild --provider sentence-transformer --model @best
~~~

Users do not need to know Agent namespaces or Block IDs. Normal Search never
performs this maintenance implicitly. In a terminal, old vector rows require
an explicit choice to rebuild their embeddings or drop stale semantic
retrieval. In noninteractive mode the choice must be provided by an argument.
No incompatible old vector bytes are silently reused, and re-embedding may
download models or incur API charges.

The operation:
1. Requires all readers and writers to stop. It preserves the supplied
   archive family for ordinary format admission, uses the canonical
   physical path for native operations, and acquires the existing writer
   locks for the known target identities.
2. Inspects existing FTS and vector dimensions using a short exclusive
   ResourceGate lease for the separate SQLite reader. The verified
   logical backup includes committed WAL data. Normal Store/Memory opens
   use their existing native resource lifecycle; there is no file
   replacement or manual sidecar management.
3. Reads all source namespaces strictly before invalidating derived indexes.
   Previously indexed namespaces without physical events are refused.
   Anchor ambiguity alone does not reject a rebuild.
4. Uses the ordinary Memory generation, pending, revision and cursor
   protocol on the original Session. Known invalid rows are excluded
   from ranked retrieval. Embeddings are computed before clearing the
   corresponding old Agent vector rows.
5. Preserves raw events and artifacts. On interruption, an affected
   index can be refreshed or explicitly rebuilt; the earlier backup
   preserves the previous indexes. The operation is not a whole-database
   transaction and does not promise byte-identical failure rollback.

The explicit vector drop policy disables the saved embedding provider and
clears old vector rows. A later explicit embedding build can restore semantic
retrieval. Legacy null model names use provider defaults.

This is offline maintenance; writers and readers not participating in the
existing coordination mechanisms must be stopped by the operator. Rebuild
does not discover arbitrary filesystem aliases or repair Session history.

## Validation and cost

Measured on synthetic Windows histories (already indexed, median of five):

| Events | FTS query | Anchor registration | Context expansion |
| ---: | ---: | ---: | ---: |
| 200 | 0.170 ms | 0.075 ms | 0.160 ms |
| 1,000 | 0.174 ms | 0.064 ms | 0.140 ms |
| 4,000 | 0.219 ms | 0.076 ms | 0.206 ms |

Unlike the initial prototype (27.96 ms registration and 13.73 ms expansion
at 4,000 events), Anchor navigation does not reread the full history per
expansion. The mainline live Search refresh still scans history and has its
own cost, outside #584's performance target.

A copy of the real 2,087-event Session was rebuilt in FTS-only mode after
explicitly dropping invalid old vectors. FTS rows changed 512 -> 438,
Vector rows 414 -> 0; the output could issue and expand real source Anchors.
The event fingerprints matched before/after and the original snapshot was
byte-for-byte unchanged in the earlier staged implementation. These are
historical measurements, not a claim of byte-identical rollback for the
current in-place implementation. Separately,
a deterministic test embedder validated full FTS+Vector reconstruction and
semantic hits without network or paid API calls.

The original user's Session was never modified during these validations.
No future promise is made for arbitrary malformed imports or out-of-band
uncoordinated SQL mutations. Unknown lineage is not guessed; raw context
remains available when physical identity is verifiable.

## Coordination and review

Cold Resume owns its own physical event reading and shutdown/resume concerns.
The Memory-only physical-key scan can later reuse a bounded Store cursor,
but #584 does not depend on Cold Resume's checkpoint lifecycle or format.

The upstream Review and CI should focus on recoverable index maintenance,
vector-policy UX, unambiguous source proof, selected-lineage behavior,
old-index recovery and bounded context navigation, not on unrelated
Compaction/Resume repairs.
