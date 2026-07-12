---
title: Programmatic Drive
summary: Drive the durable-commitment runtime from Python — explicit engine config, the self-service tools, propose/verify completion, the sidecar persistence file, and resume, with no Studio dependency.
tags:
  - guides
  - drive
  - programmatic
---

# Programmatic Drive

For readers driving the [Drive runtime](../concepts/multi-agent/drive.md)
directly from Python. This is the low-level, no-Studio path: you build
a `Terrarium` with explicit Drive arguments, and you create and
administer Drives through the engine's `TerrariumService`. Nothing here
reads `~/.kohakuterrarium` or asks Studio for anything — the managed
surfaces ([`kt`](../reference/cli.md), web, TUI) sit *above* this and
resolve the same explicit arguments from
[Drive settings](../reference/configuration.md#drive-settings-drive-settingsyaml).

If you only want the concept, read
[concepts / Drive](../concepts/multi-agent/drive.md) first. If you want
`/goal`, that is a composition on top of everything here — see
[Goal](goal.md).

## Enabling the runtime

A Terrarium has no Drive machinery unless you pass a `drive_config`.
Enablement is explicit dependency injection: the config plus a concrete,
non-empty list of registrations.

```python
import asyncio

from kohakuterrarium import Terrarium
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)


async def main():
    async with Terrarium(
        session_dir="runs/",                       # autosession: Drives persist
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),  # == [GenericDriveRegistration()]
    ) as engine:
        assert engine.drives is not None           # the runtime is live
        ...

asyncio.run(main())
```

Rules the constructor enforces:

- **`drive_config=None` (the default) means no Drive runtime.** The
  engine builds no manager, tools, prompt, or dispatcher, and
  `engine.drives` is `None`.
- **`enabled=True` with no registrations fails validation.** The
  low-level engine never scans packages or invents an enable set. You
  pass `default_registrations()` (the generic kind) explicitly, or your
  own instances.
- Registrations are collision-checked (duplicate `name` or conflicting
  `kind` is a hard error) before any creature starts.
- Every convenience constructor forwards the same three arguments:

```python
engine = await Terrarium.from_recipe(
    "team.yaml",
    drive_config=drive_config,
    drive_registrations=registrations,
)
engine = await Terrarium.resume(
    "run.kohakutr",
    drive_config=drive_config,
    drive_registrations=registrations,
)
```

The recipe object is unchanged — recipes never carry Drive fields (see
[recipes stay graph-only](terrariums.md#recipes-stay-graph-only)).
Applying the same recipe to two engines can yield different Drive
capabilities because the engine arguments differ, not the recipe.

`DriveRuntimeConfig` is where the scheduler / retry / retention / payload
limits live; every field and its default is in the
[configuration reference](../reference/configuration.md#runtime-fields).

## Creating and administering Drives

Records are created and administered through a `TerrariumService`. For
an embedded caller that is `LocalTerrariumService(engine)`. The service
is the same surface Studio uses, so your code and the managed UIs share
one behavior and one set of typed errors.

Every mutation carries an **actor** you supply from your own trusted
context; the manager re-checks authorization on every call, so holding a
service object is never itself permission.

```python
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest

worker = await engine.add_creature("@kt-biome/creatures/swe", start=True)
service = LocalTerrariumService(engine)

operator = ActorRef("service", "deploy-bot")   # "<kind>:<identity>"

view = await service.create_drive(
    CreateDriveRequest(
        kind="generic",
        title="Watch the deployment",
        scope_type="graph",                     # or "creature"
        scope_id=worker.graph_id,
        owner=operator,
        owner_scope="service",
        created_by=operator,
        spec={"instruction": "Monitor until the rollout is stable"},
        assignee_creature_id=worker.creature_id,
    ),
    graph_id=worker.graph_id,
    actor=operator,
    operator=True,          # audited graph-authority elevation (see below)
)
drive_id = view.record.drive_id
```

`create_drive` (and every mutation) returns a **`DriveView`**: the
`record`, its `assignee_creature_id` / `assignment_state`, the derived
`availability` and `durability`, and an actor-scoped `allowed_actions`
tuple so a UI can render permission-aware controls without guessing.

### Actors, ownership, and the operator flag

- A **creature-scoped** Drive owned by the caller needs no elevation —
  that is the baseline capability of any creature (and the self-service
  tools below).
- Creating a **graph-scoped** Drive, assigning to another creature, or
  transferring ownership is a graph-authority operation. A plain
  `user:` actor does not hold it; a trusted embedded caller passes
  `operator=True`, which the manager treats as an explicit, audited
  elevation — never as creature privilege.
- Assignment is **not** ownership. A creature assigned a Drive owned by
  someone else may read it, report progress, and *propose* transitions,
  but may not rewrite, cancel, reassign, or retire it.

### Reads, updates, transitions

```python
from kohakuterrarium.terrarium.drive.requests import DrivePatch

# read
view = await service.get_drive(drive_id, actor=operator)
views = await service.list_drives(
    actor=operator,
    statuses=frozenset({DriveStatus.ACTIVE, DriveStatus.WAITING}),
)

# CAS update — expected_revision must match or DriveConflictError is raised
view = await service.update_drive(
    drive_id,
    DrivePatch(priority=5),
    expected_revision=view.record.revision,
    actor=operator,
)

# non-terminal control transitions
await service.transition_drive(
    drive_id, DriveStatus.PAUSED,
    expected_revision=view.record.revision, actor=operator,
)
await service.wake_drive(drive_id, actor=operator)     # re-arm a waiting Drive

# append-only progress (no revision bump)
await service.report_drive_progress(
    drive_id, summary="rollout at 40%", evidence={"pct": 40}, actor=operator,
)
```

Every canonical mutation takes `expected_revision` (optimistic
concurrency) and an optional `idempotency_key`. A stale revision raises
`DriveConflictError`; reusing an idempotency key with a different
payload raises `DriveIdempotencyConflictError`. `report_drive_progress`
is the append-only exception and takes no `expected_revision`.

## Propose / verify completion

A creature (or an operator) does **not** write a terminal status
directly. Completion and failure go through a **proposal** so
registration validators and any required verifier run first:

```python
result = await service.propose_drive_transition(
    drive_id, DriveStatus.COMPLETED,
    evidence={"tests": "green", "run": "ci#4821"},
    expected_revision=view.record.revision,
    actor=operator,
)

if isinstance(result, dict) and result.get("pending"):
    # a required verifier / two-party approval is pending
    final = await service.approve_drive_proposal(
        result["proposal_id"], actor=operator, operator=True,
    )
else:
    final = result          # a DriveView: the proposal was accepted outright
```

The verifier mode is the registration's decision. The `generic` kind
accepts an authorized proposal directly. Other kinds can require a named
verifier, a specific approver actor class, or a distinct two-party
approver — and a missing required verifier **fails closed**, never open.
Terrarium never judges whether the objective was truly met; it only
applies a transition an authorized, verified proposal earned.

## The self-service tools (creature-facing)

A Drive-enabled Terrarium injects five generic Drive tools into **every**
creature it hosts, plus one privileged tool. These are what the LLM
calls; they resolve the actor from the tool context and enforce
owner / scope / ACL and registration availability on every call, so they
are safe on non-privileged creatures (tool presence is not
authorization).

| Tool | Scope | What it does |
|---|---|---|
| `drive_create` | every creature | Create a Drive **you own**, scoped and assigned to you. |
| `drive_status` | every creature | List the Drives you own / are assigned, or get one by `drive_id`. |
| `drive_update` | every creature | CAS-update a Drive you own. |
| `drive_report` | every creature | Append progress / evidence to an owned or assigned Drive. |
| `drive_transition` | every creature | Manage an owned Drive, or propose an allowed transition on a foreign-owned assigned Drive. |
| `group_drive` | privileged node only | Create graph-owned Drives; assign / reassign / unassign, transfer ownership, wake, retire, repair, or replay a dead letter within the graph. |

`group_drive` is the [privileged-node](../concepts/multi-agent/privileged-node.md)
graph-administration surface — a worker that a privileged node spawns
does not receive it. When the runtime is enabled, the engine also injects
one bounded prompt fragment (the generic Drive contract plus the prose
of each *enabled* registration, ordered by name); it never dumps current
Drive records into the prompt — those arrive as events and through
`drive_status`.

## Delivery you can rely on (and can't)

A Drive becomes work as an ordinary `TriggerEvent`
(`drive_ready` / `drive_resume` / `drive_recovery`) delivered through
the public creature ingress and settled like any turn. The guarantee is
**at least once**, deduplicated logically by delivery ID, revision,
lifecycle epoch, assignment ID, and readiness generation.

There is **no exactly-once guarantee.** If a side-effecting tool must
not double-apply, give it its own idempotency key — the delivery context
exposes `delivery_id` for exactly that. After an interrupted attempt the
creature receives a recovery event that says a previous attempt *may*
have run its side effects and to reconcile before repeating them; it is
never told to blindly replay. See
[delivery](../concepts/multi-agent/drive.md#delivery-at-least-once-logically-deduplicated).

## Persistence and the sidecar file

Durability follows the graph setup, and the creation result reports it
as `view.durability` (`"persistent"` or `"ephemeral"`):

| Setup | Durability |
|---|---|
| `Terrarium(session_dir=...)` / a session store attached | **persistent** — resumes after a process restart |
| No session and no `drive_store` | **ephemeral** — survives a creature stop, not an engine shutdown |
| `Terrarium(drive_store=...)` | **persistent**, independent of the conversation session |

When a session is attached, the Drive repository is **not** written into
the `.kohakutr` file. It lives in a dedicated **sidecar** paired with the
session:

```text
runs/run.kohakutr          <- conversation, events, scratchpad (KohakuVault)
runs/run.kohakutr.drives   <- the Drive repository (its own SQLite database)
```

Two consequences to plan for:

- **A persistent Drive travels with its sidecar.** If you copy or move a
  session and want its Drives, copy `<name>.kohakutr.drives` alongside
  `<name>.kohakutr`. The bare `.kohakutr` carries no Drive state.
- **Forking a session does not fork its Drives.** A fork copies only the
  `.kohakutr`, so it is born with zero Drives by construction — this
  avoids two branches mutating the same commitment. Merge and split
  carry Drives explicitly through row-copy hooks.

Requesting persistence when no persistent backend can be resolved fails
at engine construction (`DrivePersistenceRequiredError`), not after a
Drive is already active.

## Resume and reconcile

`Terrarium.resume(...)` takes the **same** explicit Drive arguments as
the constructor, opens the persisted Drive state (from the sidecar), and
reconciles it. It never reapplies "recipe Drive seeds" because no such
thing exists — a recipe never creates a Drive.

```python
engine = await Terrarium.resume(
    "runs/run.kohakutr",
    drive_config=DriveRuntimeConfig(enabled=True),
    drive_registrations=default_registrations(),
)
```

On resume, no Drive is delivered until the creature passes the
[restoration barrier](../concepts/multi-agent/drive.md#the-restoration-barrier):
conversation / scratchpad / plugin / session state restored, the Drive
repository attached, topology replayed, the creature started, and its
startup trigger settled. Only then does the manager reconcile
assignments and reintroduce still-current Drives — as `drive_resume` for
a clean stop, or `drive_recovery` (with the "may have executed side
effects" warning) for an attempt that was interrupted mid-flight.

If a resumed Drive's registration is not enabled on this engine, the
record is **not** deleted or downgraded: it becomes non-deliverable with
a derived availability of `registration_disabled` /
`registration_unavailable` / `registration_incompatible`, stays
inspectable, and reconciles the moment a compatible registration is
enabled. See
[when a registration is disabled](../concepts/multi-agent/drive.md#when-a-registration-is-disabled-or-unavailable).

## What Terrarium will not do

- **Reason about a Drive.** The engine answers deterministic questions
  (is the revision current? is the assignee running? is `not_before`
  past?). It never decides whether the objective is met, whether a plan
  is good, or what the creature should do next.
- **Complete a Drive on its own.** Budgets and errors can pause or block
  a Drive; nothing silently marks it complete. `completed` requires an
  authorized, verified proposal.
- **Roll back side effects.** Cancelling a Drive prevents future
  delivery; it does not undo effects a prior turn already executed.

## See also

- [Drive concept](../concepts/multi-agent/drive.md): the model behind
  this API.
- [Goal](goal.md): `/goal` as an optional composition over Drive.
- [Configuration reference](../reference/configuration.md#drive-settings-drive-settingsyaml):
  `DriveRuntimeConfig` fields, `drive-settings.yaml`, and the
  `drive_registrations:` manifest slot.
- [Terrariums](terrariums.md): the engine that hosts the Drive runtime.
- The built-in Goal composition (`kohakuterrarium.terrarium.drive.goal` +
  `kohakuterrarium.builtins.plugins.goal`): a full Goal registration +
  plugin built only on this public surface.
