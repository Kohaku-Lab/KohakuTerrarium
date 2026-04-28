---
title: Studio
summary: 使用 Studio 類別管理 catalog、identity、執行中 session、保存的 session、attach policy 與編輯器流程。
tags:
  - guides
  - studio
  - python
  - embedding
---

# Studio 使用指南

`Studio` 是 `Terrarium` 執行引擎之上的管理 facade。它不是另一個 agent，也不是網頁 UI；它是 CLI、HTTP API、dashboard 與你自己的 Python 程式碼可以共用的管理層。

使用 `Studio` 來處理：

- catalog：套件、內建工具/子代理、workspace 內的生物與模組；
- identity：LLM profile/backend、API key、Codex OAuth、MCP、UI preferences；
- sessions：目前正在執行的 engine-backed session；
- persistence：保存的 `.kohakutr` session，包含 list/resume/history/viewer/export/delete；
- attach：聊天、頻道觀察、trace/logs、檔案、pty 等 live attach policy；
- editors：workspace 內生物與模組的 scaffold / save / delete 流程。

概念說明請見 [Studio](../concepts/studio.md) 與 [Terrarium](../concepts/multi-agent/terrarium.md)。完整 method map 請見 [Python API](../reference/python.md)。

## 快速開始

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
            "用一段話說明 KohakuTerrarium。",
        )
        async for chunk in stream:
            print(chunk, end="", flush=True)

asyncio.run(main())
```

在 script 裡優先使用 `async with Studio()`，離開時會關閉底下的 `Terrarium` engine。如果你已經有 engine，也可以傳入：

```python
from kohakuterrarium import Studio, Terrarium

engine = Terrarium()
studio = Studio(engine=engine)
```

## 建立方式

```python
async with Studio() as studio:
    print(studio.sessions.list())
```

建立一個已啟動單一生物的 Studio：

```python
studio = await Studio.with_creature("@kt-biome/creatures/general")
try:
    session = studio.sessions.list()[0]
    print(session.session_id)
finally:
    await studio.shutdown()
```

從 terrarium recipe 建立：

```python
studio = await Studio.from_recipe("@kt-biome/terrariums/swe_team")
try:
    session = studio.sessions.list()[0]
    print(session.kind, session.creatures)
finally:
    await studio.shutdown()
```

恢復保存的 session：

```python
async with await Studio.resume("~/.kohakuterrarium/sessions/alice.kohakutr") as studio:
    print(studio.sessions.list())
```

## 執行中 sessions

Studio 把一個 live `Terrarium` graph 稱為 session。單一生物 graph 是 creature session；從 recipe 啟動的 graph 是 terrarium session。

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

啟動 multi-creature recipe：

```python
session = await studio.sessions.start_terrarium(
    "@kt-biome/terrariums/swe_team",
    pwd="/tmp/my-project",
)
```

每個 creature 操作都以 `(session_id, creature_id)` 為 scope：

```python
sid = session.session_id
cid = session.creatures[0]["creature_id"]

stream = await studio.sessions.chat.chat(sid, cid, "Hello")
async for chunk in stream:
    print(chunk, end="")

history = studio.sessions.chat.history(sid, cid)
branches = studio.sessions.chat.branches(sid, cid)
```

控制與狀態：

```python
await studio.sessions.ctl.interrupt(sid, cid)
jobs = studio.sessions.ctl.list_jobs(sid, cid)
await studio.sessions.ctl.cancel_job(sid, cid, jobs[0]["job_id"])

scratchpad = studio.sessions.state.scratchpad(sid, cid)
studio.sessions.state.patch_scratchpad(sid, cid, {"phase": "review"})
print(studio.sessions.state.working_dir(sid, cid))
print(studio.sessions.state.system_prompt(sid, cid)["text"])
```

Plugin、model 與 slash command：

```python
plugins = studio.sessions.plugins.list(sid, cid)
await studio.sessions.plugins.toggle(sid, cid, "my_plugin")

studio.sessions.model.switch(sid, cid, "openai/gpt-4.1")
options = studio.sessions.model.native_tool_options(sid, cid)
await studio.sessions.command.execute(sid, cid, "status")
```

## Topology

Studio 提供 session-scoped topology helper，底層仍然呼叫 `Terrarium` engine：

```python
await studio.sessions.add_channel(session.session_id, "review")
await studio.sessions.connect("coder", "reviewer", channel="review")
await studio.sessions.disconnect("coder", "reviewer", channel="review")
```

如果需要更底層的 runtime API，直接使用 `studio.engine`：

```python
async for ev in studio.engine.subscribe():
    print(ev.kind, ev.creature_id, ev.payload)
```

## Catalog 與 identity

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

Viewer helper 產生 web session viewer 使用的 payload：

```python
from kohakuterrarium.session.store import SessionStore

store = SessionStore(path)
try:
    tree = studio.persistence.viewer.tree(store, "alice")
    summary = studio.persistence.viewer.summary(store)
finally:
    store.close()
```

## Attach 與 editors

查詢某個 creature 或 session 支援哪些 live attach mode：

```python
policies = studio.attach.policies_for_creature(cid)
session_policies = studio.attach.policies_for_session(sid)
```

目前 Python facade 主要提供 policy advertisement；實際 live stream 由 HTTP/WebSocket adapter 使用。

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

## 何時用 Studio，何時用 Terrarium

- 用 `Terrarium` 處理 runtime mechanics：新增 creature、connect channel、hot-plug、event subscription。
- 用 `Studio` 處理 user-facing management：套件、設定、執行中 session、保存 session、attach policy、editor workflow。
- 用 `Agent` 處理單一 creature 內部的低階控制。

## 參見

- [程式化使用](programmatic-usage.md)
- [生態瓶](terrariums.md)
- [工作階段與恢復](sessions.md)
- [Python API](../reference/python.md)
