---
title: Studio
summary: The management layer above the Terrarium engine — catalog, identity, sessions, persistence, attach policies, and editors.
tags:
  - concepts
  - studio
  - architecture
---

# Studio

## What it is

**Studio** is the management layer above the `Terrarium` runtime engine.
It is not a UI and it is not another agent. It is the shared Python
surface for things that every UI and automation script otherwise has to
re-implement:

- package and built-in **catalog** lookup;
- LLM profile, API key, MCP, and UI-preference **identity** state;
- active **session lifecycle** over the `Terrarium` engine;
- saved-session **persistence**: list, resume, fork, history, export;
- live **attach policies**: IO chat, channel observer, trace, logs,
  workspace files, and pty;
- Studio **editors**: workspace creature/module CRUD and scaffolding.

The Python facade is `kohakuterrarium.Studio`. The HTTP API, web UI,
`kt` commands, and your own code should all delegate to the same Studio
operations instead of duplicating catalog/session/settings logic.

## The layer stack

Think in three programmatic facades:

| Facade | Layer | Owns |
|---|---|---|
| `Agent` / creature internals | Creature | One LLM controller with tools, triggers, sub-agents, plugins, memory, I/O. |
| `Terrarium` | Runtime engine | Live creatures, graph topology, channels, output wiring, hot-plug, engine events. |
| `Studio` | Management layer | Catalog, identity, active sessions, saved sessions, attach policies, editor workflows. |

Lower layers do not import higher layers:

- Creature code does not know `Terrarium` or `Studio` exist.
- `Terrarium` hosts creatures, but does not know about `Studio`, HTTP,
  or CLI.
- `Studio` takes a `Terrarium` engine and adds management semantics on
  top.
- `api/`, `cli/`, and the frontend are adapters over Studio.

This is the practical result of the structure-hierarchy plan in
`plans/structure-hierarchy/`: one runtime engine, one management layer,
and thin UI adapters.

## Why Studio exists

Before Studio, the same responsibilities lived in multiple places:

- package listing in both `kt list` and web routes;
- profile/key/MCP logic in `kt config`, `kt model`, `kt login`, and
  `/api/settings`;
- active agent vs terrarium routes with duplicated lifecycle logic;
- saved-session viewer/export/diff/resume code separate from runtime
  session creation;
- WebSocket chat/log/file/terminal endpoints each with their own attach
  policy.

Studio turns those into one implementation per concern. The CLI prints
terminal-shaped output. The HTTP API serialises JSON. The frontend
renders panels. But all of them ask Studio to do the work.

## Sessions in Studio vs graphs in Terrarium

`Terrarium` owns **graphs**: connected components of live creatures.
A solo creature is one graph. A multi-creature team is also one graph.
Connecting two graphs merges them; disconnecting can split them.

Studio names a graph a **session** when a user or UI is managing it.
That session handle carries:

- `session_id` — the graph id;
- `kind` — `"creature"` for a one-creature graph, `"terrarium"` for a
  multi-creature graph started from a recipe;
- creature summaries for UI tabs and per-creature operations;
- metadata Studio cares about, such as config path, working directory,
  and creation time.

This is why the public active-session API uses URLs like
`/api/sessions/{sid}/creatures/{cid}/...`: a creature operation is
always scoped by the graph/session that owns it.

Saved sessions are different: they are `.kohakutr` files on disk.
Studio persistence can list them, resume them into a running engine,
fork them, and build post-hoc viewer payloads.

## Attach policies

Not every creature is a chat bot. A monitor might have no user input; a
scheduler might only emit logs; a multi-agent team might need a channel
observer rather than a chat box. Studio separates **running** a creature
from **attaching** a UI to it.

Attach policies answer: "what live view/control surface makes sense for
this running creature or session?"

| Policy | Shape | Use |
|---|---|---|
| IO chat | read/write stream | Conversational creatures. |
| Channel observer | read-only stream | Inspect graph channel traffic without consuming queue messages. |
| Trace | read-only stream | Engine events, turns, topology changes, tool activity. |
| Log | read-only stream | Process/runtime logs. |
| Workspace files | browse/watch | File panels and editor refresh. |
| PTY | read/write terminal | Shell attached to a creature working directory. |

The web dashboard exposes these through HTTP/WebSocket adapters. The
`Studio.attach` namespace currently advertises available policies; more
programmatic streaming helpers can live there without changing the
runtime engine.

## Don't confuse Studio with the web dashboard

The web dashboard is a UI. Studio is the Python management layer the
dashboard calls. You can use Studio with no web server:

```python
from kohakuterrarium import Studio

async with Studio() as studio:
    session = await studio.sessions.start_creature("@kt-biome/creatures/general")
    print(session.session_id)
```

You can also run the web dashboard, which mounts FastAPI routes and
WebSocket endpoints over the same Studio/Terrarium concepts:

```bash
kt web
```

Both paths share the same mental model: Studio manages sessions;
Terrarium runs creatures.

## When to use which layer

- Use **`Agent`** directly when you need full low-level control over one
  creature's modules, event queue, output handlers, or test harness.
- Use **`Terrarium`** when you need runtime topology: add creatures,
  connect channels, hot-plug, observe engine events.
- Use **`Studio`** when you are building a UI, service, automation, or
  script that needs user-facing management concerns: packages,
  settings, active sessions, saved sessions, attach policies, or
  editors.

## See also

- [Terrarium](multi-agent/terrarium.md) — the runtime engine Studio wraps.
- [Programmatic Usage](../guides/programmatic-usage.md) — how to embed `Studio` and `Terrarium`.
- [Studio guide](../guides/studio.md) — task-oriented examples.
- [Python API](../reference/python.md) — signatures and namespace map.
