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

## 代码中的位置

- `src/kohakuterrarium/session/store.py` — `SessionStore` API。
- `src/kohakuterrarium/session/output.py` — `SessionOutput` 透过
  `OutputModule` 协定记录事件，因此控制器层不需要特别处理。
- `src/kohakuterrarium/session/resume.py` — 重建路径。
- `src/kohakuterrarium/session/memory.py` — FTS 与向量查询。
- `src/kohakuterrarium/session/embedding.py` — embedding providers。

## 另请参阅

- [记忆与压缩](../modules/memory-and-compaction.md) — 概念层面的说明。
- [reference/cli.md — kt resume, kt search, kt embedding 参考](../../reference/cli.md) — 用户可见介面。
