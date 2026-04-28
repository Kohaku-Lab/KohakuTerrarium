---
title: Studio
summary: Use the Studio class to manage catalog, identity, active sessions, saved sessions, attach policies, and editor workflows.
tags:
  - guides
  - studio
  - python
  - embedding
---

# Studio Guide

For readers embedding KohakuTerrarium in a Python service, automation
script, or custom dashboard.

`Studio` is the management facade above the `Terrarium` runtime engine.
It wraps an engine and groups the shared operations that CLI commands
and HTTP routes also use: catalog, identity, sessions, persistence,
attach policies, and editors.

Concept primer: [Studio](../concepts/studio.md), [Terrarium](../concepts/multi-agent/terrarium.md). Exact method names live in [Python API](../reference/python.md).

## Quick start

```python
import asyncio
from kohakuterrarium import Studio

async def main():
    async with Studio() as studio:
        session = await studio.sessions.start_creature(
            "@kt-biome/creatures/general"
        )
        cid = session.creatures[0]["creature_id"]

        stream = await studio.sessions.chat.chat(
            session.session_id,
            cid,
            "Explain what KohakuTerrarium is in one paragraph.",
        )
        async for chunk in stream:
            print(chunk, end="", flush=True)

asyncio.run(main())
```

Use `async with Studio()` for scripts. It starts/owns a `Terrarium`
engine and shuts it down on exit. If you already have an engine, pass it
in:

```python
from kohakuterrarium import Studio, Terrarium

engine = Terrarium()
studio = Studio(engine=engine)
```

## Construction patterns

### Empty Studio

```python
async with Studio() as studio:
    print(studio.sessions.list())
```

This creates an empty engine. Add sessions with `studio.sessions`.

### One creature

```python
studio = await Studio.with_creature("@kt-biome/creatures/general")
try:
    sessions = studio.sessions.list()
    print(sessions[0].session_id)
finally:
    await studio.shutdown()
```

`with_creature()` is convenient for simple embedding. It returns a
`Studio`; get the created session via `studio.sessions.list()`.

### Terrarium recipe

```python
studio = await Studio.from_recipe("@kt-biome/terrariums/swe_team")
try:
    session = studio.sessions.list()[0]
    print(session.kind, session.creatures)
finally:
    await studio.shutdown()
```

A recipe creates one graph/session containing every creature declared in
the terrarium config.

### Resume saved session

```python
async with await Studio.resume("~/.kohakuterrarium/sessions/alice.kohakutr") as studio:
    print(studio.sessions.list())
```

For an already-created Studio, use the persistence namespace:

```python
async with Studio() as studio:
    session = await studio.persistence.resume("alice")
    print(session.session_id)
```

The resume helper accepts a full path or a saved-session name resolvable
from the default session directory.

## Active sessions

Studio calls a live `Terrarium` graph a **session**. A one-creature graph
is a creature session; a recipe graph is a terrarium session.

```python
async with Studio() as studio:
    session = await studio.sessions.start_creature(
        "@kt-biome/creatures/general",
        pwd="/tmp/my-project",
        llm_override="openai/gpt-4.1-mini",
    )

    print(session.session_id)
    print(session.kind)        # "creature"
    print(session.creatures)   # list of creature summary dicts

    await studio.sessions.stop(session.session_id)
```

Start a multi-creature recipe:

```python
session = await studio.sessions.start_terrarium(
    "@kt-biome/terrariums/swe_team",
    pwd="/tmp/my-project",
)
```

List and inspect:

```python
for item in studio.sessions.list():
    print(item.session_id, item.kind, item.name)

handle = studio.sessions.get(session.session_id)
```

Find a creature inside a session:

```python
creature = studio.sessions.find_creature(session.session_id, "swe")
print(creature.agent.config.name)
```

## Chat and creature-scoped operations

Creature operations are scoped by `(session_id, creature_id)`.

```python
sid = session.session_id
cid = session.creatures[0]["creature_id"]

stream = await studio.sessions.chat.chat(sid, cid, "Hello")
async for chunk in stream:
    print(chunk, end="")

history = studio.sessions.chat.history(sid, cid)
branches = studio.sessions.chat.branches(sid, cid)
```

Regenerate, edit, and rewind:

```python
await studio.sessions.chat.regenerate(sid, cid)
await studio.sessions.chat.edit_message(sid, cid, msg_idx=4, content="better prompt")
await studio.sessions.chat.rewind(sid, cid, msg_idx=2)
```

Control jobs and interrupts:

```python
await studio.sessions.ctl.interrupt(sid, cid)
jobs = studio.sessions.ctl.list_jobs(sid, cid)
await studio.sessions.ctl.cancel_job(sid, cid, jobs[0]["job_id"])
```

State inspection:

```python
scratchpad = studio.sessions.state.scratchpad(sid, cid)
studio.sessions.state.patch_scratchpad(sid, cid, {"phase": "review"})
print(studio.sessions.state.env(sid, cid))
print(studio.sessions.state.working_dir(sid, cid))
print(studio.sessions.state.system_prompt(sid, cid)["text"])
```

Plugins, model switching, and slash commands:

```python
plugins = studio.sessions.plugins.list(sid, cid)
await studio.sessions.plugins.toggle(sid, cid, "my_plugin")

studio.sessions.model.switch(sid, cid, "openai/gpt-4.1")
options = studio.sessions.model.native_tool_options(sid, cid)
await studio.sessions.command.execute(sid, cid, "status")
```

## Topology management

Studio exposes session-scoped topology helpers over the underlying
engine.

```python
await studio.sessions.add_channel(session.session_id, "review")
await studio.sessions.connect("coder", "reviewer", channel="review")
await studio.sessions.disconnect("coder", "reviewer", channel="review")
```

When a connection joins two previously separate graphs, the Terrarium
engine merges them and Studio sees one session. When a disconnect splits
a graph, the engine copies the parent session history into each child
store.

Use raw `studio.engine` when you need lower-level engine access:

```python
async for ev in studio.engine.subscribe():
    print(ev.kind, ev.creature_id, ev.payload)
```

## Catalog

Catalog helpers are read/management operations shared by CLI and HTTP.

```python
packages = studio.catalog.packages.list()
remote = studio.catalog.packages.remote()
scanned = studio.catalog.packages.scan()

pkg_name = studio.catalog.packages.install(
    "https://github.com/Kohaku-Lab/kt-biome.git"
)
studio.catalog.packages.update(pkg_name)
```

Built-ins and schemas:

```python
tools = studio.catalog.builtins.list("tools")
bash_info = studio.catalog.builtins.info("bash")
schema = studio.catalog.introspect.builtin_schema("tool")
```

Workspace-backed catalog calls take a workspace object from the editor
layer (for example a local workspace opened by the API):

```python
creatures = studio.catalog.creatures.list(workspace)
modules = studio.catalog.modules.list(workspace, "tools")
```

## Identity

Identity groups LLM profiles/backends, API keys, Codex OAuth, MCP
servers, and UI preferences.

```python
for backend in studio.identity.llm.list_backends():
    print(backend["name"], backend["backend_type"])

print("default:", studio.identity.llm.get_default())
studio.identity.llm.set_default("openai/gpt-4.1-mini")

profiles = studio.identity.llm.list_profiles()
models = studio.identity.llm.list_models()
```

API keys:

```python
studio.identity.keys.set("openai", "sk-...")
print(studio.identity.keys.list())
studio.identity.keys.delete("openai")
```

MCP registry:

```python
studio.identity.mcp.upsert({
    "name": "sqlite",
    "transport": "stdio",
    "command": "mcp-server-sqlite",
    "args": ["/tmp/app.db"],
})
print(studio.identity.mcp.list())
```

## Saved-session persistence

List saved sessions:

```python
for saved in studio.persistence.list():
    print(saved["name"], saved.get("status"))
```

Resolve and view a saved session:

```python
path = studio.persistence.resolve_path("alice")
index = studio.persistence.history_index(path)
root_history = studio.persistence.history(path, "root")
```

Resume into the live engine:

```python
session = await studio.persistence.resume("alice")
```

Delete all versions of a saved session:

```python
deleted_paths = studio.persistence.delete("alice")
```

Viewer helpers build the payloads used by the web session viewer:

```python
from kohakuterrarium.session.store import SessionStore

store = SessionStore(path)
try:
    tree = studio.persistence.viewer.tree(store, "alice")
    summary = studio.persistence.viewer.summary(store)
finally:
    store.close()
```

## Attach policies

Ask which attach modes make sense for a creature or session:

```python
policies = studio.attach.policies_for_creature(cid)
session_policies = studio.attach.policies_for_session(sid)
```

The current facade exposes policy advertisement. The concrete live
streams are used by the HTTP/WebSocket adapters (`/ws/sessions/...`,
`/ws/logs`, `/ws/files/...`, `/ws/sessions/.../pty`). Programmatic
streaming helpers can be added under `studio.attach` without changing
`Terrarium`.

## Editors

The editor namespace is for workspace files and scaffolding. It is the
Python layer below the web Studio editor.

```python
from pathlib import Path

creatures_dir = Path("./creatures")
path = studio.editors.creatures.scaffold(creatures_dir, "my-agent")
studio.editors.creatures.write_prompt(
    creatures_dir,
    "my-agent",
    "prompts/system.md",
    "You are a concise assistant.",
)
```

Module helpers mirror the custom-module editor flows:

```python
studio.editors.modules.scaffold(workspace, "tools", "my_tool")
studio.editors.modules.save_doc(workspace, "tools", "my_tool", "# My tool")
```

## Studio vs Terrarium

Use `Terrarium` when you only need runtime mechanics:

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")
    await engine.connect(a, b, channel="handoff")
```

Use `Studio` when you also need management concerns:

```python
async with Studio() as studio:
    print(studio.catalog.packages.list())
    session = await studio.sessions.start_creature("@kt-biome/creatures/general")
    await studio.persistence.resume("older-session")
```

`Studio.engine` is available when you need to drop down to raw runtime
operations.

## Common pitfalls

- **Using Studio as if it were an agent.** Studio does not have an LLM.
  It manages sessions; creatures inside the engine run LLM controllers.
- **Forgetting session scope.** Per-creature operations need both
  `session_id` and `creature_id`.
- **Keeping Studio open forever in scripts.** Use `async with Studio()`
  or call `await studio.shutdown()`.
- **Re-implementing settings/package/session logic in a UI.** Call
  Studio or the HTTP routes that delegate to Studio; do not duplicate
  those policies.

## See also

- [Programmatic Usage](programmatic-usage.md) — full Python embedding guide.
- [Terrariums](terrariums.md) — runtime topology and recipes.
- [Sessions](sessions.md) — saved `.kohakutr` files and resume.
- [Python API](../reference/python.md) — method reference.
