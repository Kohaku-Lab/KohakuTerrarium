# Canonical History Semantics Boundary (R2)

The append-only SessionStore events are authoritative. An event's physical
identity, conversation-round membership and branch visibility are **different
facts**; interpreting one must not silently invent or discard the others.

## Shared coordinate classification

The History semantics module provides a pure branch-coordinate classifier:

| Raw coordinates | Classification | Scoped memory search |
| --- | --- | --- |
| Both missing (None, None) | Legacy, unscoped | Visible |
| (0, 0) | Global bookkeeping | Visible |
| Positive integers (turn, branch) | Explicit branch | Selected view + ancestry check |
| (0, 1), (1, 0), negative values | Invalid | Excluded |
| One missing, non-integers, booleans | Invalid | Excluded |

Memory extraction retains the raw coordinate pair in block metadata, including
invalid ones; it never erases a zero value into None. A malformed source row
is not allowed to become an unscoped hit. Both selected-only and all-branch
memory extraction apply the strict search policy.

**Replay compatibility:** the long-standing public replay/selection methods
retain their established compatibility behavior for historical, noncanonical
records (including unusual branch mappings). R2 does not silently redefine
their output: the canonical classifier is shared, while strict memory-search
eligibility is a distinct policy. For *valid, unambiguous* histories, replay
selection and indexed branch visibility must agree. Changing legacy Replay
compatibility further requires a separate explicit migration decision.

## Legacy-to-stamped round continuation

An unstamped user input can open a conversational round, and its later
assistant text or tool events may contain explicit (turn, branch) coordinates.
When there is exactly one open legacy round, no competing scoped round, and
the stamped turn matches that legacy round, the continuation inherits its
round membership. The original branch coordinates of each event stay intact.

After ownership transfers, a stamped processing_end must close that round:
an unstamped orphan after it is *not* part of the completed round. The inverse
transition (stamped opener, unstamped tail) continues to use only the uniquely
open scoped round. Multiple open sibling scopes never authorize guessing.

Incremental extraction stores only the existing round, block, in_round,
and open_branches continuation cursor. No second persisted state machine is
introduced. The same history must give the same blocks whether extracted once
or appended in arbitrarily sized batches.

## Index migration

Pre-R2 all-branch indexes (internal format 5) can contain irreversible lossy
normalizations. R2 advances the internal **memory index** contract to format
6 and rebuilds derived FTS/vector rows before scoped retrieval; it does **not**
change the SessionStore .kohakutr physical format or rewrite the event log.
A stale index must fail closed until rebuilt.

R1 owns when physical materializations are safely invalidated and published;
R2 owns what the raw source events mean before materialization.

## Selection eligibility contract

Strict Memory branch resolution excludes INVALID coordinate records **before**
calling the legacy Replay branch resolver or inferring ancestry. This is
different from filtering ranked hits after selection: invalid booleans,
partial coordinates or malformed openers must not change which of the valid
events is selected. The public Replay API retains its historical compatibility
semantics; this stricter input boundary is specific to Memory search.
