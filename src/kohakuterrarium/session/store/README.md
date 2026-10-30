# session/store

`SessionStore`: one `.kohakutr` file holding metadata, per-agent state, the
append-only event log, channel history, sub-agent runs, jobs, turn rollups and
the full-text index.

`from kohakuterrarium.session.store import SessionStore` is the only import
path callers need. The class is assembled in `assembly.py` from the parts
below; `__init__.py` is a lazy facade so the leaf modules can be imported by
modules that `assembly` itself depends on.

## Layout

```
store/
├── __init__.py          lazy facade: SessionStore
├── assembly.py          class SessionStore(<mixins>) + __init__ ordering
├── meta.py              StoreMetaMixin          meta table: identity, status
├── derived.py           StoreDerivedMixin       search, token usage, fork entry points
├── fork.py              perform_fork            fork implementation
├── protocol.py          SessionStoreLike        structural type for helpers
├── identity.py          stable identities for persisted conversations
├── token_views.py       token usage for agents and session trees
├── lifecycle/           file handle, ownership, close
│   ├── handle.py        StoreHandleMixin        path, tables, artifacts, flush, close
│   ├── affinity.py      StoreAffinityMixin      one worker thread per store
│   ├── resources.py     StoreResourceMixin      occupancy + writer-lock enrollment
│   ├── lock.py          writer lock, table disposal helpers
│   ├── open.py          open_owned_store        cancellation-safe off-loop open
│   ├── tables.py        open_store_tables       native table open order
│   ├── table_policy.py  per-table cache and reader-pool sizing
│   └── version.py       format detection and fail-closed writable admission
├── events/              the event log
│   ├── append.py        EventAppendMixin        ids, watermark, buffering, subscribers
│   ├── read.py          EventReadMixin          reads, resume normalization, discovery
│   ├── branch_index.py  BranchIndexMixin        rebuildable per-store branch maxima
│   ├── key_index.py     rebuildable event-id locators
│   ├── counters.py      counter restore + persistence helpers
│   ├── flush.py         EventFlushPipeline      batched commit of events + FTS rows
│   ├── values.py        value iteration over key ranges
│   └── discovery.py     agent names from event keys
└── records/             keyed record tables
    ├── conversation.py  ConversationRecordsMixin  snapshots, state, rollups
    ├── channels.py      ChannelRecordsMixin
    ├── subagents.py     SubagentRecordsMixin
    ├── jobs.py          JobRecordsMixin
    ├── artifacts.py     session-local binary artifacts
    ├── rollup.py        denormalized per-turn summaries
    └── snapshot.py      independent conversation checkpoints per branch lineage
```

## Pseudo-class rules

A class too large for one file is a package. Its public name is defined in
the assembly module, which lists the parts as bases and contains nothing else
beyond assembly (constructor ordering, cross-part glue).

1. **One part, one concern.** A part is a mixin named `<Concern>Mixin` in a
   file named for the concern. Helpers that do not touch `self` are plain
   functions in their own module.
2. **A part owns its state.** Attributes are created in the part's own
   `_init_<concern>()`; `__init__` calls the hooks in dependency order.
3. **Cross-part attributes are declared, not assumed.** A part that reads an
   attribute owned by another part lists it as a class-level annotation under a
   comment naming the owner. The annotations are the part's contract.
4. **Parts never import the assembled class at run time.** Type-only
   references go under `TYPE_CHECKING`.
5. **A part may itself be a package** when it outgrows a file; its
   `__init__.py` assembles the sub-parts the same way.
6. **The package `__init__` stays light** when leaves are imported by modules
   the assembly depends on: a lazy facade, with the class built in `assembly.py`.
7. **Tests mirror the tree**: `tests/unit/session/store/<same path>/test_<file>.py`.
   Monkeypatch the module that *uses* a name (`events.append` for
   `scan_max_event_id`), not the assembled package.

## `__init__` order

`_init_affinity` → `_init_handle` (path, enrolment) → `_init_events` →
`_init_channels` → `_init_subagents` → open tables → restore counters.
Enrolment precedes any native open; a failed open discards partial handles.
