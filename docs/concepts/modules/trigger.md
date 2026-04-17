# Trigger

## What it is

A **trigger** is anything that wakes the controller without explicit
user input. Timers, idle detectors, webhook receivers, channel
listeners, and monitor conditions are all triggers. Each one runs as a
background task and pushes `TriggerEvent`s onto the event queue when
its firing condition is met.

## Why it exists

A pure input-driven agent can only work when a user is around. But
real agents need to:

- run `/loop`-style recurring plans while nobody is watching;
- react to a channel message from another creature;
- wake up N seconds after the last event to summarise;
- receive a webhook from an external service;
- poll a resource and fire when a condition flips.

You could bolt each of these on as ad-hoc code. The framework says:
they are all the same thing — event sources — and they deserve one
abstraction.

## How we define it

A trigger implements:

- an async generator `fire()` that yields `TriggerEvent`s;
- `to_resume_dict()` / `from_resume_dict()` so the trigger can be
  persisted and restored across sessions;
- a `trigger_id` for addressability (so tools can list / cancel it).

The trigger manager starts one background task per registered trigger.
Each task loops over `fire()` and pushes events.

## How we implement it

Built-in trigger types:

- **`timer`** — fires every N seconds or on a cron schedule.
- **`idle`** — fires if N seconds pass without any event.
- **`channel`** — listens on a named channel; fires on message.
- **`webhook` / `http`** — receives POST requests.
- **`monitor`** — fires when a predicate over scratchpad / context
  returns true.

`TriggerManager` (`core/trigger_manager.py`) owns the running tasks,
wires completions into the agent's event callback, and persists
trigger state to the session store so `kt resume` can re-create them.

Config-time triggers are declared in `config.triggers[]`. Runtime
triggers can be created from a tool (`create_trigger`) or
programmatically via `agent.add_trigger(...)`.

## What you can therefore do

- **Recurring agents.** A `timer` trigger that fires every hour lets a
  creature self-refresh its view of a file system or a set of metrics.
- **Cross-creature wiring.** A `channel` trigger is the mechanism that
  makes [terrariums](../multi-agent/terrarium.md) actually work.
- **Idle-driven summaries.** An `idle` trigger that fires after two
  minutes of silence can dispatch a `summarize` sub-agent and send the
  result to a log channel.
- **External signalling.** A `webhook` trigger turns a creature into a
  receiver for CI hooks, deployment events, or upstream product
  traffic.
- **Adaptive watchers.** A custom trigger whose `fire()` runs a small
  nested agent can decide *when* to wake the outer creature based on
  judgement, not a fixed rule. See [patterns](../patterns.md).

## Don't be bounded

A creature can have zero triggers. It can also have only triggers
(no input). The framework does not rank these configurations; it just
supports them all. And because a trigger is itself a Python object,
you can put an agent inside one — a watcher that *thinks* about
whether to fire rather than following a hand-coded rule. That pattern
is what makes "agentic ambient behaviour" cheap to build.

## See also

- [Input](input.md) — the specific-case trigger for user content.
- [Channel](channel.md) — the trigger type that underpins multi-agent communication.
- [reference/builtins.md — Triggers](../../reference/builtins.md) — full inventory.
- [patterns.md — adaptive watcher](../patterns.md) — agent-inside-trigger.
