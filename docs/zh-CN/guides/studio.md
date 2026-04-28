---
title: Studio
summary: 使用 Studio 类管理 catalog、identity、运行中 session、保存的 session、attach policy 与编辑器流程。
tags:
  - guides
  - studio
  - python
  - embedding
---

# Studio 使用指南

`Studio` 是 `Terrarium` 运行引擎之上的管理 facade。它不是另一个 agent，也不是 Web UI；它是 CLI、HTTP API、dashboard 和你自己的 Python 代码可以共用的管理层。

使用 `Studio` 处理：

- catalog：包、内置工具/子代理、workspace 内的 Creature 与模块；
- identity：LLM profile/backend、API key、Codex OAuth、MCP、UI preferences；
- sessions：当前正在运行的 engine-backed session；
- persistence：保存的 `.kohakutr` session，包括 list/resume/history/viewer/export/delete；
- attach：聊天、频道观察、trace/logs、文件、pty 等 live attach policy；
- editors：workspace 内 Creature 与模块的 scaffold / save / delete 流程。

概念说明请见 [Studio](../concepts/studio.md) 与 [Terrarium](../concepts/multi-agent/terrarium.md)。完整 method map 请见 [Python API](../reference/python.md)。

## 快速开始

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
            "用一段话说明 KohakuTerrarium。",
        )
        async for chunk in stream:
            print(chunk, end="", flush=True)

asyncio.run(main())
```

在脚本里优先使用 `async with Studio()`，退出时会关闭底层的 `Terrarium` engine。如果你已经有 engine，也可以传入：

```python
from kohakuterrarium import Studio, Terrarium

engine = Terrarium()
studio = Studio(engine=engine)
```

## 构造方式

```python
async with Studio() as studio:
    print(studio.sessions.list())
```

创建一个已启动单个 Creature 的 Studio：

```python
studio = await Studio.with_creature("@kt-biome/creatures/general")
try:
    session = studio.sessions.list()[0]
    print(session.session_id)
finally:
    await studio.shutdown()
```

从 terrarium recipe 创建：

```python
studio = await Studio.from_recipe("@kt-biome/terrariums/swe_team")
try:
    session = studio.sessions.list()[0]
    print(session.kind, session.creatures)
finally:
    await studio.shutdown()
```

恢复保存的 session：

```python
async with await Studio.resume("~/.kohakuterrarium/sessions/alice.kohakutr") as studio:
    print(studio.sessions.list())
```

## 运行中的 sessions

Studio 把一个 live `Terrarium` graph 称为 session。单个 Creature graph 是 creature session；从 recipe 启动的 graph 是 terrarium session。

```python
async with Studio() as studio:
    session = await studio.sessions.start_creature(
        "@kt-biome/creatures/general",
        pwd="/tmp/my-project",
        llm_override="openai/gpt-4.1-mini",
    )

    print(session.session_id)
    print(session.kind)
    print(session.creatures)

    await studio.sessions.stop(session.session_id)
```

启动 multi-creature recipe：

```python
session = await studio.sessions.start_terrarium(
    "@kt-biome/terrariums/swe_team",
    pwd="/tmp/my-project",
)
```

每个 Creature 操作都以 `(session_id, creature_id)` 为作用域：

```python
sid = session.session_id
cid = session.creatures[0]["creature_id"]

stream = await studio.sessions.chat.chat(sid, cid, "Hello")
async for chunk in stream:
    print(chunk, end="")

history = studio.sessions.chat.history(sid, cid)
branches = studio.sessions.chat.branches(sid, cid)
```

控制与状态：

```python
await studio.sessions.ctl.interrupt(sid, cid)
jobs = studio.sessions.ctl.list_jobs(sid, cid)
await studio.sessions.ctl.cancel_job(sid, cid, jobs[0]["job_id"])

scratchpad = studio.sessions.state.scratchpad(sid, cid)
studio.sessions.state.patch_scratchpad(sid, cid, {"phase": "review"})
print(studio.sessions.state.working_dir(sid, cid))
print(studio.sessions.state.system_prompt(sid, cid)["text"])
```

Plugin、model 与 slash command：

```python
plugins = studio.sessions.plugins.list(sid, cid)
await studio.sessions.plugins.toggle(sid, cid, "my_plugin")

studio.sessions.model.switch(sid, cid, "openai/gpt-4.1")
options = studio.sessions.model.native_tool_options(sid, cid)
await studio.sessions.command.execute(sid, cid, "status")
```

## Topology

Studio 提供 session-scoped topology helper，底层仍然调用 `Terrarium` engine：

```python
await studio.sessions.add_channel(session.session_id, "review")
await studio.sessions.connect("coder", "reviewer", channel="review")
await studio.sessions.disconnect("coder", "reviewer", channel="review")
```

如果需要更底层的 runtime API，直接使用 `studio.engine`：

```python
async for ev in studio.engine.subscribe():
    print(ev.kind, ev.creature_id, ev.payload)
```

## Catalog 与 identity

```python
packages = studio.catalog.packages.list()
remote = studio.catalog.packages.remote()
pkg_name = studio.catalog.packages.install(
    "https://github.com/Kohaku-Lab/kt-biome.git"
)

tools = studio.catalog.builtins.list("tools")
bash_info = studio.catalog.builtins.info("bash")
```

```python
for backend in studio.identity.llm.list_backends():
    print(backend["name"], backend["backend_type"])

print("default:", studio.identity.llm.get_default())
studio.identity.llm.set_default("openai/gpt-4.1-mini")

studio.identity.keys.set("openai", "sk-...")
print(studio.identity.keys.list())
studio.identity.keys.delete("openai")
```

MCP registry：

```python
studio.identity.mcp.upsert({
    "name": "sqlite",
    "transport": "stdio",
    "command": "mcp-server-sqlite",
    "args": ["/tmp/app.db"],
})
print(studio.identity.mcp.list())
```

## 保存的 sessions

```python
for saved in studio.persistence.list():
    print(saved["name"], saved.get("status"))

path = studio.persistence.resolve_path("alice")
index = studio.persistence.history_index(path)
root_history = studio.persistence.history(path, "root")

session = await studio.persistence.resume("alice")
deleted_paths = studio.persistence.delete("alice")
```

Viewer helper 生成 web session viewer 使用的 payload：

```python
from kohakuterrarium.session.store import SessionStore

store = SessionStore(path)
try:
    tree = studio.persistence.viewer.tree(store, "alice")
    summary = studio.persistence.viewer.summary(store)
finally:
    store.close()
```

## Attach 与 editors

查询某个 Creature 或 session 支持哪些 live attach mode：

```python
policies = studio.attach.policies_for_creature(cid)
session_policies = studio.attach.policies_for_session(sid)
```

目前 Python facade 主要提供 policy advertisement；实际 live stream 由 HTTP/WebSocket adapter 使用。

Workspace editor helper：

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

## 何时用 Studio，何时用 Terrarium

- 用 `Terrarium` 处理 runtime mechanics：新增 Creature、connect channel、hot-plug、event subscription。
- 用 `Studio` 处理 user-facing management：包、设置、运行中 session、保存 session、attach policy、editor workflow。
- 用 `Agent` 处理单个 Creature 内部的低层控制。

## 参见

- [程序化使用](programmatic-usage.md)
- [Terrarium 指南](terrariums.md)
- [会话与恢复](sessions.md)
- [Python API](../reference/python.md)
