# Session Memory Index Reconciliation

**Scope:** derived memory indexes inside one .kohakutr SessionStore.
The append-only source events remain authoritative; the index is disposable.
A Terrarium SessionStore can contain multiple Creature event namespaces, but
its active vector corpus uses one embedding model.

## Responsibilities

| Owner | Decisions | Must not do |
| --- | --- | --- |
| SessionIndexReconciler (memory/reconcile.py) | Discover Agents, capture detached source events, select FTS/vector participants and query projections | Mutate physical tables or select a different model |
| SessionMemory._prepare (memory/index.py) | Plan an incremental batch or a reset from events and checkpoint state | Modify cursors, delete rows or publish index state |
| MemoryIndexLifecycle (memory/lifecycle.py) | Acquire canonical Session locks, invalidate and stage resets, validate and publish readiness | Read another Agent's source or run embedding |
| FTS / vector materializers (memory/index.py) | Execute staged plans and commit their checkpoints | Delete or replace rows while the corresponding index is ready |
| Live / CLI / Studio / SessionReader | Request FTS or vector materialization, specify Agent/branch visibility | Choose which other Agents to refresh or promote an index |

**Materialization participants** and **query visibility** are distinct. A
vector rebuild may include every historical Agent, but the returned search
results remain scoped to the caller's selected Agent and branch lineage.

## Protocol

1. **Capture:** Read detached events for each participating Agent through
   SessionStore.get_memory_events(). A checkpoint records the event count,
   final-event anchor and source-reference completeness. The same source
   snapshot is reused for its FTS and vector materialization.
2. **Plan:** Decide append versus reset without modifying persistent state.
   Source-reference upgrades, changed anchors, generation changes and
   interrupted batches count as planned mutations.
3. **Invalidate:** Under the short Session mutation lock, acquire build
   ownership if needed, and persist building before any row deletion or
   insertion. Full format/model migrations invalidate the relevant index.
4. **Materialize:** Apply FTS plans and checkpoints. On full vector rebuild,
   first refresh every vector participant's FTS from the same captured source
   snapshot; encode outside the short mutation lock while retaining the build
   ownership.
5. **Validate / publish:** Vector checkpoints must match the Session epoch,
   vector generation, embedding identity, and the corresponding Agent's FTS
   cursor revision, count, anchor and source-reference completeness.
   Every participant, including an empty Agent, must finish before ready.

New source events can arrive during embedding. An earlier captured snapshot
is still usable if its FTS cursor has not advanced. If another reconciliation
has advanced that cursor, reject the prepared vector commit and retry against
a fresh snapshot. This avoids blocking writers or endlessly restarting model
calls merely because new source events exist.

## Lock and recovery semantics

- Canonicalize the physical Session path before constructing sidecar locks;
  symlink spellings must contend on the same build and mutation ownership.
- Short mutation locks cover native table and state writes. Build ownership
  spans a multi-Agent rebuild and its embedding calls; it does not authorize
  reading a building index.
- A crash after invalidation leaves the corpus unavailable. On reopening,
  recovery starts a new build generation. Physical row counts never prove
  readiness.
- FTS-only requests do not compute embeddings. Retired and empty Agent
  namespaces receive zero-count checkpoints, avoiding indefinite pending.
- Old vector tables at other dimensions may remain physically present, but
  are ineligible for search under the active Session manifest.


### Build ownership termination

Long FTS/vector build locks represent **one logical Session-wide build**,
not one Agent call. An Agent can successfully materialize its partial batch
while the Manifest still lists other pending Agents; the current builder
keeps ownership until the final checkpoint is validated and published.

For abnormal exits, MemoryIndexLifecycle owns termination. A build
mutation catches failures while holding the short mutation lock, marks any
owned FTS/vector builds unreadable, and releases their long locks even if a
second manifest write fails. The FTS recovery path resets the full index
epoch on the next independent builder; vector recovery also advances its
generation and rejects late encoding commits.

Synchronous vector encoding runs outside the short mutation lock, and
cross-Agent progress callbacks run outside an individual materializer.
Those outer boundaries delegate exceptional termination to the same
Lifecycle abort operation. Live semantic cancellation continues to use the
affinity-thread vector abort after detached embedding has been fenced.
No successful *partial* Agent call releases a long build lock prematurely.

Regression probes inject FTS insert/clear, forced reset, model encoding,
vector insertion, multi-Agent callback errors, and abort-persistence failure.
They verify the original error survives, invalid indexes remain unreadable,
both owned locks are released, and an independent SessionMemory can recover
without waiting for the original object to close.

## Regression contract

Every query must **either read an internally consistent published snapshot
or fail explicitly**. Representative injected faults are source-reference
upgrades interrupted after row deletion, stale model results after FTS
advancement, an Alice-only search requiring Bob's keyword refresh, symlinked
path lock contention, and interrupted partial vector builds.

Not promised: zero-downtime rebuilding, instantaneous freshness after every
append, a globally atomic snapshot across live writers, or cross-model
vector ranking fusion.

## End-to-end contract closure

- **Restorable embedding specification:** the model factory normalizes unset
  optional configuration values and resolves provider, model defaults and
  presets. Offline builds persist the effective model specification obtained
  after constructing the embedder, not optional user inputs containing null.
  Search and resume pass saved specifications through the same factory; the
  stored vector identity still gates search if the loaded model differs. A
  failure to persist the model specification fails the build explicitly.
- **Aborted build ownership:** a live semantic operation keeps its build
  ownership across encoding. Failure, timeout and cancellation route through
  one abort operation on the store affinity thread, which increments the
  vector generation, leaves the manifest unreadable and releases the long
  build lock. Detached model workers may finish later, but an old prepared
  vector batch cannot commit against the new generation. An independent
  builder may recover the Session immediately.
- **Strict search lineage:** Memory projects only events with canonical,
  eligible coordinates when deriving a selected branch and its inferred
  ancestry. The source event log and public Replay compatibility are not
  changed. Filtering invalid hits only after branch selection is insufficient:
  a malformed opener must not influence selection of valid siblings.


### Effective config equivalence after offline builds

SessionStore.claim_embedding_config() serializes competing Creature claims
without importing a model runtime. A lightweight embedding-spec normalizer
compares the saved concrete model against the incoming default/preset/explicit
configuration. An omitted model inherits the stored model for that provider;
a genuinely different explicit model remains a conflict. The Embedder factory
shares the same pure normalization rules before loading any model, while
offline index builds persist the concrete identity after construction.
This prevents a successful offline build from blocking an unchanged Creature
configuration on resume.


## Mirrored history and index format

Memory's strict source projection uses the same original-position ancestry
authority in Search selection, FTS/vector extraction, and Anchor navigation.
Unlike public Replay compatibility, its scoped round identity includes the
ancestor path; late completion of an older mirror must not close a newer one.
The single all-branch derived-index release format is **2**, replacing the
legacy selected-lineage (format 1 / unversioned) materialization. Ancestry,
physical provenance and deduplication semantics are all part of that one
format contract; there is no compatibility promise for intermediate PR drafts.
This is not a SessionStore source-format migration.


## Replay event projection relationship

SessionIndexReconciler and strict Memory lineage are unchanged by this
consumer-wide Replay audit. Event-stream readers select source rows through
`project_live_event_stream()` (physical positions), not the legacy
`select_live_event_ids()` result. This does not give them authority to
publish indexes, infer Memory lineage, or rewrite events. Job settlement
remains separate evidence correlation: a result recorded on another branch
may still finalize a selected live call when provider/job identity proves
ownership. This split prevents both sibling leakage and late-result loss.


## Session-wide participant enrollment and contended abort

An explicit `SessionIndexReconciler.build(source)` enrolls the **whole captured
Agent roster** in the FTS and (when configured) Vector pending sets under
`MemoryIndexLifecycle`'s short mutation lock **before the first Agent write**,
including non-forced builds against an already READY corpus. The final
checkpoint alone can publish READY; an empty Agent is still a participant.

`refresh_keywords(source)` uses the same lifecycle enrollment for multi-Agent
FTS mutations. A pure batch preflight skips the redundant BUILDING transition
when every captured source is already current; an FTS-only request does not
claim a Vector build. Both operations abort any owned Session-wide builds
when a later Agent or progress callback fails.

An abort normally takes the short mutation lock to record an incremented
generation/revision and release long ownership. If another writer already
holds that short lock, termination still releases this instance's long build
locks: every native index mutation was preceded by a durable BUILDING
barrier, so a partially written corpus is not readable. A detached Vector
commit additionally checks that it still owns the long build lock whenever
the manifest is BUILDING; a lost owner cannot publish a stale batch before
the next builder's generation bump.

When a new owner sees an orphaned `vector=building` manifest, claiming its
long lock must **not** adopt the prior owner's unfinished native rows.
The existing `vector_needs_clear` flag forces a full table clear and
generation restart before the new participant batch can be published.
No new persisted fields, background retry manager or SessionStore source
format change are introduced.


### Embedding-space compatibility is a declaration

A shared SessionStore verifies **declared** embedding-space compatibility, not
the behavior of a remote model service. The supported identity projection
covers provider, normalized model name, API endpoint, requested dimensions,
and other vector-affecting options KT actually passes to the embedder. Device
selection, unknown options, the API key, and its environment-variable name
are not evidence of a different embedding space. A model name or deployment
alias can be silently remapped by a provider or behave differently between
accounts; KT does not probe weights, fingerprint remote responses, or
automatically repair these changes. Users/providers must change the declared
identity and explicitly re-embed when the space changes.

SessionStore's embedding_config retains the model declaration, but newly
written records omit inline API secrets. A non-secret api_key_env hint may
remain so saved-session CLI/Studio searches can use the **current** process
environment. Live searches prefer authentication from the current Creature
configuration or its open SessionStore's volatile claim. A legacy persisted
api_key is never used to authenticate a new client and is scrubbed on a
compatible Creature claim. Without current API credentials, explicit
semantic search reports an authentication error instead of secretly
reusing a historical key. A running API client does not automatically hot-swap
credentials: a newly constructed client uses the current source.

Neither credential rotation nor environment-variable renaming invalidates
the vector corpus. A different declared model, endpoint or dimensions
remains a compatibility conflict and requires an explicit rebuild. This
boundary does not add a Secret Manager, background drift detection, or a new
persistent index identity format.


## Bounded source-freshness responsibility

**Supported contract:** KT's SessionStore.append_event uses append-only
per-agent physical keys, including imported mirrors that reuse logical
event_id. SessionStore Fork creates a different Session ID. For this
contract, the memory index records a derived, compact source catalogue
(index epoch, FTS revision, physical event row count, Session ID, last
source fingerprint, and branch candidates) after a complete publication.

A stable query reads the Session ID, explicitly declared Agent roster and
KVault physical row count without calling full metadata discovery. The
catalogue is valid only for the **numeric physical event-key order**, not
KVault's lexical order (which differs after e999999). The full capture
verifies the Agent roster and successfully decoded physical key counts once.
Old catalogues without this ordering proof are rebuilt as *derived cache
metadata*, not a new v2 index format.

The fast path compares the physical frontier **before and after** projecting
the whole known Agent roster. Every newly inserted physical row must be
accounted for by an indexed Agent's consecutive physical append suffix; if
not, an unknown Agent, excluded framework event, or broken prefix may have
appeared, and full Agent Discovery + Reconciliation is required. This
allows newly registered but still-empty Agent names from the lightweight
metadata roster without scanning the event keyspace per unchanged query.
A normal append uses the existing incremental extraction cursor. The
Live Search, Saved Studio Search, CLI Search, SessionReader Search and
published-index status paths share this Reconciler proof rather than each
pre-discovering the Agent roster. Explicit full indexing and raw-history
or complete-Agent-census views retain full discovery when required.
A DIRTY vector corpus with unchanged model/generation and matching pre-append
Agent checkpoints can embed this suffix without reloading full history.
Missing or incomplete vector checkpoints, model changes and interrupted builds
still require full-source recovery. Repeated unchanged queries do not
reload the full event log or recompute its ancestry. For saved searches,
the read-only memory source avoids rebuilding unrelated event counters on
every open. The navigator enumerates physical event keys once per active
Store/Agent and extends its compact sequence after normal appends.
It observes the existing SessionStore accepted-append notifications: when
those local notifications account for all new physical rows, an append to
another Agent need not invalidate the current Agent's reference catalogue.
A gap caused by a failed Append, or growth not explained by the local Store
(e.g., a separate writer), invalidates that proof and forces physical
re-enumeration. The navigator unsubscribes when closed. This is a
process-local optimization, not a persisted source registry.

**Explicit boundary:** This is not an arbitrary database mutation detector.
Direct SQL / KVault edits, deleting or reusing old physical keys, replacing
historical event content in place, modifying the SQLite file outside KT,
or unannounced provider changes are not proven by a row-count frontier.
Such unsupported changes require explicit source reindex/reopen. Existing
fingerprint checks still protect individual Anchor source events. A missing,
unverifiable, incompatible or stale derived catalogue falls back to full
source reconciliation, never to unverified cached results. A legacy
unstamped ancestry tail can still require full reconstruction on change;
it is not the normal steady-state query path.

The optional catalogue lives in the existing derived memory_state table
under the **single v2** index format; it is not another writer, persistence
format, sidecar, watcher, distributed lease or new invalidation state
machine.

## Proof ownership and invalidation matrix

These are logical contracts, **not** additional database tables, leases or state
machines. The source event log is authoritative; FTS, Vector and the source
catalogue are derived.

| Authority and evidence | Proves | Invalidated/rechecked by | Does not prove |
| --- | --- | --- | --- |
| SessionStore: numeric physical keys, Session ID, total event count | Source identity and existing physical rows within KT-managed append/fork semantics | Append, Fork, unexplained key growth, sequence gap | Arbitrary SQL/in-place edits or remote model identity |
| Reconciler: Source Frontier and per-Agent Catalogue (count, next_seq, source_order, anchor) | All known Agent prefixes were captured; published index corresponds to one physical source prefix | Source growth not explained by known Agent suffixes, unreadable record, unknown Agent, changed Session ID | Vector readiness or an unchanged selected Branch View |
| History: candidate entries and parent paths | Which physical branch nodes are eligible under a selected view | Branch selection changes or new branch nodes | FTS publication, source freshness, model identity |
| Memory Lifecycle: Manifest, epoch, fts_revision, keyword cursors | Coordinated READY FTS publication for the complete enrolled Agent roster | Migration, source refresh, force rebuild, interrupted ownership, missing cursor | Vector publication and embedding-space validity |
| Vector: embedding identity, dimension, generation, keyword revision | Published vector corpus matches its declared model and FTS prefix | Model change, destructive clear, aborted build, incompatible cursor | Remote provider drift or branch visibility |
| Navigator: physical refs, fingerprint, accepted-append observations | Anchor source identity and locally verified append suffix | Gap, fingerprint mismatch, unobserved global growth, close/expiry | Distributed source registry or foreign writer lease |
| Replay/Compaction: visible events plus source-path evidence | Correct visible messages and reusable summary eligibility | Selection change, new compaction evidence | Source Prefix or Vector readiness |

**Separate lifetimes:** A valid Source Prefix does not make Vector READY.
A selection change does not invalidate FTS. IndexSourceSnapshot.published
currently means that the FTS Source Catalogue is unchanged, not that Vector
is ready. TailHistory is a verified suffix facade, not an arbitrary full list.

### Change impact

| Change | Required action | Reusable state |
| --- | --- | --- |
| Unchanged source / view | Check small identity/count and publication state | Existing event rows, candidate projection, FTS/Vector |
| Switch Branch View | Recalculate selection eligibility | Source proof, physical FTS/Vector data |
| Accepted Bob Append | Verify global delta and Bob suffix; refresh affected index | Alice physical refs if locally observed writes account for all growth |
| Failed Append creates key gap | Reject consecutive-suffix shortcut; enumerate physical keys | Previously committed rows |
| New undeclared Agent event | Unexplained delta triggers full Agent discovery | Existing physical source, subject to coordinated publication |
| Declared embedder identity change | Invalidate/rebuild Vector generation | Source, FTS, branch candidates |
| New Compaction completion | Re-evaluate eligible summaries and snapshot freshness | Original physical source facts |
| Fork changes Session ID | Invalidate source-derived certificates | Original independent session |
| External in-place mutation, unchanged key count | Unsupported automatic freshness; require explicit reindex/reopen | No proof of unchanged source |

## Opt-in Source Prefix performance baseline

From the repository root run:

    PYTHONDONTWRITEBYTECODE=1 python -B scripts/bench_memory_source.py --turns 48 144 320 --samples 5 --append-count 3

The script uses throwaway SessionStore files in the OS temp directory and
prints JSONL; it is **not a CI timing gate**. Each fixture contains one Agent,
one event per turn and a canonical linear, *cumulative* parent path. Timings
are warm in-process medians (except the single gap-recovery run). Source
enumeration counts include explicit full key scans and full decoded-source
reads, not the native KVault lookups reflected in timings.

Baseline: Windows / Python 3.14.2, HEAD 5d7b271eb.

| Turns | Catalogue KiB | Catalogue read p50 | Source proof p50 | Branch selection p50 | Steady prepare p50 | FTS query p50 | Append refresh p50 | Gap recovery single |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 48 | 10.7 | 0.12 ms | 0.76 ms | 0.17 ms | 1.04 ms | 1.02 ms | 20.32 ms | 17.56 ms |
| 144 | 93.4 | 1.22 ms | 5.61 ms | 1.75 ms | 7.71 ms | 0.95 ms | 51.40 ms | 51.96 ms |
| 320 | 476.5 | 5.96 ms | 29.65 ms | 5.53 ms | 36.66 ms | 1.04 ms | 177.18 ms | 201.11 ms |

All steady and regular Append samples: **zero** full event-key scans and
**zero** full source reads. The intentional failed-Append gap forced exactly
**one** full key scan and **one** full source read in each fixture.

On the 320-turn fixture, source proof verification costs about 30 ms
while separate FTS retrieval costs about 1 ms. This is a synthetic stress
case with cumulative ancestry, not production telemetry or an SLA.

**Next-slice acceptance criteria:**

- **Steady:** source proof verification must not deserialize or project
  irrelevant complete ancestry; materialization proportional to actual
  returned selection is separately justified.
- **Append:** normal refresh should track affected Agent/source changes
  rather than reserializing every past ancestor.
- **Recovery:** uncertain source coverage must still trigger complete
  recertification; this may cost O(source size) on the recovery path.

The baseline does not authorize a new format, Source Registry or separate
persistent proof state.

### Convergence: bounded proof reads and shared-prefix candidates

The v2 Memory index format and its READY/BUILDING publication authority have
**not** changed. Within the existing derived memory_state table, the per-Agent
source materialization is now two values, published together using KVault
put_many:

- memory_source_catalog is a small source certificate: Session ID / global
  physical frontier, Agent count, last-event fingerprint, numeric source
  ordering, Index epoch, FTS revision, Keyword revision and generation.
- memory_branch_catalog holds only the branch selection materialization.
  Candidate ancestry uses immutable interned parent edges with references to
  shared prefixes, not repeated full parent_branch_path lists. It is bound to
  the same Agent count and last-event fingerprint as the certificate.

Steady proof validation uses the **existing** Keyword revision, Cursor key
existence, pending flag, generation and global Manifest/FTS revision; it does
not deserialize the often much larger Keyword Round Cursor or Branch
Candidate materialization. Full Cursor values are still verified once on
publication. Keyword mutations always advance their existing revision,
including destructive resets. The branch value is loaded only for actual
branch selection or an appended suffix. A malformed/missing source binding
fails closed; a legacy same-version catalogue is fully recertified once
without changing the Memory index version. No independent writer, Registry,
state machine, or new persistent lifecycle epoch is created.

Run the **same** opt-in benchmark command above for the new HEAD, including
the additional branch_candidates_bytes and keyword_cursor_bytes fields.
Illustrative Windows / Python 3.14.2 measurements at the same sizes and
sample count (warm process medians, not a performance budget):

| Turns | Proof bytes | Shared branch bytes | Keyword cursor bytes | Proof p50 before -> after | Branch projection p50 before -> after | Steady prepare p50 before -> after | Append refresh p50 before -> after |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 48 | 255 | 1,784 | 20,764 | 0.76 -> 0.11 ms | 0.17 -> 0.06 ms | 1.04 -> 0.17 ms | 20.32 -> 14.23 ms |
| 144 | 258 | 5,364 | 174,087 | 5.61 -> 0.18 ms | 1.75 -> 0.16 ms | 7.71 -> 0.29 ms | 51.40 -> 22.74 ms |
| 320 | 258 | 12,404 | 881,255 | 29.65 -> 0.10 ms | 5.53 -> 0.42 ms | 36.66 -> 0.45 ms | 177.18 -> 81.92 ms |

The 320-turn FTS-only query still takes about 1 ms, unchanged in order of
magnitude. No normal steady/Append probe performed full source scans.
The deliberate sequence gap still triggers exactly one complete key scan
and one full source read (320-turn recovery: about 135 ms).

**Residual, deliberately not included in this slice:** the artificial
long-lived open Rounds make the Keyword extraction Cursor about 881 KB at 320
turns; ordinary Appends still have to read and update that cursor. This is a
distinct Round-ownership/state-encoding problem, not a reason to weaken
the source publication proof or build another durable source registry.
