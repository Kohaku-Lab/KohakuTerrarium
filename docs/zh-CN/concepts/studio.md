---
title: Studio
summary: Terrarium 引擎之上的管理层：catalog、identity、sessions、persistence、attach policy 与 editors。
tags:
  - concepts
  - studio
  - architecture
---

# Studio

## 它是什么

**Studio** 是 `Terrarium` runtime engine 之上的管理层。它不是 UI，也不是另一个 agent。它是一个 Python facade，让 CLI、HTTP API、web dashboard 和你自己的代码共用同一套管理逻辑。

Studio 负责：

- 包与内置项的 **catalog** 查询；
- LLM profile、API key、MCP、UI preferences 等 **identity** 状态；
- engine-backed **active session** lifecycle；
- 保存的 `.kohakutr` **persistence**：list、resume、fork、history、export；
- live **attach policy**：IO chat、channel observer、trace、logs、workspace files、pty；
- Studio **editors**：workspace 内的 Creature / module CRUD 与 scaffold。

Python facade 是 `kohakuterrarium.Studio`。HTTP API、web UI、`kt` commands 与自定义 embedding code 都应该调用 Studio，而不是各自重写 package/session/settings policy。

## 分层模型

| Facade | Layer | 负责 |
|---|---|---|
| `Agent` / creature internals | Creature | 单一 LLM controller，以及 tools、triggers、sub-agents、plugins、memory、I/O。 |
| `Terrarium` | Runtime engine | live creatures、graph topology、channels、output wiring、hot-plug、engine events。 |
| `Studio` | Management layer | catalog、identity、active sessions、saved sessions、attach policies、editor workflows。 |

低层不 import 高层：

- Creature code 不知道 `Terrarium` 或 `Studio` 存在。
- `Terrarium` host creatures，但不需要知道 Studio、HTTP 或 CLI。
- `Studio` 接收一个 `Terrarium` engine，并在其上提供管理语义。
- `api/`、`cli/`、frontend 是 Studio 的 adapter。

这是 `plans/structure-hierarchy/` 的实现方向：一个 runtime engine、一个 management layer，以及薄 UI adapter。

## 为什么需要 Studio

没有 Studio 时，很多策略会在不同地方重复：

- package listing 同时出现在 CLI 与 web route；
- profile/key/MCP logic 分散在 `kt config`、`kt model`、`kt login` 与 `/api/settings`；
- active agent / terrarium route 重复 lifecycle logic；
- saved-session viewer/export/diff/resume 与 runtime session creation 分开；
- WebSocket chat/log/file/terminal endpoints 各自实现 attach policy。

Studio 把这些整理成每个 concern 一个 implementation。CLI 负责输出终端格式；HTTP API 负责 JSON；frontend 负责 panel；实际工作交给 Studio。

## Session 与 graph

`Terrarium` 拥有 **graphs**：live creatures 的 connected components。单一 Creature 是一个 graph；multi-creature team 也是一个 graph。连接两个 graph 会 merge；移除连接可能 split。

Studio 在用户或 UI 管理某个 graph 时，把它称为 **session**。这个 session handle 包含：

- `session_id` — graph id；
- `kind` — `"creature"` 或 `"terrarium"`；
- creature summaries，用于 UI tabs 与 per-creature 操作；
- config path、working directory、creation time 等 Studio metadata。

保存的 session 是磁盘上的 `.kohakutr` 文件。Studio persistence 可以 list、resume、fork、生成 viewer payload，或删除它们。

## Attach policy

不是每个 Creature 都是聊天机器人。Monitor 可能没有 user input；scheduler 可能只输出 logs；multi-agent team 可能需要 channel observer 而不是 chat box。Studio 把“运行 Creature”与“把 UI attach 到它”分开。

Attach policy 回答：“对这个 running creature/session，哪些 live view/control surface 合理？”

| Policy | 形状 | 用途 |
|---|---|---|
| IO chat | read/write stream | 对话型 Creature。 |
| Channel observer | read-only stream | 不消耗 queue message 的 channel traffic 观察。 |
| Trace | read-only stream | Engine events、turns、topology changes、tool activity。 |
| Log | read-only stream | Process/runtime logs。 |
| Workspace files | browse/watch | File panel 与 editor refresh。 |
| PTY | read/write terminal | 附着到 Creature working directory 的 shell。 |

## Studio 不是 web dashboard

Web dashboard 是 UI；Studio 是 dashboard 调用的 Python management layer。你可以完全不启动 web server：

```python
from kohakuterrarium import Studio

async with Studio() as studio:
    session = await studio.sessions.start_creature("@kt-biome/creatures/general")
    print(session.session_id)
```

也可以启动 web dashboard；它只是把 FastAPI routes 与 WebSocket endpoints 接到相同的 Studio/Terrarium model：

```bash
kt web
```

## 何时使用哪一层

- 用 **`Agent`** 直接控制一个 Creature 的 modules、event queue、output handlers 或测试 harness。
- 用 **`Terrarium`** 处理 runtime topology：add creatures、connect channels、hot-plug、observe engine events。
- 用 **`Studio`** 建 UI、service 或 automation：packages、settings、active sessions、saved sessions、attach policies、editors。

## 参见

- [Terrarium](multi-agent/terrarium.md)
- [程序化使用](../guides/programmatic-usage.md)
- [Studio 使用指南](../guides/studio.md)
- [Python API](../reference/python.md)
