---
title: Goal
summary: /goal as an optional composition over the Drive runtime — two independent toggles (a goal registration and GoalPlugin), the command set, ownership, user-confirm completion, budgets that pause not complete, and honest recovery.
tags:
  - guides
  - goal
  - drive
---

# Goal

`/goal` is **not** a framework feature. It is one optional composition
built entirely on top of the generic [Drive](../concepts/multi-agent/drive.md)
runtime, shipped as a **built-in** plugin + registration:
`GoalPlugin` (`kohakuterrarium.builtins.plugins.goal`) and
`GoalDriveRegistration` (`kohakuterrarium.terrarium.drive.goal`). Both
ship in-tree but **disabled by default** — you enable them per agent,
exactly like the other built-in plugins (sandbox / budget / permgate /
compact). Drive does the durable work; Goal adds a human-friendly slash
command and a kind called `goal`. Because it is only a composition,
everything `/goal` does is also reachable through the generic Drive tools
and APIs in the [programmatic guide](programmatic-drive.md).

## Two independent toggles

The single most important thing to understand: Goal is **two separate
switches**, and neither implies the other.

| Toggle | What it is | How you enable it |
|---|---|---|
| The `goal` **registration** | The deterministic `kind="goal"` policy: schema, autonomy-aware readiness, projection, terminal verifier. | Enable the built-in `goal` registration in [Drive settings](../reference/configuration.md#drive-settings-drive-settingsyaml), or pass `GoalDriveRegistration()` to `Terrarium(drive_registrations=[...])`. |
| `GoalPlugin` | The optional built-in plugin that contributes the `/goal` command and a short Goal-semantics prompt fragment. | Enable it in the plugin panel (`/plugin`, web/TUI Plugins tab), list it in a creature's `plugins:` config, or `agent.add_plugin(GoalPlugin())`. |

- **Enabling `GoalPlugin` does not enable the `goal` registration**, and
  vice versa. They are two independent built-in toggles — one lives in
  the plugin panel, the other in Drive settings.
- **Registration disabled** → `drive_create(kind="goal")` and `/goal
  set` fail closed with a clear message; any Goal records already in the
  store stay inspectable.
- **Plugin disabled** → `/goal` disappears from every command inventory;
  Goal Drives created earlier keep running under the (still-enabled)
  registration, manageable through the generic Drive surfaces.

This separation is deliberate: the runtime-executable policy (the
registration) is an operator/Settings decision, while `/goal` UX is a
per-creature plugin decision. One is *what kinds the node may run*; the
other is *whether this creature offers the slash command*.

## Enabling

Both toggles ship in-tree; there is **no install step**. Enable the
registration for the runtime through the managed Settings surface (which
writes `drive-settings.yaml`):

```yaml
# ~/.kohakuterrarium/drive-settings.yaml
runtime:
  enabled: true
registrations:
  goal:
    enabled: true
```

Then opt a creature into `/goal` by enabling the built-in plugin — in the
plugin panel (`/plugin`, or the web/TUI Plugins tab), or by listing it in
the creature's config:

```yaml
# a creature config.yaml
plugins:
  - name: goal          # resolves to the built-in GoalPlugin
```

Or do both explicitly in Python, with no Settings file at all:

```python
from kohakuterrarium import Terrarium
from kohakuterrarium.builtins.plugins.goal import GoalPlugin
from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
from kohakuterrarium.terrarium.drive.goal import GoalDriveRegistration

async with Terrarium(
    drive_config=DriveRuntimeConfig(enabled=True),
    drive_registrations=[GoalDriveRegistration()],   # the registration toggle
) as engine:
    creature = await engine.add_creature(
        "@kt-biome/creatures/general",
        plugins=[GoalPlugin()],                       # the plugin toggle
        start=True,
    )
```

## The `/goal` command set

```text
/goal set [autonomy=continue_when_ready] [policy=user_confirm] [criteria=a;b] <objective>
/goal show [id]
/goal list
/goal pause  [id]
/goal resume [id]
/goal cancel [id]
/goal complete [id]         # user-authoritative completion
/goal assign <id> <creature>
```

`/goal` always acts as the **authenticated user** actor — never as the
plugin's or the creature's identity — and resolves the
`TerrariumService` plus the focused creature from trusted command
context. It never accepts an actor string from the command text, and it
stores no state of its own: every `show` / `list` reads live Drive
state. `/goal set` with no explicit id operates on the creature's single
most recent active Goal; give an id to disambiguate.

`/goal set` defaults to `autonomy=manual`: the creature works one turn
now, and `/goal resume` wakes it for the next one. Pass
`autonomy=continue_when_ready` for a goal that keeps going on its own.
`/goal list` shows every live goal in the focused creature's graph with
its assignee, so a goal assigned to another creature is still visible.
`/goal resume` reactivates a paused, waiting, or blocked goal and then
wakes it; on an active manual goal it is simply the wake. Setting a new
goal while others are still live reports those goals; the per-creature
active-goal limit is enforced by the runtime, and the command tells you
to pause or cancel one when it is reached.

The command is only UI. It calls the same `TerrariumService` methods
that Python, HTTP, the web panel, and the generic Drive tools call, so a
Goal created through `/goal` is identical to one created any other way.

## GoalSpec

`goal` is a Drive kind, not a redefinition of Drive. Its `spec` is:

```python
{
    "objective": str,                       # required
    "success_criteria": list[str],
    "constraints": list[str],
    "completion_policy": "self_propose" | "user_confirm" | "verifier",
    "autonomy": "manual" | "continue_when_ready",
    "budgets": {
        "max_turns": int | None,
        "max_tool_calls": int | None,
        "max_walltime_s": int | None,
    },
}
```

Only `objective` is required; everything else defaults conservatively
(`manual` autonomy, `self_propose` completion, no budgets). The Drive
core never parses the objective or judges the criteria — that is the
creature's job. The projection the creature receives each turn tells it
that this is a *continuing commitment, not a request to invent a new
objective*, to report material progress with evidence, and to *propose*
completion with evidence rather than assert it.

### Autonomy drives continuation (there is no GoalRunner)

- `manual` — the Goal is worked once per wake and then waits; an
  authorized actor must wake it again (`/goal resume`, or a dependency
  becoming ready).
- `continue_when_ready` — after each settled turn the registration's
  readiness re-arms, so the generic Drive dispatcher emits the next
  ordinary Drive event. Continuation is the dispatcher reacting to
  readiness, **not** a special agent loop.

## Ownership

Who owns a Goal (and who may fully manage it) depends on the creation
path. Ownership is not assignment: the assignee pursues the work; the
owner controls the record.

| Creation path | Default owner | Default assignee | Who may fully manage it |
|---|---|---|---|
| Human `/goal set ...` | authenticated user | focused creature | user / admin; assignee may report + propose |
| Web / TUI Goal form | authenticated user | selected creature | user / admin; assignee may report + propose |
| Creature calls `drive_create(kind="goal")` | that creature | that creature | that creature / admin |
| Privileged `group_drive` create | graph or chosen actor | selected graph member | privileged graph authority / admin |
| Application Python / API | supplied service / user actor | explicit | owner / capability policy |

Because a user-owned Goal is assigned to *another* actor (the creature),
`/goal set` and `/goal assign` are graph-authority operations. The local
operator console supplies an explicit, audited operator elevation for
those two verbs; every other verb (`show` / `list` / `pause` / `resume`
/ `cancel` / `complete`) runs as the plain user owner and needs no
elevation.

## Completion is authoritative, per policy

`/goal complete` (and any completion) goes through a **proposal**, and
the `goal` registration's `completion_policy` decides what finalizes it:

- **`self_propose`** — an authorized proposal is accepted directly. The
  creature can complete its own Goal when it judges the objective met.
- **`user_confirm`** — only a **user-actor** proposal finalizes. A
  creature proposing completion is *not* accepted; completion stays with
  the human `/goal complete` path. This is how you keep a human in the
  loop.
- **`verifier`** — the proposal must carry non-empty evidence; an
  evidence-less completion is rejected.

Terrarium never decides whether the objective was truly achieved. It
only applies a transition that an authorized, policy-satisfying proposal
earned.

## Budgets pause, they never complete

A Goal's `budgets` bound how far `continue_when_ready` autonomy runs
before it must stop and check in. When a budget is exhausted:

- readiness stops re-arming, with an observable reason like
  `turn budget exhausted (3/3)`;
- the creature is guided to **propose a pause or block**;
- the Goal is **never** marked `completed` because a budget ran out.

Budget exhaustion is a "stop and ask", not a success. This is a hard
rule across the whole Drive runtime, not just Goal.

## Interrupting pauses the goal

Stop means stop. When the user interrupts a turn that a Goal delivery
started, the runtime acknowledges that delivery with reason
`user_interrupted` and moves the Goal to `paused` with the same status
reason. A `continue_when_ready` Goal therefore does **not** re-arm on
the next readiness scan, and a delivery that was still queued behind the
interrupted turn is settled the same way instead of being retried. The
Goal resumes only through an explicit `/goal resume` (or a wake through
the generic Drive surfaces). Every surface marks the start of a
drive-triggered turn in the transcript with the Goal id and delivery
reason, so a turn the creature did not start from user input is never
silent.

## Recovery is honest

A Goal is durable, so a creature can be interrupted mid-pursuit (a stop,
a crash). On restart, after the
[restoration barrier](../concepts/multi-agent/drive.md#the-restoration-barrier),
the still-active Goal comes back as a recovery event whose guidance is
explicit:

> A previous attempt may have executed side effects. Inspect the current
> world before repeating any side effect.

The framework never tells the creature to blindly replay, and it never
softens this warning. If a Goal step has external effects that must not
double-apply, the tool doing them should use its own idempotency key —
the delivery context exposes `delivery_id` for that. Delivery is
[at least once, not exactly once](../concepts/multi-agent/drive.md#delivery-at-least-once-logically-deduplicated).

## Without GoalPlugin

Because `/goal` is only a convenience, a user who has enabled the `goal`
registration but not the plugin can still:

- create a `goal` (or `generic`) Drive through Python / API / CLI / web;
- ask the creature to call `drive_create(kind="goal")` itself;
- have the creature manage its own caller-owned Goal through the generic
  `drive_*` tools;
- inspect and manage authorized Goals through the generic Drive surfaces.

`/goal` is optional syntax and UX layered on top — not the capability
boundary.

## See also

- [Drive concept](../concepts/multi-agent/drive.md): the runtime `/goal`
  composes over.
- [Programmatic Drive](programmatic-drive.md): the generic tools and
  service APIs `/goal` calls under the hood.
- The built-in implementation: `GoalDriveRegistration` + the GoalSpec
  helpers in `kohakuterrarium.terrarium.drive.goal`; `GoalPlugin` + the
  `/goal` command in `kohakuterrarium.builtins.plugins.goal`.
- [Configuration reference](../reference/configuration.md#drive-settings-drive-settingsyaml):
  enabling the registration in `drive-settings.yaml`.
