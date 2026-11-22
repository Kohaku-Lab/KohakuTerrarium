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
Store/Agent and extends its compact sequence after normal appends. If a
failed Append consumes a sequence without publishing a row, the resulting
physical gap invalidates the incremental extension; navigation re-enumerates
that Agent's existing keys instead of treating the incomplete catalogue
as current.

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
