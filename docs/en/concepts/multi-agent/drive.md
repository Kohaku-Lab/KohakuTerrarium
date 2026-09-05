---
title: Drive
summary: A durable, assignable runtime commitment the engine delivers as ordinary events. A Terrarium resource beside Session and Channel, opt-in, and never a reasoning loop.
tags:
  - concepts
  - multi-agent
  - drive
---

# Drive

## What it is

A **Drive** is a durable, addressable, assignable runtime commitment
that can produce ordinary events for a creature. "Keep investigating
this incident until it is resolved or blocked." "Watch this migration
and resume after a process restart." "Complete this research objective
across several turns." A Drive stores that commitment, decides *when*
it is ready to be worked, delivers a wake event to the creature that
owns it, survives a restart, and reconciles itself when the graph
changes underneath it.

Drive is an **optional Terrarium-managed runtime resource**, in the
same family as a [Session](../modules/session-and-environment.md) or a
[Channel](../modules/channel.md). The engine owns the facility; each
graph owns its Drive records the same way it owns its session store.
A creature runs perfectly well with zero Drives, and a Terrarium
constructed without a Drive config has no Drive machinery at all.

A Drive is deliberately **not**:

- a seventh creature component (Controller / Input / Trigger / Tool /
  Output / sub-agent / plugin stay the whole list);
- a creature-owned or session-owned goal loop;
- an LLM, planner, evaluator, or motivational faculty;
- a replacement for triggers, tools, plugins, sessions, or channels;
- a promise that an external side effect runs *exactly once*.

## Why it exists

Applications already approximate durable pursuit with plugin state,
scratchpads, timers, commands, and hand-rolled orchestration. Those
pieces prove the idea composes, but each app reinvents a subtly
different — and usually incomplete — outer lifecycle: durable identity,
assignment, resume reconciliation, versioned mutation, retry, and
administrative inspection. That missing piece is **runtime
coordination, not reasoning**, and it has the same shape as the
resources Terrarium already owns:

| Resource | Terrarium mechanically owns | Creature / application supplies meaning |
|---|---|---|
| Session | durable history, attachment, merge/split lineage | what remembered content means |
| Channel | identity, wiring, broadcast delivery | what messages mean and whether to act |
| **Drive** | identity, assignment, readiness, durable delivery, recovery | what the commitment means and how to pursue it |

A Drive also needs lifecycle knowledge a creature-local module does not
have: creature start / stop / removal, graph membership, graph merge /
split, session attachment, remote ownership, engine shutdown. Terrarium
already owns those facts and can coordinate them *without ever calling
an LLM*.

## The ownership boundary

This is the load-bearing rule and the reason Drive lives in the engine:

- **Terrarium owns the mechanics.** Globally stable `drive_id` and a
  monotonic `revision`; scope and assignment; deterministic lifecycle
  transition validation; readiness/dependency calculation; durable
  persistence and a transactional outbox; physical delivery, retry,
  acknowledgement, and dead-letter state; stale-revision and
  stale-epoch suppression; reconciliation on start / stop / removal /
  reassignment / resume / topology change; actor identity, capability
  checks, and audit; local / remote / multi-node parity.
- **The creature owns the meaning.** Interpreting the Drive's `kind`,
  `title`, and `spec`; planning and tool selection; executing side
  effects; assessing progress and gathering evidence; deciding when to
  *propose* waiting, blocking, completion, or failure; recovery
  reasoning after an interrupted attempt.

### The non-intellectual runtime rule

Terrarium may answer **deterministic** questions: does the Drive exist
and is this revision current? Is its status deliverable? Is its
assignee present and running? Have its dependencies reached configured
states? Is `not_before` in the past? Is the actor authorized? Did a
registered validator accept the proposal? Is this delivery stale,
duplicated, over budget, or awaiting backoff?

Terrarium must **never** answer semantic questions: is the objective
actually achieved? Is this plan good? What should the creature do next?
Is progress meaningful? A creature, a human, an external service, or a
deterministic registered verifier may propose those conclusions;
Terrarium only applies valid state transitions. `COMPLETED` means an
authorized proposal passed configured policy — not that the engine
reasoned about the world.

## Lifecycle states

A Drive has one **runtime control status** at a time (these are engine
control states, not the engine's opinion about the objective):

| Status | Deliverable? | Meaning at runtime |
|---|---|---|
| `draft` | no | Exists but not admitted for pursuit. |
| `active` | yes, when ready | Eligible for delivery. |
| `waiting` | not until a deterministic wake condition | Awaiting time / dependency / external signal. |
| `blocked` | no (by default) | Needs actor intervention or a policy-defined unblock. |
| `paused` | no | Explicitly suspended without declaring failure. |
| `completed` | no | Accepted completion proposal; terminal. |
| `failed` | no | Accepted unrecoverable failure; terminal. |
| `cancelled` | no | Explicitly abandoned; terminal. |
| `retired` | no | Historical tombstone / retention terminal. |

The generic transition graph:

```text
 draft ------> active <------ paused
   |             |  ^            ^
   |             |  |            |
   +-> cancelled |  +-- waiting -+
                 |       |
                 +-----> blocked
                 |
                 +-----> completed
                 +-----> failed
                 +-----> cancelled

 completed / failed / cancelled ---> retired
```

Anything beyond this generic graph requires an enabled registration's
policy. **Reopening a terminal Drive is forbidden by default**; the
intended pattern is to create a successor Drive carrying
`metadata.parent_drive_id`. If a registration explicitly allows reopen,
the repository increments the Drive's `lifecycle_epoch` (which
invalidates every prior delivery) and writes an audit record. Waiting
Drives carry deterministic wake conditions only — a timestamp, a
dependency predicate, a named external signal, a registration readiness
function, or a manual wake by an authorized actor. The manager never
infers readiness from free-form prose.

## Delivery: at-least-once, logically deduplicated

A Drive becomes work by turning into an ordinary `TriggerEvent`
(`drive_ready` / `drive_resume` / `drive_recovery`) delivered through
the public creature ingress — the same admission, serialization,
plugin, controller, tool, and output path any trigger uses. The
dispatcher does not call any private agent method and does not start a
second reasoning loop; the creature stays the single-turn serializer.

The delivery guarantee, stated honestly:

> Physical Drive event delivery is **at least once**. Processing is
> logically deduplicated by delivery ID, Drive revision, lifecycle
> epoch, assignment ID, and readiness generation.

**There is no exactly-once guarantee, and the framework never claims
one.** Exactly-once side effects are impossible to promise across a
model turn, a tool call, and an external system that can each fail
independently. The engine separates *physical dispatch* from *logical
acknowledgement*: a delivery becomes `admitted` when the creature
accepts the event, and `acknowledged` when that turn settles.
`acknowledged` means "the turn settled" — **not** "the Drive is
complete" and **not** "the external side effect happened exactly once."
Before admission, the dispatcher rejects or supersedes any delivery
whose Drive is gone or terminal, whose revision or epoch is stale,
whose assignment changed, or that was already admitted.

Tools that perform side effects should carry their own idempotency
keys; the Drive delivery context exposes `delivery_id` precisely so a
side-effecting tool has a stable key to deduplicate against.

A user interrupt is a deterministic engine fact, so the engine acts on
it: the interrupted delivery is acknowledged with reason
`user_interrupted`, the Drive moves to `paused` with the same status
reason, and no readiness scan re-arms it. Pursuit resumes only through
an explicit resume or wake by an authorized actor.

### Recovery is honest about uncertainty

If a creature stops (or the process crashes) between admission and
acknowledgement, the prior attempt is **uncertain**: it may or may not
have run its side effects. After the creature restarts and passes the
[restoration barrier](#the-restoration-barrier), the manager
reintroduces the still-current Drive through a `drive_resume` or
`drive_recovery` event that says, in the projection the creature sees:

> A previous attempt may have executed side effects. Inspect the
> current state and reconcile before repeating actions. Use the
> delivery ID as an idempotency key where supported.

A recovery event never instructs a blind replay, and no UI ever renders
a recovery or blocked state as ordinary success.

## The restoration barrier

A Drive must never be delivered against a half-restored runtime. Some
construction paths start a creature before its session store is
attached; Drive requires an explicit ordering:

```text
construct creature
-> restore conversation / scratchpad / plugin / session state
-> attach the graph SessionStore and Drive repository
-> replay runtime topology
-> start the creature
-> complete the startup trigger
-> mark the creature restoration-ready
-> reconcile Drives
```

No Drive is delivered before that barrier. This is what prevents a
Drive from pursuing an objective against an empty conversation or a
partially restored graph. On a cold start the order is always:
restoration first, then the startup trigger, then Drive reconciliation.

## Registrations: installed is not enabled

New Drive **instances** are created dynamically at runtime. New
executable Drive **kinds** are not — a Drive's `kind` is served by a
**Drive registration**, a deterministic runtime extension supplying
that kind's schema validation, readiness rules, event projection,
optional completion verifier, and a bounded prompt contribution. A
registration runs no LLM, writes no repository, and dispatches no
events; it only answers the deterministic questions the core asks. The
framework ships a built-in `generic` registration (opaque spec, manual
terminal proposals); other kinds — such as `goal` — arrive as installed
packages.

Two separate ideas that are easy to conflate:

- **Discovery** — a package declaring a `drive_registrations:` manifest
  slot makes a registration *available*. The Studio catalog can list it
  without importing its code.
- **Enablement** — a registration only becomes usable when it is
  explicitly enabled (in Drive settings, or by passing the instance to
  `Terrarium(drive_registrations=[...])`). **Installed is never
  automatically enabled.** Only an enabled registration can create,
  validate, project, schedule, or contribute prompt text for its kind.

Duplicate registration `name`s and conflicting `kind` ownership are hard
validation errors surfaced before anything is applied.

### When a registration is disabled or unavailable

Persisted Drive records are **never deleted or rewritten** just because
their registration is turned off. Availability is a *derived* runtime
condition (`DriveAvailability`), not a new status and not a reason to
consume a revision:

- records stay listable and can still be paused / cancelled / retired
  administratively;
- the derived condition is `registration_disabled`,
  `registration_unavailable`, or `registration_incompatible`, and **no
  delivery is admitted** while it holds;
- any operation that needs the registration's semantics — a spec edit,
  readiness evaluation, projection, terminal verification — **fails
  closed**;
- re-enabling a compatible registration clears the condition and
  reconciles the still-active records; an incompatible schema version
  requires an explicit migration first;
- generic read / status views and saved-session viewers keep working
  throughout.

## Persistence

A Drive's durability follows how its graph is set up:

| Engine / graph setup | Drive behavior |
|---|---|
| Session store / autosession attached | **Durable**; resumes after a process restart. |
| No session and no separate Drive store | **In-memory** only; survives a creature stop, not an engine shutdown. |
| Explicit `drive_store=` | **Durable** independently of the conversation session (for service / daemon apps). |

When a session is attached, the Drive repository lives in a dedicated
**sidecar file paired with the session** — `<name>.kohakutr.drives`
alongside `<name>.kohakutr` — so Drive writes and conversation writes
never contend on one database. Copying a session with persistent Drives
means copying the sidecar too. The mechanics are in
[the programmatic guide](../../guides/programmatic-drive.md#persistence-and-the-sidecar-file).

## How it relates to `/goal`

The `/goal` feature is **one optional composition** on top of the
generic Drive facility, not the definition of Drive. It is two
independent toggles: a `goal` Drive *registration* (the deterministic
kind semantics) and a `GoalPlugin` (the `/goal` slash command and its
prompt guidance). Either can be enabled without the other. See
[Goal as a composition over Drive](../../guides/goal.md).

## What you can therefore build

- **Durable incident pursuit.** A creature keeps a `blocked`/`active`
  Drive across restarts; recovery events tell it to re-inspect before
  acting.
- **Scheduled/awaited work.** A `waiting` Drive re-arms on a timestamp
  or a dependency Drive reaching a terminal state.
- **Operator-visible commitments.** Because a Drive is a first-class
  runtime resource, its status, owner, assignee, and recovery/blocked
  warnings are inspectable everywhere Terrarium is operated —
  independently of whether `/goal` is installed.

## Don't be bounded

A creature is fully valid with no Drives, and most creatures never need
one. Reach for a Drive only when the commitment is genuinely more
durable than a single turn *and* needs the engine's coordination
(identity, assignment, resume, recovery). A one-shot task is a turn; a
periodic check is a trigger; a durable, assignable, recoverable
objective is a Drive.

## See also

- [Session & environment](../modules/session-and-environment.md): the
  per-graph state a durable Drive persists beside.
- [Channel](../modules/channel.md): the other broadcast-delivery
  runtime resource.
- [Programmatic Drive](../../guides/programmatic-drive.md): driving the
  Drive runtime directly from Python.
- [Goal](../../guides/goal.md): `/goal` as an optional composition over
  Drive.
- [Configuration reference](../../reference/configuration.md#drive-settings-drive-settingsyaml):
  the `drive-settings.yaml` schema and the `drive_registrations:`
  manifest slot.
