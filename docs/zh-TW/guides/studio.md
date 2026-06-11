---
title: Studio
summary: 用 Studio 類別管理目錄、身份、活動工作階段、已儲存的工作階段、attach 政策與編輯器流程。
tags:
  - guides
  - studio
  - python
  - embedding
---

# Studio 使用指南

寫給想把 KohakuTerrarium 嵌進 Python 服務、自動化腳本或自製
dashboard 的讀者。

`Studio` 是 `Terrarium` 執行期引擎之上的管理門面。
它包住一個引擎，把 CLI 指令和 HTTP 路由共用的操作整理成幾組：
目錄、身份、工作階段、持久化、attach 政策與編輯器。

概念入門：[Studio](../concepts/studio.md)、[Terrarium](../concepts/multi-agent/terrarium.md)。精確的方法名稱在 [Python API](../reference/python.md)。

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
            "Explain what KohakuTerrarium is in one paragraph.",
        )
        async for chunk in stream:
            print(chunk, end="", flush=True)

asyncio.run(main())
```

腳本請用 `async with Studio()`。它會啟動並持有一個 `Terrarium`
引擎，離開時關閉。已經有引擎的話，直接傳進去：

```python
from kohakuterrarium import Studio, Terrarium

engine = Terrarium()
studio = Studio(engine=engine)
```

## 建構模式

### 空的 Studio

```python
async with Studio() as studio:
    print(studio.sessions.list())
```

這會建一個空引擎。用 `studio.sessions` 加入工作階段。

### 單一生物

```python
studio = await Studio.with_creature("@kt-biome/creatures/general")
try:
    sessions = studio.sessions.list()
    print(sessions[0].session_id)
finally:
    await studio.shutdown()
```

`with_creature()` 適合簡單的嵌入。它回傳一個
`Studio`；建立的工作階段用 `studio.sessions.list()` 拿。

### Terrarium 配方

```python
studio = await Studio.from_recipe("@kt-biome/terrariums/swe_team")
try:
    session = studio.sessions.list()[0]
    print(session.kind, session.creatures)
finally:
    await studio.shutdown()
```

配方會建立一張圖 / 一個工作階段，內含 terrarium 設定宣告的所有
生物。這個工作階段是完整註冊的：有自己的 session store、會以
名稱出現在 `studio.sessions.list()`、之後也可以恢復，
跟 `start_terrarium` 走同一條路。

### 恢復已儲存的工作階段

```python
async with await Studio.resume("~/.kohakuterrarium/sessions/alice.kohakutr") as studio:
    print(studio.sessions.list())
```

已經建好的 Studio，改用 persistence 命名空間：

```python
async with Studio() as studio:
    session = await studio.persistence.resume("alice")
    print(session.session_id)
```

恢復輔助函式接受完整路徑，或可以從預設 session 目錄解析出來的
已儲存工作階段名稱。

## 活動工作階段

Studio 把一張活著的 `Terrarium` 圖叫做一個**工作階段 (session)**。
單生物圖是 creature session；配方圖是 terrarium session。

```python
async with Studio() as studio:
    session = await studio.sessions.start_creature(
        "@kt-biome/creatures/general",
        pwd="/tmp/my-project",
        llm="openai/gpt-4.1-mini",     # profile / preset / 選擇器
        name="scratch-helper",         # 顯示名稱覆寫
    )

    print(session.session_id)
    print(session.kind)        # "creature"
    print(session.creatures)   # 生物摘要 dict 的列表

    await studio.sessions.stop(session.session_id)
```

(多節點 lab 部署下，`start_creature(..., on_node="worker-a")`
可以把生物放到特定 worker 上；預設的 `"_host"` 在本地跑。)

啟動多生物配方：

```python
session = await studio.sessions.start_terrarium(
    "@kt-biome/terrariums/swe_team",
    pwd="/tmp/my-project",
    llm="openai/gpt-4.1-mini",
)
```

列表與檢視：

```python
for item in studio.sessions.list():
    print(item.session_id, item.kind, item.name)

handle = studio.sessions.get(session.session_id)
```

在工作階段裡找一隻生物：

```python
creature = studio.sessions.find_creature(session.session_id, "swe")
print(creature.agent.config.name)
```

## 聊天與生物範圍的操作

生物層級的操作以 `(session_id, creature_id)` 定位。

```python
sid = session.session_id
cid = session.creatures[0]["creature_id"]

stream = await studio.sessions.chat.chat(sid, cid, "Hello")
async for chunk in stream:
    print(chunk, end="")

history = studio.sessions.chat.history(sid, cid)
branches = studio.sessions.chat.branches(sid, cid)
```

重新生成、編輯、倒帶：支援分支的關鍵字參數
(`turn_index=`、`user_position=`、`branch_view=`) 跟網頁檢視器
送的一樣，所以腳本也能鎖定一段編輯過對話的特定分支：

```python
await studio.sessions.chat.regenerate(sid, cid)
await studio.sessions.chat.regenerate(sid, cid, turn_index=3)
await studio.sessions.chat.edit_message(sid, cid, msg_idx=4, content="better prompt")
await studio.sessions.chat.rewind(sid, cid, msg_idx=2)
```

控制 job 與中斷：

```python
await studio.sessions.ctl.interrupt(sid, cid)
jobs = studio.sessions.ctl.list_jobs(sid, cid)
await studio.sessions.ctl.cancel_job(sid, cid, jobs[0]["job_id"])
```

狀態檢視：

```python
scratchpad = studio.sessions.state.scratchpad(sid, cid)
studio.sessions.state.patch_scratchpad(sid, cid, {"phase": "review"})
print(studio.sessions.state.env(sid, cid))
print(studio.sessions.state.working_dir(sid, cid))
print(studio.sessions.state.system_prompt(sid, cid)["text"])
```

外掛、切換模型、slash 指令：

```python
plugins = studio.sessions.plugins.list(sid, cid)
await studio.sessions.plugins.toggle(sid, cid, "my_plugin")

studio.sessions.model.switch(sid, cid, "openai/gpt-4.1")
options = studio.sessions.model.native_tool_options(sid, cid)
await studio.sessions.command.execute(sid, cid, "status")
```

## 拓樸管理

Studio 在底層引擎之上提供以工作階段為範圍的拓樸輔助方法。

```python
await studio.sessions.add_channel(session.session_id, "review")
await studio.sessions.connect("coder", "reviewer", channel="review")
await studio.sessions.disconnect("coder", "reviewer", channel="review")
```

當一條連接把兩張原本分開的圖接起來，Terrarium 引擎會合併它們，
Studio 看到的就是一個工作階段。當斷開造成分割，引擎會把
parent 的工作階段歷史複製進每個 child store。

需要更低階的引擎存取時，直接用 `studio.engine`：

```python
async for ev in studio.engine.subscribe():
    print(ev.kind, ev.creature_id, ev.payload)
```

## 目錄

目錄輔助方法是 CLI 和 HTTP 共用的讀取 / 管理操作。

```python
packages = studio.catalog.packages.list()
remote = studio.catalog.packages.remote()
scanned = studio.catalog.packages.scan()

pkg_name = studio.catalog.packages.install(
    "https://github.com/Kohaku-Lab/kt-biome.git"
)
studio.catalog.packages.update(pkg_name)
```

內建模組與 schema：

```python
tools = studio.catalog.builtins.list("tools")
bash_info = studio.catalog.builtins.info("bash")
schema = studio.catalog.introspect.builtin_schema("tool")
```

需要 workspace 的目錄呼叫從編輯器層拿一個 workspace 物件
(例如 API 開啟的本地 workspace)：

```python
creatures = studio.catalog.creatures.list(workspace)
modules = studio.catalog.modules.list(workspace, "tools")
```

## 身份

身份這組涵蓋 LLM profile / backend、API 金鑰、Codex OAuth、MCP
伺服器與 UI 偏好。

```python
for backend in studio.identity.llm.list_backends():
    print(backend["name"], backend["backend_type"])

print("default:", studio.identity.llm.get_default())
studio.identity.llm.set_default("openai/gpt-4.1-mini")

profiles = studio.identity.llm.list_profiles()
models = studio.identity.llm.list_models()
```

API 金鑰：

```python
studio.identity.keys.set("openai", "sk-...")
print(studio.identity.keys.list())
studio.identity.keys.delete("openai")
```

MCP 註冊表：

```python
studio.identity.mcp.upsert({
    "name": "sqlite",
    "transport": "stdio",
    "command": "mcp-server-sqlite",
    "args": ["/tmp/app.db"],
})
print(studio.identity.mcp.list())
```

## 已儲存工作階段的持久化

列出已儲存的工作階段：

```python
for saved in studio.persistence.list():
    print(saved["name"], saved.get("status"))
```

解析並檢視一個已儲存的工作階段：

```python
path = studio.persistence.resolve_path("alice")
index = studio.persistence.history_index(path)
root_history = studio.persistence.history(path, "root")
```

恢復進活著的引擎：

```python
session = await studio.persistence.resume("alice")
```

刪除一個已儲存工作階段的所有版本：

```python
deleted_paths = studio.persistence.delete("alice")
```

Viewer 輔助方法產生網頁工作階段檢視器用的 payload：

```python
from kohakuterrarium.session.store import SessionStore

store = SessionStore(path)
try:
    tree = studio.persistence.viewer.tree(store, "alice")
    summary = studio.persistence.viewer.summary(store)
finally:
    store.close()
```

## Attach 政策

詢問哪些 attach 模式適用於某隻生物或某個工作階段：

```python
policies = studio.attach.policies_for_creature(cid)
session_policies = studio.attach.policies_for_session(sid)
```

目前的門面提供的是政策宣告。具體的即時串流由 HTTP / WebSocket
adapter 使用 (`/ws/sessions/...`、`/ws/logs`、`/ws/files/...`、
`/ws/sessions/.../pty`)。程式化的串流輔助方法可以加在
`studio.attach` 底下，而不需要改動 `Terrarium`。

## 編輯器

編輯器命名空間負責 workspace 檔案與 scaffolding。它是網頁版
Studio 編輯器底下的 Python 層。

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

模組輔助方法對應自訂模組的編輯流程：

```python
studio.editors.modules.scaffold(workspace, "tools", "my_tool")
studio.editors.modules.save_doc(workspace, "tools", "my_tool", "# My tool")
```

## 錯誤是型別化的 Python 例外

Studio 是純 Python，它永遠不拋 HTTP 錯誤。失敗以
`kohakuterrarium.errors` 階層浮現，嵌入端的程式碼接的是
真正的例外型別：

```python
from kohakuterrarium import errors

try:
    await studio.persistence.resume("no-such-session")
except errors.NotFoundError as e:
    print("nothing to resume:", e)
except errors.KTError as e:
    print("studio operation failed:", e)
```

HTTP 層 (`api/`) 用一個 adapter 統一轉換：`NotFoundError`
→ 404、`ConflictError` → 409、`InvalidRequestError` / `ValueError` → 400、
其他 `KTError` → 500。在 Studio 上自建傳輸層的話，
在你的邊界做同樣的對應。

兩個 Studio 實例彼此獨立：session 註冊表掛在實例上
(錨定在它的引擎)，所以在一個行程裡嵌多個 studio（或在多使用者
伺服器上一個請求一個）不會互相污染。

## Studio vs Terrarium

只需要執行期機制時用 `Terrarium`：

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")
    await engine.connect(a, b, channel="handoff")
```

連管理面的事情也需要時用 `Studio`：

```python
async with Studio() as studio:
    print(studio.catalog.packages.list())
    session = await studio.sessions.start_creature("@kt-biome/creatures/general")
    await studio.persistence.resume("older-session")
```

需要降到原始執行期操作時，`Studio.engine` 隨時可用。

## 常見陷阱

- **把 Studio 當 agent 用。** Studio 沒有 LLM。它管理工作階段；
  跑 LLM 控制器的是引擎裡的生物。
- **忘了工作階段範圍。** 生物層級的操作同時需要
  `session_id` 和 `creature_id`。
- **腳本裡讓 Studio 一直開著。** 用 `async with Studio()`
  或呼叫 `await studio.shutdown()`。
- **在 UI 裡重新實作設定 / 套件 / 工作階段邏輯。** 呼叫
  Studio 或委派給 Studio 的 HTTP 路由；不要複製那些政策。

## 另見

- [程式化使用](programmatic-usage.md)：完整的 Python 嵌入指南。
- [生態瓶](terrariums.md)：執行期拓樸與配方。
- [工作階段](sessions.md)：已儲存的 `.kohakutr` 檔與恢復。
- [Python API](../reference/python.md)：方法參考。
