# Issue #584 — source-bound Search Anchors and index rebuild

Status: scoped integration on feat/issue-584-memory-anchors, targeting #605.
The earlier 48-commit, all-branch Draft #586 was superseded. Mainline
selected-lineage Search remains the source of truth for what is searchable.

## Scope and source contracts

The builtin search_memory tool returns opaque, process-local Anchors for hits
whose physical source event and projected block have been independently
verified. A creature can read bounded preceding/following context and follow
continuation Anchors. Stale, invalid, or unprovable source data is never
silently reinterpreted as a neighboring event.

- New FTS and Vector index records carry exact physical event keys and source
  fingerprints. Both live indexing and the existing offline embedding
  builder use the same sourced-events adapter.
- Anchor issuance checks exact event content, selected-lineage visibility and
  the canonical block extractor; expansion uses bounded physical key probes.
  A matching paragraph proves only *which physical event* to read. Context
  then paginates the **whole original event text**, even when Search indexed
  it as several separate paragraph blocks.
- Peer-Agent hits from an unfiltered Search are certified on demand only for
  namespaces actually returned and not yet certified in the live session.
  A failure in this extra certification withholds Anchors but preserves the
  ordinary Search hits; unrelated Agent histories are not scanned.
- An active Anchor is promoted ahead of neighbor-token allocation so its own
  expansion cannot evict it when the 2048-token process cache is at capacity.
- Expansion is capped to 10 neighboring events per direction, 256 examined
  keys per side, and 900 characters per displayed event slice. Continuity can
  be unknown when the source budget cannot determine whether more history
  exists; requested unavailable neighbors still return a scan-limit error.
- Legacy unannotated or all-branch index records remain ordinary Search hits
  but do not receive unproven Anchors. They report ANCHOR_REBUILD_REQUIRED.
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
1. Acquires the existing process-wide native occupancy gate *exclusively*
   before source inspection or rebuild and holds it through publication. This
   also excludes standalone SessionMemory and other KohakuVault handles not
   registered as live SessionStore instances, while preventing new local
   opens during the rebuild. A separate Session writer lock covers cooperating
   processes; the operator must still stop unrelated external readers/writers
   that do not participate in that lock. Historical read-only opens can leave
   a zero-byte SQLite WAL and 32 KB shared-memory sidecar. After acquiring
   the writer lock, a zero-frame SQLite TRUNCATE checkpoint may retire these
   idle sidecars; an uncheckpointed WAL or still-live handle is refused.
   Read-only inventory must never create or remove sidecars.
2. Inspects old FTS and vector counts without materializing old indexes.
   A destructive rebuild uses strict physical event decoding: an unreadable
   event rejects publication instead of being reclassified as an empty Agent.
   Normal Search still uses its existing tolerant fallback and withholds
   unverifiable Anchors.
   Before opening the staged copy, the existing Session
   `require_writable_format()` gate validates the **original archive family**
   under the existing writer lock (and before native exclusive occupancy
   admission, because the format reader itself uses a legacy lease).
   Rebuild refuses to replace a previously nonempty Agent FTS/vector index
   when that Agent has no physical source event keys; it does not assert
   completeness or attempt repair of the Session event log.
3. Takes a same-directory stage copy of the database, rebuilding **only the
   derived Memory indexes** in that copy for all discovered Session agents.
   It clears old index cursors, manifest, FTS and known vector tables; it
   preserves Session events, conversation, snapshots and artifact storage.
4. Uses the physical Source Ref reader for both selected-lineage FTS and any
   newly recomputed vectors. It checks all published FTS metadata and compares
   physical event fingerprints before and after staging.
5. Keeps a backup of the original Session file, then publishes the staged
   database using one atomic file replacement; a staging/model failure
   leaves the original in place.

The vector drop choice also sets the saved embedding provider to none so
a later semantic search does not silently re-encode the discarded index.
A subsequent explicit embedding build can restore semantic retrieval.
Legacy configs with a provider but a null model select that provider's
default. The saved Embedding config is read from the isolated staging copy;
model setup failure cannot leave a new shared-memory sidecar on the source.
Provider import/model load failures are reported by CLI rather than an
uncaught traceback.
Legacy configurations with a provider but `model=None` use that provider's
default model; saved embedding settings are read from the staged copy so a
failed model initialization cannot reintroduce sidecars on the source.
Provider import/model setup exceptions are reported by CLI, not uncaught.

This is not a concurrent live database migration; the same operation must
not race unrelated processes that ignore the Session writer lock. Byte-for-
byte restoration of the old file remains possible using the emitted backup.

## Validation and cost

Measured on synthetic Windows histories (already indexed, median of five):

| Events | FTS query | Anchor registration | Context expansion |
| ---: | ---: | ---: | ---: |
| 200 | 0.170 ms | 0.075 ms | 0.160 ms |
| 1,000 | 0.174 ms | 0.064 ms | 0.140 ms |
| 4,000 | 0.219 ms | 0.076 ms | 0.206 ms |

Unlike the initial prototype (27.96 ms registration and 13.73 ms expansion
at 4,000 events), Anchor navigation no longer rereads all historical events
per hit. The mainline live Search refresh still scans history and has its
own cost, outside #584's performance target.

A copy of the real 2,087-event Session was rebuilt in FTS-only mode after
explicitly dropping invalid old vectors. FTS rows changed 512 -> 438,
Vector rows 414 -> 0; the output could issue and expand real source Anchors.
The event fingerprints matched before/after and the original snapshot was
byte-for-byte unchanged. A verified original backup was retained. Separately,
a deterministic test embedder validated full FTS+Vector reconstruction and
semantic hits without network or paid API calls.

The original user's Session was never modified during these validations.
No future promise is made for arbitrary malformed imports or out-of-band
uncoordinated SQL mutations: such histories are refused, not guessed.

## Coordination and review

Cold Resume owns its own physical event reading and shutdown/resume concerns.
The Memory-only physical-key scan can later reuse a bounded Store cursor,
but #584 does not depend on Cold Resume's checkpoint lifecycle or format.

The upstream Review and CI should focus on crash-safe index publication,
vector-policy UX, unambiguous source proof, selected-lineage behavior,
old-index recovery and bounded context navigation, not on unrelated
Compaction/Resume repairs.
