# Issue #584 — selected-lineage Search anchors

Status: **local integration candidate**, not a published feature or an approved
replacement for Draft PR #586. Experiment branch:
experiment/issue-584-minimal-anchor, originally based on 09d22a7a1.

## Scope and architectural decision

The creature-facing search_memory tool should return opaque references to
source events, so a hit can be expanded into bounded chronological context
across turns. The user explicitly accepted these two limits:

- **Existing selected-lineage index:** no requirement to search all historical
  branches. A different explicitly saved branch view must not expose siblings
  through an Anchor from an incompatible old index.
- **Explicit migration:** existing indexes may need a manual, offline source
  metadata upgrade. Search still works before migration; such hits do not
  receive an Anchor until their source is certified.

No Replay, Resume, /clear, Compaction, full-branch indexing, embedding model
governance, SessionStore format migration, or global proof state machine is part
of this feature. Cold Resume continues in its own worktrees.

## Source and lifetime contracts

**Indexing** uses the existing store executor to read real physical event keys,
not guessed logical event_ids or the old block_id ordinal. A compatible
event becomes an ephemeral SourcedEvent carrying its exact physical key.
The block extractor attaches that key and a fingerprint to a searchable Block;
existing FTS/Vector metadata stores both. If chronological IDs, ancestry or
physical key structure cannot be proved, ordinary Search retains its prior
behavior but source Anchors are withheld. Normal index updates still follow
the mainline per-Agent cursor.

**Search** returns a hit's source reference and fingerprint. The Navigator
looks up the exact source event and compares its hash, reconstructs the
indexed block with the **same block extractor** as the index,
and checks the selected-lineage view before issuing an opaque process-local
token. Tokens are bounded in number and cleared on Store close.
Unannotated old rows return ANCHOR_MIGRATION_REQUIRED without triggering
an online rewrite or full-history scan. Malformed or stale evidence produces
explicit errors.

**Context expansion** reads the actual source by physical key. It uses a
frozen selected-branch view, filters other branches, and walks canonical
numeric physical event keys in each direction, looking at no more than 256
keys per side for up to 10 neighboring events. Failed append sequence
holes are skipped. A gap or invisible history beyond the budget raises
CONTEXT_SCAN_LIMIT when a requested neighbor cannot be certified. If all
requested events are present but a has-more flag exceeds the scan budget,
that continuity flag is reported as unknown instead of failing the content
read or falsely claiming the history ended. Each displayed content slice
is limited to 900 characters, supports an offset, and returns another
opaque Anchor to continue walking.

A source fingerprint proves only that **that event** is unchanged, not that
all indexed rows or arbitrary out-of-band SQL modifications are globally
certified. No unbounded invalidation responsibility is assigned to the runtime.

## Existing-index upgrade

With *all writers stopped*, and after making a consistent Session/sidecar
backup, the operator can run:

~~~bash
kt migrate-memory-anchors /path/to/session.kohakutr --agent alice
~~~

Repeat for other Agent namespaces. The CLI refuses an already-open Store in
the current process and requests a writer lock. It never runs implicitly in
search_memory. The command only changes **derived FTS/Vector metadata** and
does not need an embedding model or vector re-encoding.

The migration proves file identity, complete source/key alignment, compatible
published FTS and vector cursors **for the target Agent's actual rows**,
source fingerprint, and exact block content. Vectors belonging only to another
Agent do not require the target Agent to have a vector cursor. The original
source sequence is captured before any index preflight and retained as the
operation's boundary. It validates all candidates before writing; per-row
source/index revision
and row checks fence changes during publication. On interruption, patched
rows may remain independently valid. Retrying is idempotent. No global
ready marker or new index format is written. The operator must own an
exclusive, quiescent Store: a migration-only lock would **not** serialize
unrelated older writers or direct SQL modifications.

If the index is stale, incomplete or historically unprovable, migration fails.
The operator must repair or rebuild the original selected-lineage index
under its existing workflow before retrying. This feature does not attempt
to fix Replay or historical index extraction semantics.

## Cost evidence (isolated Windows synthetic measurements)

| Already indexed physical events | FTS search | Anchor registration | Context expansion |
| ---: | ---: | ---: | ---: |
| 200 | 0.170 ms | 0.075 ms | 0.160 ms |
| 1,000 | 0.174 ms | 0.064 ms | 0.140 ms |
| 4,000 | 0.219 ms | 0.076 ms | 0.206 ms |

The initial history-rescanning prototype cost about 27.96 ms to register
and 13.73 ms to expand a 4,000-event anchor. The final path no longer
rereads the entire history **per Anchor action**. These measurements exclude
the mainline live index refresh. At 4,000 events, direct baseline get_events
cost about 9.68 ms, while validated sourced_events cost about 15.28 ms.
The existing Search refresh still has a linear source-read cost; address it
separately only if justified by large-session performance evidence.

The explicit offline FTS metadata backfill cost about 53 ms for 200 events
and 277 ms for 1,000 events in one local run; repeat full validation took
about 12/61 ms. Separate FTS+Vector experiments confirmed unchanged vector
values and embedding call counts. These are feasibility measurements, not
release SLA or proof of unrestricted concurrent migration.

## Release boundaries and Cold Resume coordination

The scoped public API is search_memory(query=...) followed by
search_memory(anchor=..., before=N, after=N, offset=...). Query and anchor
are mutually exclusive; invalid or expired Anchors report diagnostic codes.
Direct Search remains backward-compatible.

Physical source identity and bounded native reads may later become a
tiny shared SessionStore/Cold Resume interface, but that branch's current
reverse cursor still enumerates all keys and does not meet a bounded-key
scan guarantee. This implementation keeps the canonical-key reader local
to Memory and changes no Cold Resume branch or on-disk format.

Before using this candidate to replace PR #586, perform a fresh overall
Review/CI, confirm explicit migration documentation and get the standard
Maintainer scope approval. Historical alternatives and probes remain in
earlier experiment commits.
