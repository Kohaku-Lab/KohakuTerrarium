---
title: 会话持久化
summary: 说明.kohakutr 文件格式、每个 Creature会储存哪些内容，以及 resume 如何重建对话状态。
tags:
  - concepts
  - impl-notes
  - persistence
---

# 会话持久化

## 这要解决的问题

一个Creature的历史数据有三个消费者，而且需求各不相同：

1. **Resume**。 发生崩溃后（或执行 `kt resume --last` 时），我们需要
  快速重建代理状态。因此我们希望序列化的内容尽可能精简。
2.**人类搜寻**。 用户执行 `kt search <session> <query>` 时，
  会期待能针对所有细节进行关键字 + 语意搜寻。
3.**代理端 RAG**。 执行中的代理在一个轮次内调用 `search_memory` 时，
  也会期待同样的能力。

单一储存层必须同时服务这三种用途。若数据形状选错，至少其中一种
就会变得昂贵，甚至不可行。

## 曾考虑的方案

- **仅储存对话记录**。 Resume 很便宜；搜寻很糟糕
  （没有工具活动、没有 trigger 触发、没有子 Agent输出）。
- **只有完整事件日志，没有快照**。 搜寻很好；resume 很慢
  （必须重播所有事件）。
- **只有快照**。 Resume 很快；但没有可搜寻的历史。
- **双重储存：append-only 事件日志 + 每轮对话快照**。 这就是我们的做法。

## 我们实际怎么做

`.kohakutr` 文件是一个 SQLite 数据库（透过 KohakuVault 管理），
其中包含下列表格：

- `events` — 每个事件的 append-only 日志（文字区块、工具调用、
  工具结果、trigger 触发、频道消息、token 使用量）。永不改写。
- `conversation` — 每个（Agent、轮次边界）对应一列快照，
  储存消息列表（透过 msgpack，可保留 tool-call 结构）。
- `state` — 草稿区与各 Agent 的计数器。
- `channels` — 频道消息历史。
- `sub-agents` — 已生成子 Agent的对话快照，会在销毁前储存。
- `jobs` — 工具／子 Agent执行纪录（状态、参数、结果）。
- `meta` — 会话中继数据、配置文件路径、执行识别资讯。
- `fts` — 建立在 events 上的 SQLite FTS5 索引（关键字搜寻）。
- 向量索引（选用，位于同一个 store 中）— 在需要时由
  `kt embedding` 建立。

### Resume 路径

1. 加载 `meta` → 取得 session id、config path、Creature清单。
2. 加载 `conversation[Agent]` 快照 → 重建 Agent 的
  `Conversation` 对象。
3. 加载 `state[Agent]:*` → 还原草稿区。
4. 加载 `type == "trigger_state"` 的 events → 透过
  `from_resume_dict` 重新建立 triggers。
5. 将事件重播给 output module 的 `on_resume` → 为 TTY 用户
  重绘 scrollback。
6. 加载 `sub-agents[parent:name:run]` → 重新接回子 Agent对话。

### 搜寻路径

- FTS 模式：`events` FTS5 比对 → 依顺序返回区块。
- 语意模式：向量搜寻 → 找出最近的事件。
- 混合模式：进行 rank-fuse。
- 自动模式：若向量存在则用语意搜寻，否则用 FTS。

### 代理端 RAG

内建工具 `search_memory` 会调用与 CLI 相同的搜寻层；若有要求，
可依 Agent 名称过滤；再截断命中结果，并将它们作为工具结果返回。

## 维持不变的条件

- **事件不可变**。 它们只会被追加。
- **快照以每轮为单位**。 不是每个事件一份。Resume 相对于快照是 O(1)，
  而不是相对于整段历史的 O(N)。
- **不可序列化的状态会从 config 重建**。 像 sockets、pywebview
  handles、LLM provider sessions —— 都是重新建立，而不是还原。
- **每个会话一个文件**。 可携、可复制；`.kohakutr` 副档名也让工具
  能辨识它。
- **Resume 可选择停用**。 `--no-session` 会完全停用这个 store。

## 列表用 sidecar

`.kohakutr` 文件是数据的来源，但用来「列表」的形状不对 ——
当 `GET /api/sessions` 面对 1000 个 session 时，不应该为了渲染
侧边栏就打开 1000 个 SQLite 文件。我们在上层加了一个 write-through
cache：

```
<session_dir>/.kt-index.kvault   ← 单一 SQLite 文件，三张表：
    entries  (KVault)    filename → 打包后的 SessionIndexEntry
    search   (TextVault) 针对 name / preview / config_path /
                         agents / pwd 的 FTS5（用 BM25 排名）
    meta     (KVault)    schema_version、bootstrap_completed 等
```

一笔 `SessionIndexEntry` 是列表形状的扁平 dict（`name`、
`last_active`、`status`、`config_type`、`node_id`、
`terrarium_name`、`preview`、`agents`、`parent_session_id`、
`forked_children`…），再加上从 `stat()` 拿到的 `(mtime, size)`
指纹。一只 session 一列。冷列表现在只需要一次文件打开 + 一次表格
扫描，与 session 数量无关；搜寻则是单一 FTS5 查询。

### 索引怎么和磁盘保持一致

三条独立路径让 entries 与磁盘上的文件同步 —— 任一条都不是单点：

1. **Push hook**（`session_index/hooks.py`）。当 API server 自身
   拥有一个 `SessionStore` 时，`SessionIndexHook` 会订阅其事件流，
   每 20 个事件或 5 秒（先到先发）就 upsert 一次条目。同一个 store
   既写事件也更新索引 —— 不会落后。

2. **Pull reconcile**（`session_index/reconcile.py`）。走访 session
   目录，把每个文件做指纹比对，只打开 `(mtime, size)` 和索引不同
   （或索引中尚未存在）的文件，重新读取它们的 meta 与 preview。
   已删除的文件会从索引剔除。这就是 API 在 `?refresh=true` 时
   触发的路径。`?full_rescan=true` 则强制重新读取所有文件 ——
   用在手动修改 `.kohakutr` 之后。

3. **Startup reconcile**。`get_session_index_default` 这个 singleton
   在每个进程第一次打开时跑 reconcile。第一次开启时跑 full
   reconcile（bootstrap）并设置 `bootstrap_completed` 旗标；之后的
   每次开启（server 重启）跑增量路径，于是另一个进程在 server
   关闭期间产出的 sessions（例如另一个终端跑的 `kt run`）会被自动
   接进来。这里若失败会大声 log，但绝不挡 server 启动 —— 提供
   过期数据，仍比完全无法服务好。

### 为什么要用 sidecar（而不是只放在内存）

- **可以撑过重启**。 一个跑了很久的 server 即便崩在列表中途，下次
  开起来只要做指纹差异比对，毫秒级就能恢复，不需要重新打开 N 个文件。
- **可以撑过搬家**。 `mv ~/.kohakuterrarium/sessions /backup` 会把
  sidecar 一起带过去；之后在 `/backup` 列表是瞬间完成。
- **一次打开、一次查询**。 列出 1000 个 sessions 是一次 SQLite 打开
  + 一次 `ORDER BY last_active LIMIT 20`（或一次 FTS5 match）。
  没有 sidecar 之前的路径，光是渲染首页都要打开 1000 个文件。

Sidecar 是可以直接删除的；下次调用 `get_session_index_default` 就会
重建。它没有迁移路径，因为里面没有任何不存在于 `.kohakutr` 来源
文件中的状态。

## 代码中的位置

- `src/kohakuterrarium/session/store.py` — `SessionStore` API。
- `src/kohakuterrarium/session/output.py` — `SessionOutput` 透过
  `OutputModule` 协定记录事件，因此控制器层不需要特别处理。
- `src/kohakuterrarium/session/resume.py` — 重建路径。
- `src/kohakuterrarium/session/memory.py` — FTS 与向量查询。
- `src/kohakuterrarium/session/embedding.py` — embedding providers。
- `src/kohakuterrarium/studio/persistence/session_index/` — 列表用
  sidecar：`entry.py`（列结构）、`store.py`（KVault + TextVault 包装）、
  `reconcile.py`（指纹差异 + 并行重读）、`hooks.py`（来自运行中
  SessionStore 的 live push）、`__init__.py`（进程内 singleton +
  启动 reconcile）。
- `src/kohakuterrarium/api/routes/persistence/saved.py` — HTTP 介面
  （`GET /api/sessions`、`DELETE /api/sessions/{name}`）。

## 另请参阅

- [记忆与压缩](../modules/memory-and-compaction.md) — 概念层面的说明。
- [reference/cli.md — kt resume, kt search, kt embedding 参考](../../reference/cli.md) — 用户可见介面。
