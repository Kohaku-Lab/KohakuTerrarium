# terrarium/drive

The Drive runtime resource: durable, per-graph work records delivered to
creatures. Domain vocabulary sits at the top of the package; each family of
implementation files is a sub-package with the family prefix dropped.

```
drive/
├── __init__.py          stable public vocabulary (errors, models, requests, wire)
├── errors.py  models.py  requests.py  config.py  policy.py  acl.py
├── wire.py              primitive-dict (de)serialization of records and DTOs
├── goal.py  injection.py  prompt.py  sink.py  lifecycle.py  runtime.py
├── topology.py  split_intent.py  partition.py  migration.py  fencing.py  memory.py
├── manager/             DriveManager facade
│   ├── manager.py       DriveManager (mixes in the parts below)
│   ├── admit.py  ops.py  readiness.py  reconcile.py
├── repository/          storage contract and orchestration
│   ├── repository.py    contract, row types, pure builders
│   ├── base.py  ops.py  rows.py
├── store/               SQLite-backed durable repository
│   ├── store.py
│   ├── migration.py  migration_resources.py
├── service/             local Drive service surface
│   ├── service.py  protocol.py  reads.py  wire.py
├── multi_node/          multi-node routing
│   ├── homes.py  ops.py  remote_ops.py
├── tools/               tools exposed to creatures
│   ├── self_service.py  group.py  common.py
├── registration/        registration protocol and option resolution
│   ├── registration.py  options.py
├── delivery/            outbox dispatcher
│   ├── delivery.py  failure.py
└── snapshot/            enabled-registry and saved-session snapshots
    ├── snapshot.py  saved.py
```

Unit tests mirror the tree under `tests/unit/terrarium/drive/`.
