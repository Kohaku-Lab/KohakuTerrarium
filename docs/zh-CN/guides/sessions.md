---
title: 会话与恢复
summary: .kohakutr 会话文件如何工作、如何恢复一个生物，以及如何重放对话历史。
tags:
  - guides
  - session
  - persistence
---

# 会话

写给需要持久化、恢复或归档 Agent 运行的读者。

会话把一次运行的全部操作状态（对话、事件、子代理对话、频道历史、
便笺、任务、可恢复触发器、配置元数据）捕获成一个 `.kohakutr` 文件。
你可以在任意时刻停下一个生物 (creature)，之后从停下的地方原样恢复。

概念入门：[记忆与压缩](../concepts/modules/memory-and-compaction.md)、
[会话与环境](../concepts/modules/session-and-environment.md)。

### 压缩后的编辑与重新生成

压缩会改变实时提示并写入快速恢复快照，但不会删除追加式事件日志。Studio 使用持久化的 `event_id`、`turn_index` 和 `branch_id` 定位可编辑用户消息。保存并重新运行或重新生成时，新分支从所选消息之前的原始事件前缀重建，并忽略压缩摘要和快照；旧分支及其后续事件仍可切换和恢复。定位缺失、歧义、指向回合中注入输入或与所选分支冲突时，操作会在不修改历史的情况下失败。

## `.kohakutr` 文件

`.kohakutr` 是一个 SQLite 数据库（经 KohakuVault），有九张表：

| 表 | 用途 |
|---|---|
| `meta` | 会话元数据、配置快照、terrarium 拓扑 |
| `state` | 每个 Agent 的便笺、轮次计数、累计 token 用量、可恢复触发器 |
| `events` | 追加式日志：每个文本块、工具调用、触发、token 用量事件 |
| `channels` | 按频道名索引的频道消息历史 |
| `subagents` | 按父 Agent + 名称 + 运行编号索引的子代理对话快照 |
| `jobs` | 工具与子代理的任务记录 |
| `conversation` | 每个 Agent 最新的对话快照（用于快速恢复） |
| `fts` | 事件上的 FTS5 索引（供 `kt search`） |
| `vectors` | 可选的嵌入列（由 `kt embedding` 填充） |

事件数据是追加式的，版本管理走 KohakuVault 的自动打包。二进制产物
还可以放在同级的 `<session>.artifacts/` 目录里：一次运行生成过图片
或其他二进制输出时，请把 `.kohakutr` 文件和它的 artifacts 目录一起
归档。

## 会话放在哪

```
~/.kohakuterrarium/sessions/<name>.kohakutr
```

`<name>` 由生物/terrarium 名加时间戳自动生成。用 `--session <path>`
覆盖，或用 `--no-session` 关闭。

## 持久化哪些东西

每个轮次 KohakuTerrarium 都会记录：

- **对话快照**：经 msgpack 的原始消息 dict。保留 `tool_calls`、
  多模态内容和元数据。
- **事件日志**：每个文本块、工具调用、子代理输出、触发器触发、
  频道消息、压缩、中断或错误各一条。这是权威历史。
- **子代理对话**：在子代理销毁之前保存，事后可以检查它干了什么。
- **便笺与频道消息**：按 Agent、按频道各自记录。
- **任务记录**：长时间运行的工具与子代理的输出。
- **可恢复触发器**：任何 `resumable: True` 的 `BaseTrigger` 子类
  序列化进 `state`，恢复时还原。
- **配置快照**：运行时完全解析后的配置，磁盘上的配置变了也能
  重建 Agent。
- **二进制产物**：生成的图片等二进制输出写在会话文件旁的
  `<session>.artifacts/` 下。

## 恢复

```bash
kt resume --last            # most recent session
kt resume                   # interactive picker (10 most-recent shown)
kt resume my-agent_20240101 # by name prefix
kt resume ~/backup/run.kohakutr
```

恢复类型自动检测：Agent 会话挂载单个生物；terrarium 会话挂载完整
连线并强制 TUI 模式。

参数与 `kt run` 相同：`--mode`、`--llm`、`--log-level`，外加
`--pwd <dir>` 覆盖工作目录。

编程式恢复与之对应（见[在 Python 里使用会话](#在-python-里使用会话)）：

```python
from kohakuterrarium import Terrarium

# Fresh engine from a saved session...
engine = await Terrarium.resume("runs/swe_20240101.kohakutr", pwd="/work", llm=None)

# ...or adopt into an engine that's already running other graphs.
graph_id = await engine.adopt_session("runs/other.kohakutr")
```

两者都接受路径或 `SessionStore`；`llm=` 是可选的选择器字符串覆盖。
文件存在但无法恢复（已保存会话类型未知、元数据缺少配置路径）会抛
`ValueError`。

恢复做了什么：

1. 从 `meta` 读配置快照。
2. 重新加载磁盘上的当前配置（你后来改的提示词/工具会生效）。
3. 合并：配置快照提供会话身份；当前配置提供运行逻辑。
4. 重建 Agent，挂上同一个 `SessionStore`，重注入对话快照，
   回放便笺/频道/触发器状态。
5. 控制器从头启动；之前的事件已在上下文中。

也就是说，小的配置漂移没问题（换个 LLM、改个提示词）。结构性漂移
（重命名生物、删掉它正在用的工具）可能导致回放错误；需要完美保真
时，把会话钉死在它原来的配置上。

## Web UI 中的开放对话

Conversations Rail 只显示当前仍附加 runtime 的对话。持久化对话的生命周期仍是独立维度：

- **在线且开放：** runtime 已附加，因此对话显示在 Rail 中。
- **休眠且开放：** 当前没有附加 runtime。保存的对话仍可从 Sessions 访问，但不会渲染在 Rail 中。
- **已结束：** 用户显式结束对话。保存的历史仍可在 Sessions 中查看。

关闭 Chat 或 Inspector 标签页只会 detach 该视图，不会停止或结束对话，因此仍在线的 runtime 会保留在 Rail 中。后端将已停止或断线的 Creature 报告为非活跃后，对应行会在下一次刷新时消失。Sessions 历史页继续保持只读，并沿用现有的 **View** 与 **Resume** 操作。

每个新持久化的对话都有稳定的 `conversation_id`。runtime graph ID 在重启或恢复后可以变化，文件也可以移动或重命名，但这些变化不会产生重复记录。`GET /api/sessions/open` 使用该 identity 聚合在线 runtime 与带开放 marker 的已保存 session，并让在线行优先；Rail 只渲染 `is_live: true` 的行。Session index 与查询按已认证用户的 session directory 隔离，因此一个用户的索引不会返回另一个用户的数据。

保存的生命周期 marker 是显式的。引入 marker 之前创建的旧 session 仍可在历史中访问。普通 runtime Stop 保留开放 marker，并记录 paused/dormant 状态；显式 **End** 清除 marker 并记录 terminal 状态。本地、远程与 cluster 操作都会在 Rail 刷新前同步保存文件中的 marker。

Resume 按保存对话执行 singleflight：两个浏览器同时请求时只执行一次服务端恢复；取消一个等待者不会取消共享恢复，失败后仍可重试。Cluster resume 采用 all-or-error 语义：任何成员恢复失败或保存的连接无法重建时，服务端会补偿已恢复成员、清理 partial runtime metadata、保留原始保存生命周期，并返回非成功响应，而不是留下 degraded partial cluster。

## 中断与恢复工作流

```bash
kt run @kt-biome/creatures/swe
# work... then Ctrl+C twice while idle (or Ctrl+D / /exit)
# later:
kt resume --last
```

在 Rich CLI 模式下，Ctrl+C 中断当前轮次；空闲时按两次 Ctrl+C
（或 Ctrl+D / `/exit`）会优雅退出、落盘会话存储并打印恢复提示。
强制杀死（SIGKILL）会跳过最后的落盘，但得益于追加式写入，最近的
状态大多已经在磁盘上了。

## 复制或归档会话

```bash
# Backup
cp ~/.kohakuterrarium/sessions/swe_20240101.kohakutr ~/backups/
cp -r ~/.kohakuterrarium/sessions/swe_20240101.artifacts ~/backups/   # if present

# Resume from a moved location
kt resume ~/backups/swe_20240101.kohakutr
```

不想恢复、只想看看，就用 `SessionReader`（见下）：它以只读方式
打开文件，检视永远不会碰 `status` 或 `last_active`。

## 在 Python 里使用会话

### 创建会话：引擎持有的持久化

持久化是引擎上的一个关键字参数；会话元数据由框架自己写入并校验：

```python
from kohakuterrarium import Terrarium

# Autosession: every graph gets <session_dir>/<graph_id>.kohakutr
# automatically (merge/split children land there too).
engine = Terrarium(session_dir="runs/")

# Per-creature control via session=:
c = await engine.add_creature(
    "@kt-biome/creatures/general",
    session="runs/student-42.kohakutr",   # mint the store at this exact path
)
# session=True   -> mint in the default session dir
# session=False  -> no persistence, even under autosession
# session=<SessionStore> -> attach an existing store as-is
# session=None (default) -> follow the engine (autosession / graph store / off)

# Recipes: one terrarium-typed store for the whole graph.
await engine.apply_recipe("@kt-biome/terrariums/swe_team", session="runs/team.kohakutr")
```

`await engine.shutdown()`（或离开 `async with` 块）会关闭引擎创建的
所有存储，文件不再卡在 `status: "running"`。

### 恢复

- `await Terrarium.resume(store_or_path, *, pwd=None, llm=None)`：
  起一个新引擎并接管已保存的会话。
- `await engine.adopt_session(store_or_path, *, pwd=None, llm=None)`：
  恢复进运行中的引擎；返回新的 `graph_id`。

恢复根据会话元数据里记录的配置路径（包括 `@pkg` 引用）重建拓扑，
并使用每个 Agent 各自的工作目录，不会对你的进程 `os.chdir`。
恢复会在创建 writer store、runtime、lifecycle 或 adoption 之前执行只读
工作目录预检。目录失效时必须选择新目录、只看历史或取消，不会静默回退到
KohakuTerrarium 进程目录。使用 `workspace_overrides={"creature-id":
"/new/path"}` 只替换确认的成员；共享失效路径也可以使用预检返回的
`gap_id` 成组替换。标量 `pwd=` 仅保留为显式全队兼容覆盖，不能与
`workspace_overrides` 同时使用。成功替换会永久写回 manifest；远程和集群
会在实际 worker 上校验路径，并在第一次 adoption 前完成全成员预检。集群
API 还可按成员会话 ID 限定替换，让不同 worker 上相同的 creature 或路径组
目标选择不同目录。若回滚无法持久化，会话会标记为 `partial_dirty`；后续预检
和恢复会失败关闭，直到会话修复。

### 读取：`SessionReader`

`SessionReader` 是 `.kohakutr` 文件的只读检视接口。它经
`SessionStore.open_readonly` 打开，读取绝不会更新 `last_active` 或
改写 `status`：

```python
from kohakuterrarium import SessionReader

with SessionReader("~/backups/swe_20240101.kohakutr") as r:
    print(r.meta["status"], r.agents)

    for turn in r.turns():               # live-branch turns, reassembled
        tools = [tc["name"] for tc in turn.tool_calls]
        print(f"[{turn.source}] {turn.user_text!r} -> "
              f"{turn.assistant_text[:60]!r} tools={tools}")

    events = r.events()                  # the raw append-only log
    convo = r.conversation()             # final snapshot (message dicts)
    chan = r.channel_messages("tasks")   # one channel's history

    r.index()                            # ad-hoc FTS index, then:
    hits = r.search("score.json", k=5)
```

`turns()` 会跳过重生成 / 编辑产生的兄弟分支：你看到的就是所有查看
器展示的那条活动分支。`search()` 只对已建索引的会话返回结果
（`kt embedding`，或临时用 `reader.index()` 建 FTS）。

需要原始读写访问时，`SessionStore(path)` 还在，但任何列表 / 预览 /
查看器用途都应该用 `SessionStore.open_readonly(path)`（或干脆用
`SessionReader`）：普通的打开 + 关闭会把会话标记成已暂停并更新
`last_active`，破坏按最近使用排序。

## 压缩

上下文快满时，压缩会收缩对话。按生物配置：

```yaml
compact:
  enabled: true
  threshold: 0.8              # compact when context hits 80% of window
  target: 0.5                 # aim for 50% after compaction
  keep_recent_turns: 5        # always preserve the last N turns verbatim
  compact_model: gpt-4o-mini  # cheaper model for the summarization pass
```

压缩在后台运行（见 [concepts/modules/memory-and-compaction](../concepts/modules/memory-and-compaction.md)）：
控制器继续干活；新摘要就绪后，对话被换入。每次压缩都记成一个事件。

手动压缩：

```
/compact
```

在 CLI/TUI 提示符下输入。把长会话交接出去、或当作上下文喂给另一次
运行之前，先压一下很有用。

## 列出与搜索会话

`kt serve` 的 Web UI 和 `GET /api/sessions` 由一个旁路索引支撑：
`<session_dir>/.kt-index.kvault` 这一个 SQLite 文件缓存每个会话的
列表型元数据（名称、状态、最后活跃时间、Agent、预览等），并对文本
列提供 BM25 搜索。你不会直接和它打交道；它跨服务器重启保持一致，
服务器停机时用 `kt run` 起的会话也能对上。

`GET /api/sessions` 的查询参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `limit` | `20` | 页大小 |
| `offset` | `0` | 页偏移 |
| `search` | `""` | 对 `name` / `preview` / `config_path` / `agents` / `pwd` 的 FTS5 查询 |
| `sort` | `last_active` | `last_active` \| `created_at` \| `name` \| `status` \| `relevance` |
| `order` | `desc` | `desc` \| `asc` |
| `status` | （无） | 精确匹配（`running`、`paused` 等） |
| `config_type` | （无） | 精确匹配（`agent`、`terrarium`） |
| `node_id` | （无） | 精确匹配，按运行该会话的 lab 节点过滤 |
| `refresh` | `false` | 列表前做增量对账，只重读 `(mtime, size)` 变了的文件 |
| `full_rescan` | `false` | 强制重读每个文件（手动改过磁盘上的 `.kohakutr` 之后用） |

`sort=relevance` 只有设置了 `search` 时才有意义；用其他排序时，先
收集 FTS 命中集，再按指定字段排序。

索引如何在不手动刷新的情况下保持同步：

- **推送。**API 服务器运行期间，它持有的每个 `SessionStore` 都按
  防抖节奏（每 20 个事件或 5 秒，先到为准）把更新推进索引，正常
  使用时索引不会落后。
- **启动对账。**每次服务器启动，索引对会话目录做一次指纹差分，只
  重读变过的文件。首次启动会完整读取每个文件（*bootstrap* 步骤）
  并记住成功过。
- **`?refresh=true`。**按需触发同样的增量对账，刚把备份的
  `.kohakutr` 复制进会话目录之后特别有用。

旁路索引可以放心删除：下次列表会从 `.kohakutr` 文件重建它。索引里
没有任何唯一状态。

不经 HTTP 层的编程式列表：

```python
from kohakuterrarium.studio.persistence.session_index import (
    get_session_index_default,
)

index = get_session_index_default()
page = index.list(search="auth bug", sort="relevance", limit=10)
for row in page.rows:
    print(row["name"], row["last_active"], row["preview"])
```

## 记忆搜索

会话同时也是可搜索的知识库。建好索引之后：

```bash
kt embedding ~/.kohakuterrarium/sessions/swe.kohakutr
kt search swe "auth bug"
```

Agent 自己可以用 `search_memory` 工具搜索。完整讲解：[记忆](memory.md)。

## 禁用持久化

有时你只想要一次性的运行：

```bash
kt run @kt-biome/creatures/swe --no-session
```

不创建 `.kohakutr`。这同时会让压缩失去从磁盘找回早先轮次的能力
（内存内照常压缩）。

## 故障排查

- **压缩跑不完 / OOM。**压缩模型沿用了重型的控制器模型。把
  `compact_model` 设成便宜的（`gpt-4o-mini`、`claude-haiku`）。
- **恢复报 `tool not registered`。**生物配置变了（某个工具被删），
  但对话还引用它。手动改 `config.yaml` 把工具加回来，或开新会话。
- **`kt resume` 找不到我刚见过的会话。**会话按前缀对
  `~/.kohakuterrarium/sessions/` 下的文件名解析。重命名或移动过的
  文件请传完整路径。
- **复制会话后生成的图片不见了。**同级的 `<session>.artifacts/`
  目录也要一起复制，不能只拷 `.kohakutr`。
- **`.kohakutr` 文件很大。**事件日志是追加式的；长会话会增长。
  归档旧会话，或把工作拆成多个会话。压缩只收缩活动对话，完整事件
  历史会保留供搜索。
- **恢复后子代理输出缺失。**子代理对话在子代理完成时保存。父级在
  子代理中途被打断时，最新快照就是上一个检查点持久化的内容。

## 另请参阅

- [记忆](memory.md)：对会话历史的 FTS、语义和混合搜索。
- [配置](configuration.md)：压缩配方和会话相关参数。
- [编程式用法](programmatic-usage.md)：用 Python 驱动 Agent 和引擎。
- [参考 / Python API](../reference/python.md#会话)：`SessionReader` / `SessionStore` 签名。
- [概念 / 记忆与压缩](../concepts/modules/memory-and-compaction.md)：压缩的工作方式。
