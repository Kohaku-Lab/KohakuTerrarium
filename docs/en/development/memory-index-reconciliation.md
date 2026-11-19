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
