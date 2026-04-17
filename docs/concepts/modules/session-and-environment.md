# Session and environment

## What it is

Two levels of state:

- **Session** — private to one creature. Holds the creature's
  scratchpad, private channels, TUI reference, job store, and any
  custom extras.
- **Environment** — shared across a whole run (specifically, a whole
  terrarium). Holds the shared channel registry and a small
  custom-context dict.

A standalone creature has a session. A terrarium has an environment
and one session per creature.

## Why it exists

In a multi-agent system, the wrong default is "everything is shared."
If every creature can write every other creature's scratchpad, you
have built Global Mutable State with extra steps. Debugging becomes
impossible.

The framework's default is the reverse: **private by default, shared
by opt-in**. A creature keeps its own state unless it explicitly sends
to a shared channel. The terrarium is the only thing that sees all the
creatures; the creatures see only their own session and the shared
channels they asked to listen on.

## How we define it

```
Environment (optional, one per terrarium)
├── shared_channels  (ChannelRegistry)
├── context          (dict, user-defined)
└── <no private state here>

Session (one per creature)
├── scratchpad       (key-value, private)
├── channels         (private ChannelRegistry; may be aliased to shared)
├── tui              (TUI reference, when applicable)
├── extras           (dict, user-defined)
└── key              (session identifier)
```

Rules:

- A creature only has one session.
- An environment is shared across creatures. A standalone creature
  can skip it.
- Shared channels live on the environment. A creature opts in by
  adding a `ChannelTrigger` for a given channel name.
- Scratchpad is always session-private.

## How we implement it

`core/session.py` defines `Session` and helpers for fetching/creating
sessions by key. `core/environment.py` defines `Environment`.
`TerrariumRuntime` creates one environment and attaches a session to
each creature.

The `scratchpad` builtin tool reads/writes the current creature's
session scratchpad. The `send_message` tool picks the right channel
registry (private first, then shared).

## What you can therefore do

- **Private cross-turn memory.** Each creature uses its scratchpad as
  a working notebook; nothing leaks.
- **Shared rendezvous.** Two creatures that both listen on a shared
  channel can coordinate without knowing each other's internals.
- **Session as state bus for one creature.** Cooperating tools inside
  one creature use scratchpad as a KV rendezvous.
- **Environment-scoped custom context.** An HTTP app that drives a
  terrarium can stash user-identity / request-id on the environment's
  `context` dict and let plugins pick it up.

## Don't be bounded

Standalone creatures do not need environments. Trigger-only creatures
do not strictly need scratchpads. The framework enforces the
private/shared split only where it matters — it is happy to treat a
single-creature session as the only state.

## See also

- [Channel](channel.md) — the opt-in sharing primitive.
- [Multi-agent / terrarium](../multi-agent/terrarium.md) — where environments matter.
- [impl-notes/session-persistence](../impl-notes/session-persistence.md) — how session state actually lives on disk.
