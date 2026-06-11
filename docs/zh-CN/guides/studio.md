---
title: Studio
summary: 用 Studio 类管理目录、身份、活动会话、已保存会话、挂载策略与编辑器工作流。
tags:
  - guides
  - studio
  - python
  - embedding
---

# Studio 指南

写给把 KohakuTerrarium 嵌进 Python 服务、自动化脚本或自定义仪表盘的
读者。

`Studio` 是 `Terrarium` 运行时引擎之上的管理门面。它包住一个引擎，
把 CLI 命令和 HTTP 路由共用的操作归到一处：目录、身份、会话、
持久化、挂载策略和编辑器。

概念入门：[Studio](../concepts/studio.md)、
[Terrarium](../concepts/multi-agent/terrarium.md)。精确方法名见
[Python API](../reference/python.md)。

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
            "Explain what KohakuTerrarium is in one paragraph.",
        )
        async for chunk in stream:
            print(chunk, end="", flush=True)

asyncio.run(main())
```

脚本里用 `async with Studio()`。它会启动并持有一个 `Terrarium`
引擎，退出时关掉。已经有引擎的话直接传进去：

```python
from kohakuterrarium import Studio, Terrarium

engine = Terrarium()
studio = Studio(engine=engine)
```

## 构造方式

### 空 Studio

```python
async with Studio() as studio:
    print(studio.sessions.list())
```

这会创建一个空引擎。用 `studio.sessions` 添加会话。

### 单个生物

```python
studio = await Studio.with_creature("@kt-biome/creatures/general")
try:
    sessions = studio.sessions.list()
    print(sessions[0].session_id)
finally:
    await studio.shutdown()
```

`with_creature()` 适合简单嵌入。它返回一个 `Studio`；创建出的会话
通过 `studio.sessions.list()` 获取。

### Terrarium 配方

```python
studio = await Studio.from_recipe("@kt-biome/terrariums/swe_team")
try:
    session = studio.sessions.list()[0]
    print(session.kind, session.creatures)
finally:
    await studio.shutdown()
```

配方创建一张图 / 一个会话，包含 terrarium 配置里声明的所有生物
(creature)。该会话是完整注册的：有会话存储、能按名字出现在
`studio.sessions.list()` 里、之后可恢复，和 `start_terrarium` 走的
是同一条路。

### 恢复已保存的会话

```python
async with await Studio.resume("~/.kohakuterrarium/sessions/alice.kohakutr") as studio:
    print(studio.sessions.list())
```

对已经创建好的 Studio，用 persistence 命名空间：

```python
async with Studio() as studio:
    session = await studio.persistence.resume("alice")
    print(session.session_id)
```

恢复辅助函数接受完整路径，或能从默认会话目录解析出来的已保存会话名。

## 活动会话

Studio 把一张活动的 `Terrarium` 图称为**会话**。单生物的图是
creature 会话；配方图是 terrarium 会话。

```python
async with Studio() as studio:
    session = await studio.sessions.start_creature(
        "@kt-biome/creatures/general",
        pwd="/tmp/my-project",
        llm="openai/gpt-4.1-mini",     # profile / preset / selector
        name="scratch-helper",         # display-name override
    )

    print(session.session_id)
    print(session.kind)        # "creature"
    print(session.creatures)   # list of creature summary dicts

    await studio.sessions.stop(session.session_id)
```

（在多节点 lab 部署里，`start_creature(..., on_node="worker-a")` 把
生物放到指定工作机上；默认的 `"_host"` 在本地运行。）

启动多生物配方：

```python
session = await studio.sessions.start_terrarium(
    "@kt-biome/terrariums/swe_team",
    pwd="/tmp/my-project",
    llm="openai/gpt-4.1-mini",
)
```

列出与检视：

```python
for item in studio.sessions.list():
    print(item.session_id, item.kind, item.name)

handle = studio.sessions.get(session.session_id)
```

在会话里找一个生物：

```python
creature = studio.sessions.find_creature(session.session_id, "swe")
print(creature.agent.config.name)
```

## 聊天与生物级操作

生物操作的作用域是 `(session_id, creature_id)`。

```python
sid = session.session_id
cid = session.creatures[0]["creature_id"]

stream = await studio.sessions.chat.chat(sid, cid, "Hello")
async for chunk in stream:
    print(chunk, end="")

history = studio.sessions.chat.history(sid, cid)
branches = studio.sessions.chat.branches(sid, cid)
```

重生成、编辑、回退：分支相关的关键字参数
（`turn_index=`、`user_position=`、`branch_view=`）与 Web 查看器发送
的一致，脚本可以精确指向一条被编辑过的对话的特定分支：

```python
await studio.sessions.chat.regenerate(sid, cid)
await studio.sessions.chat.regenerate(sid, cid, turn_index=3)
await studio.sessions.chat.edit_message(sid, cid, msg_idx=4, content="better prompt")
await studio.sessions.chat.rewind(sid, cid, msg_idx=2)
```

任务控制与中断：

```python
await studio.sessions.ctl.interrupt(sid, cid)
jobs = studio.sessions.ctl.list_jobs(sid, cid)
await studio.sessions.ctl.cancel_job(sid, cid, jobs[0]["job_id"])
```

状态检视：

```python
scratchpad = studio.sessions.state.scratchpad(sid, cid)
studio.sessions.state.patch_scratchpad(sid, cid, {"phase": "review"})
print(studio.sessions.state.env(sid, cid))
print(studio.sessions.state.working_dir(sid, cid))
print(studio.sessions.state.system_prompt(sid, cid)["text"])
```

插件、模型切换、斜杠命令：

```python
plugins = studio.sessions.plugins.list(sid, cid)
await studio.sessions.plugins.toggle(sid, cid, "my_plugin")

studio.sessions.model.switch(sid, cid, "openai/gpt-4.1")
options = studio.sessions.model.native_tool_options(sid, cid)
await studio.sessions.command.execute(sid, cid, "status")
```

## 拓扑管理

Studio 在底层引擎之上提供会话级的拓扑辅助方法。

```python
await studio.sessions.add_channel(session.session_id, "review")
await studio.sessions.connect("coder", "reviewer", channel="review")
await studio.sessions.disconnect("coder", "reviewer", channel="review")
```

一次连接把两张原本独立的图接到一起时，Terrarium 引擎会合并它们，
Studio 看到的就是一个会话。断开导致图拆分时，引擎把父会话历史复制
进每个子存储。

需要更底层的引擎访问时，直接用 `studio.engine`：

```python
async for ev in studio.engine.subscribe():
    print(ev.kind, ev.creature_id, ev.payload)
```

## 目录

目录辅助方法是 CLI 和 HTTP 共用的读取 / 管理操作。

```python
packages = studio.catalog.packages.list()
remote = studio.catalog.packages.remote()
scanned = studio.catalog.packages.scan()

pkg_name = studio.catalog.packages.install(
    "https://github.com/Kohaku-Lab/kt-biome.git"
)
studio.catalog.packages.update(pkg_name)
```

内置组件与 schema：

```python
tools = studio.catalog.builtins.list("tools")
bash_info = studio.catalog.builtins.info("bash")
schema = studio.catalog.introspect.builtin_schema("tool")
```

需要工作区的目录调用接受编辑器层的工作区对象（比如 API 打开的本地
工作区）：

```python
creatures = studio.catalog.creatures.list(workspace)
modules = studio.catalog.modules.list(workspace, "tools")
```

## 身份

身份命名空间汇总 LLM profile / backend、API key、Codex OAuth、MCP
服务器与 UI 偏好。

```python
for backend in studio.identity.llm.list_backends():
    print(backend["name"], backend["backend_type"])

print("default:", studio.identity.llm.get_default())
studio.identity.llm.set_default("openai/gpt-4.1-mini")

profiles = studio.identity.llm.list_profiles()
models = studio.identity.llm.list_models()
```

API key：

```python
studio.identity.keys.set("openai", "sk-...")
print(studio.identity.keys.list())
studio.identity.keys.delete("openai")
```

MCP 注册表：

```python
studio.identity.mcp.upsert({
    "name": "sqlite",
    "transport": "stdio",
    "command": "mcp-server-sqlite",
    "args": ["/tmp/app.db"],
})
print(studio.identity.mcp.list())
```

## 已保存会话的持久化

列出已保存的会话：

```python
for saved in studio.persistence.list():
    print(saved["name"], saved.get("status"))
```

解析并查看一个已保存的会话：

```python
path = studio.persistence.resolve_path("alice")
index = studio.persistence.history_index(path)
root_history = studio.persistence.history(path, "root")
```

恢复进活动引擎：

```python
session = await studio.persistence.resume("alice")
```

删除一个已保存会话的所有版本：

```python
deleted_paths = studio.persistence.delete("alice")
```

查看器辅助方法构建 Web 会话查看器用的载荷：

```python
from kohakuterrarium.session.store import SessionStore

store = SessionStore(path)
try:
    tree = studio.persistence.viewer.tree(store, "alice")
    summary = studio.persistence.viewer.summary(store)
finally:
    store.close()
```

## 挂载策略

询问某个生物或会话适合哪些挂载模式：

```python
policies = studio.attach.policies_for_creature(cid)
session_policies = studio.attach.policies_for_session(sid)
```

当前的门面只提供策略通告。具体的实时流由 HTTP/WebSocket 适配器使用
（`/ws/sessions/...`、`/ws/logs`、`/ws/files/...`、
`/ws/sessions/.../pty`）。编程式流辅助方法可以加到 `studio.attach`
下，无需改动 `Terrarium`。

## 编辑器

编辑器命名空间面向工作区文件和脚手架。它是 Web Studio 编辑器之下的
Python 层。

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

模块辅助方法对应自定义模块编辑器的流程：

```python
studio.editors.modules.scaffold(workspace, "tools", "my_tool")
studio.editors.modules.save_doc(workspace, "tools", "my_tool", "# My tool")
```

## 错误是类型化的 Python 异常

Studio 是纯 Python，它从不抛 HTTP 错误。失败以
`kohakuterrarium.errors` 层级浮出，嵌入方代码可以捕获真正的异常
类型：

```python
from kohakuterrarium import errors

try:
    await studio.persistence.resume("no-such-session")
except errors.NotFoundError as e:
    print("nothing to resume:", e)
except errors.KTError as e:
    print("studio operation failed:", e)
```

HTTP 层（`api/`）在一个适配器里完成转换：`NotFoundError`
→ 404、`ConflictError` → 409、`InvalidRequestError`/`ValueError` →
400、其他 `KTError` → 500。在 Studio 上自建传输层的话，请在你的
边界做同样的映射。

两个 Studio 实例彼此独立：会话注册表挂在实例上（锚定到它自己的
引擎），所以一个进程嵌入多个 studio（或者多用户服务器里每请求
一个）不会互相污染。

## Studio 与 Terrarium 怎么选

只需要运行时机制时用 `Terrarium`：

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")
    await engine.connect(a, b, channel="handoff")
```

还需要管理面的事情时用 `Studio`：

```python
async with Studio() as studio:
    print(studio.catalog.packages.list())
    session = await studio.sessions.start_creature("@kt-biome/creatures/general")
    await studio.persistence.resume("older-session")
```

需要降到原始运行时操作时，`Studio.engine` 随时可用。

## 常见误区

- **把 Studio 当成 Agent 用。**Studio 没有 LLM。它管理会话；跑 LLM
  控制器的是引擎里的生物。
- **忘了会话作用域。**生物级操作同时需要 `session_id` 和
  `creature_id`。
- **脚本里一直开着 Studio 不关。**用 `async with Studio()`，或调用
  `await studio.shutdown()`。
- **在 UI 里重新实现设置/包/会话逻辑。**调用 Studio 或委托给 Studio
  的 HTTP 路由；不要复制那些策略。

## 另请参阅

- [编程式用法](programmatic-usage.md)：完整的 Python 嵌入指南。
- [Terrarium](terrariums.md)：运行时拓扑与配方。
- [会话](sessions.md)：已保存的 `.kohakutr` 文件与恢复。
- [Python API](../reference/python.md)：方法参考。
