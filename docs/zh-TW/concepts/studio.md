---
title: Studio
summary: Terrarium 引擎之上的管理層：catalog、identity、sessions、persistence、attach policy 與 editors。
tags:
  - concepts
  - studio
  - architecture
---

# Studio

## 它是什麼

**Studio** 是 `Terrarium` runtime engine 之上的管理層。它不是 UI，也不是另一個 agent。它是一個 Python facade，讓 CLI、HTTP API、web dashboard 與你自己的程式碼共用同一套管理邏輯。

Studio 負責：

- 套件與內建項目的 **catalog** 查詢；
- LLM profile、API key、MCP、UI preferences 等 **identity** 狀態；
- engine-backed **active session** lifecycle；
- 保存的 `.kohakutr` **persistence**：list、resume、fork、history、export；
- live **attach policy**：IO chat、channel observer、trace、logs、workspace files、pty；
- Studio **editors**：workspace 內的 creature / module CRUD 與 scaffold。

Python facade 是 `kohakuterrarium.Studio`。HTTP API、web UI、`kt` commands 與自訂 embedding code 都應該呼叫 Studio，而不是各自重寫 package/session/settings policy。

## 分層模型

| Facade | Layer | 負責 |
|---|---|---|
| `Agent` / creature internals | Creature | 單一 LLM controller，以及 tools、triggers、sub-agents、plugins、memory、I/O。 |
| `Terrarium` | Runtime engine | live creatures、graph topology、channels、output wiring、hot-plug、engine events。 |
| `Studio` | Management layer | catalog、identity、active sessions、saved sessions、attach policies、editor workflows。 |

低層不 import 高層：

- Creature code 不知道 `Terrarium` 或 `Studio` 存在。
- `Terrarium` host creatures，但不需要知道 Studio、HTTP 或 CLI。
- `Studio` 接收一個 `Terrarium` engine，並在其上提供管理語意。
- `api/`、`cli/`、frontend 是 Studio 的 adapter。

這是 `plans/structure-hierarchy/` 的實作方向：一個 runtime engine、一個 management layer，以及薄 UI adapter。

## 為什麼需要 Studio

沒有 Studio 時，很多政策會在不同地方重複：

- package listing 同時出現在 CLI 與 web route；
- profile/key/MCP logic 分散在 `kt config`、`kt model`、`kt login` 與 `/api/settings`；
- active agent / terrarium route 重複 lifecycle logic；
- saved-session viewer/export/diff/resume 與 runtime session creation 分開；
- WebSocket chat/log/file/terminal endpoints 各自實作 attach policy。

Studio 把這些整理成每個 concern 一個 implementation。CLI 負責輸出終端格式；HTTP API 負責 JSON；frontend 負責 panel；實際工作交給 Studio。

## Session 與 graph

`Terrarium` 擁有 **graphs**：live creatures 的 connected components。單一 creature 是一個 graph；multi-creature team 也是一個 graph。連接兩個 graph 會 merge；移除連線可能 split。

Studio 在使用者或 UI 管理某個 graph 時，把它稱為 **session**。這個 session handle 包含：

- `session_id` — graph id；
- `kind` — `"creature"` 或 `"terrarium"`；
- creature summaries，用於 UI tabs 與 per-creature 操作；
- config path、working directory、creation time 等 Studio metadata。

保存的 session 是磁碟上的 `.kohakutr` 檔。Studio persistence 可以 list、resume、fork、產生 viewer payload，或刪除它們。

## Attach policy

不是每個 creature 都是聊天機器人。Monitor 可能沒有 user input；scheduler 可能只輸出 logs；multi-agent team 可能需要 channel observer 而不是 chat box。Studio 把「執行 creature」與「把 UI attach 到它」分開。

Attach policy 回答：「對這個 running creature/session，哪些 live view/control surface 合理？」

| Policy | 形狀 | 用途 |
|---|---|---|
| IO chat | read/write stream | 對話型 creature。 |
| Channel observer | read-only stream | 不消耗 queue message 的 channel traffic 觀察。 |
| Trace | read-only stream | Engine events、turns、topology changes、tool activity。 |
| Log | read-only stream | Process/runtime logs。 |
| Workspace files | browse/watch | File panel 與 editor refresh。 |
| PTY | read/write terminal | 附著到 creature working directory 的 shell。 |

## Studio 不是 web dashboard

Web dashboard 是 UI；Studio 是 dashboard 呼叫的 Python management layer。你可以完全不啟動 web server：

```python
from kohakuterrarium import Studio

async with Studio() as studio:
    session = await studio.sessions.start_creature("@kt-biome/creatures/general")
    print(session.session_id)
```

也可以啟動 web dashboard；它只是把 FastAPI routes 與 WebSocket endpoints 接到相同的 Studio/Terrarium model：

```bash
kt web
```

## 何時使用哪一層

- 用 **`Agent`** 直接控制一個 creature 的 modules、event queue、output handlers 或測試 harness。
- 用 **`Terrarium`** 處理 runtime topology：add creatures、connect channels、hot-plug、observe engine events。
- 用 **`Studio`** 建 UI、service 或 automation：packages、settings、active sessions、saved sessions、attach policies、editors。

## 參見

- [Terrarium](multi-agent/terrarium.md)
- [程式化使用](../guides/programmatic-usage.md)
- [Studio 使用指南](../guides/studio.md)
- [Python API](../reference/python.md)
