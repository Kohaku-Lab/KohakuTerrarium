# History Semantics: Strict Memory and Replay Compatibility

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
records (including unusual branch mappings). This contract does not silently redefine
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

Accepted (0, 0) bookkeeping text, tool calls/results and processing_end
events follow this same unambiguous Round-continuation rule: one open
scoped Round may own them, but several open sibling Rounds authorize no
guess. Their recorded (0, 0) coordinates remain unchanged for visibility
and provenance, and a bookkeeping completion closes only its matched
Round. Full and incremental Memory extraction must agree.

After ownership transfers, a stamped processing_end must close that round:
an unstamped orphan after it is *not* part of the completed round. The inverse
transition (stamped opener, unstamped tail) continues to use only the uniquely
open scoped round. Multiple open sibling scopes never authorize guessing.

Incremental extraction stores the round, block, in_round, open_branches
continuation cursor and a fixed-size digest of the last **eligible**
deduplication predecessor. Invalid or unassignable events cannot update that
digest, including at batch boundaries. No second persisted state machine is
introduced. The same history must give the same blocks whether extracted once
or appended in arbitrarily sized batches.

## Index migration

The derived all-branch memory index uses **format 3**. The legacy
selected-lineage format 1 and locally tested all-branch format 2 are both
rebuilt under the existing Session-wide lifecycle. Format 3 preserves strict
source coordinates, physical references, ancestry-aware round state and
bounded deduplication, and additionally indexes explicitly stamped tool
results that arrive after processing_end. Ordinary text still requires an
open Round; missing/invalid lineage cannot authorize an orphaned result.
A previously READY v2 cursor may have counted the omitted result as indexed,
so automatic format migration must rebuild FTS and vectors from source.
The SessionStore .kohakutr physical format and original event log do not change.
A stale index must fail closed until rebuilt.

Memory Index Lifecycle owns when physical materializations are safely invalidated
and published. Strict History projection owns what the raw events mean before
materialization.

## Selection eligibility contract

Strict Memory branch resolution excludes INVALID coordinate records **before**
calling the legacy Replay branch resolver or inferring ancestry. This is
different from filtering ranked hits after selection: invalid booleans,
partial coordinates or malformed openers must not change which of the valid
events is selected. The public Replay API retains its historical compatibility
semantics; this stricter input boundary is specific to Memory search.


## Modern writes versus legacy fallback

Ordinary accepted user input is stamped before persistence: the Agent event
consumer allocates (turn_index, branch_id, parent_branch_path) and then
writes the canonical user_input / user_message pair. Regeneration and
editing likewise persist their selected turn/branch. Queued ingress need not
have a branch until the consuming turn is chosen. The generic low-level
SessionStore.append_event() intentionally retains optional coordinates for
legacy imports and non-conversation bookkeeping.

An unstamped user_input in a modern-format file is therefore *not* a new
normal-writing convention. Memory's existing legacy compatibility parser
may see a stamped turn followed by an unstamped historical opener; the
completed stamped opener advances the legacy round high-water so a later
unambiguous stamped continuation can hand off without losing text/tools.
No second global/sibling round-state machine is introduced.

For strict Memory search, illegal coordinate pairs are ineligible **both**
for branch selection and for inferred lineage. The canonical eligibility
projection retains original physical positions and feeds one ancestry
inference shared by Index and Context Navigation. The permissive public
Replay behavior for noncanonical historical records remains unchanged.


## Strict Memory round ancestry

Memory Index, Search branch selection and Context Navigation use one strict
physical-position-preserving ancestry projection. Replay continues using its
existing public compatibility inference; this does not rewrite source events.

An open scoped round is identified by **(turn, branch, ancestry)**. Mirrored
or imported children can reuse the same (turn, branch) under different roots;
a fully stamped processing_end must close only its own lineage, even when an
older completion arrives after a newer child has opened. The persisted
open_branches cursor encodes this composite identity without a new field.

A user_message paired with an already open user_input is a **continuation**,
not a second opener. Missing/null parent paths on such paired messages and on
ordinary tool/text continuations inherit the most recently reopened matching
(turn, branch) lineage, preserving existing legacy physical-order behavior.
A processing_end with no ancestry and multiple matching live lineages cannot
safely choose which one to close; it is left unassigned. Explicit ancestry
wins over the physical-order fallback.

An unstamped legacy opener handing off to stamped text/tools remains supported;
the subsequent missing-path tail inherits that stamped ancestry. All of these
decisions are recomputed from eligible original positions when required.
Incremental and full materialization must return identical blocks, and neither
a malformed coordinate nor an ambiguous completion may mutate a valid round.


## Compaction evidence versus ordinary event visibility

A compact_complete or compact_replace recorded on a deselected tail can
still summarize **unchanged selected ancestors**. Its eligibility is governed
by compact_replacement_rules() and the recorded compact_source_path, not
by the visibility of the completion event's own turn/branch. Incompatible
source dependencies are never reused. The visible user/tool event stream and
the full compaction-rule evidence therefore have separate inputs during
settled-tool replay and context-cutoff snapshot repair; discarded tail
messages must never be reintroduced just to recover a summary.

**Checkpoint representation freshness:** a branch snapshot or legacy
snapshot+tail resume cannot be reused when later compatible compaction
evidence changes the represented prefix. Both paths consult one
non-persistent Replay watermark query. ID-based checkpoints require
**strictly increasing positive event IDs in physical source order**; repeated,
missing, or out-of-order imported logical IDs require full source Replay,
even when a saved summary appears to match. A matching summary alone cannot
certify all other source events. The fallback is full source Replay, not silent
raw-history expansion.

**Physical coverage:** the public compact-covered ID set retains its
legacy logical-ID contract. Internal selected Replay, settled-tool
normalization and Snapshot checks use an ephemeral per-physical-event
coverage decision when mirrors reuse an ID. A covered ancestor must not
hide a selected sibling's tool call or result; provenance annotations are
never persisted to the source event log. Strict raw-history mutations
already reject ambiguous duplicate logical IDs rather than guessing.

**Settlement ownership:** cross-branch late tool results require uniqueness
of the issuing call over the original source, not merely the already
projected tail. Snapshot cutoff repair passes source evidence separately
for both summary dependencies and tool-call identity without admitting
foreign messages. An explicit malformed non-null compact_source_path
never downgrades to legacy compact_path authorization; explicit null
retains its old unknown-source compatibility behavior.

## Consumer authority

Branch visibility, physical event identity and conversational Round ownership
are related but not interchangeable. Consumers share the **projection
primitive**, while preserving their established compatibility policies:

| Consumer | Projection authority | Identity and recovery contract |
| --- | --- | --- |
| Memory Search / Index / Anchor navigation | `eligible_branch_history` + `index_eligible_parent_paths_by_position` | Strict coordinates and composite (turn, branch, ancestry) Round ownership; invalid or ambiguous events fail closed |
| Public Replay and branch metadata | `index_parent_paths_by_position` + `select_live_event_positions` | Retain legacy Replay permissiveness; never rewrite source data |
| SessionReader turns; stdout, TUI, Rich CLI, ktui and headless Terrarium resume display | `project_live_event_stream` | Consume physical selected events, not an `event_id` membership set; presentation grouping remains local |
| Edit/Rerun raw prefix; user-turn content lookup; snapshot user metadata backfill | Positional Replay projection and existing target validity checks | Reused (turn, branch, event_id) cannot borrow another ancestry; raw edit still validates original source and ambiguous IDs |
| Checkpoint resume, tool settlement, history image repair | Physical source membership plus provider/job identity | A tool result may settle a live call from another turn or branch; retain verified settlement evidence but no foreign content |
| Canonical assistant start/end projection | Shared `_coerce_path` over recorded ancestry | Compare persisted (turn, branch, path) equality; no inferred lineage or new error policy |
| `select_live_event_ids` and selected-only v1 indexes | Existing logical-ID contract | Do not use an ID set to filter source event streams where logical IDs may collide |

One global `round_id` or universally strict Replay rule is intentionally not
introduced: `user_input` opens the Memory extraction Round, `user_message`
is normally its paired continuation, `assistant_round_start` and
`assistant_message` form provider-response correlation, while delayed job
settlement follows call identity. They share recorded coordinates, not one
interchangeable lifecycle.

Cross-consumer tests cover mirrored duplicate logical IDs across Reader,
resume surfaces, edit targeting, Memory, and settled tool calls, alongside
late-result repair and full/incremental Memory extraction parity. The
source format, public Replay interfaces, and Memory v2 contract remain intact.
