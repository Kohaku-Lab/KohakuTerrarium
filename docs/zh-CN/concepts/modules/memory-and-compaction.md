---
title: 记忆与压缩 (Memory and compaction)
summary: 会话储存如何同时成为可搜寻的记忆，以及非阻塞压缩如何把上下文维持在预算内。
tags:
  - concepts
  - memory
  - compaction
---

# 记忆与压缩

## 它是什么

这里其实是两个彼此相关的系统：

- **记忆**。 `.kohakutr` 会话档同时扮演运行时持久化与可搜寻知识库。
  每个事件都会建立索引，用于全文搜寻（FTS5），也可选择建立向量搜寻。
  Agent 可以透过 `search_memory` 工具，从内部查询这些内容。
- **压缩**。 长时间执行的 Creature终究会撑爆上下文视窗。自动压缩会在背景
  摘要旧回合，而且不会暂停控制器，因此 Agent 能一边继续工作，一边把
  过去内容压缩得更精简。

这其实是同一个问题的两面：*我们要怎么处理Creature累积下来的历史？*

## 为什么它存在

### 记忆

多数 Agent 框架把历史当成暂时性的东西：它只服务当前的 LLM 调用，
也许会为了「resume」而持久化，其余情况下就消失了。这会丢掉大量讯号。
同一份事件纪录其实可以同时支持：

- `kt resume`（在工作中途重建 Agent）、
- `kt search`（让人类查看发生过什么）、
- Agent 对自己历史做 RAG（`search_memory`）。

一个储存，三个消费者。

### 压缩

上下文视窗虽然持续变大，但永远追不上需求增长。没有压缩的话，跑了几小时
的 Creature最后一定会撞墙。天真的压缩方式会在摘要期间直接暂停 Agent — 在
Agent 框架里，这等于「控制器卡住，等待 50k token 被浓缩成 2k」。
对 ambient Agents 来说，这是不能接受的。

非阻塞压缩会在背景 task 里完成摘要，并在回合与回合之间以原子方式把结果
接回去。控制器本身不会停下来。

## 我们怎么定义它

### 会话储存的形状

`.kohakutr` 是一个 SQLite 文件（透过 KohakuVault），里面有以下数据表：

- `meta` — 会话中继数据、快照、配置
- `events` — append-only 事件日志
- `state` — scratchpad、计数器、每个 Agent 的状态
- `channels` — 消息历史
- `conversation` — 供快速 resume 使用的最新快照
- `sub-agents` — 子 Agent的对话快照
- `jobs` — 工具 / 子 Agent执行纪录
- `fts` — 事件的全文索引
- （向量索引，可选，只有建立 embeddings 时才有）

### 压缩契约

Creature有一个 `compact` 配置区块，包含：`enabled`、`max_tokens`
（或自动推导）、`threshold`（到达多少预算百分比时开始压缩）、
`target`（压缩后降到多少百分比）、`keep_recent_turns`
（永不摘要的活跃区），以及可选的 `compact_model`
（更便宜的摘要模型）。

每回合结束时，如果 `prompt_tokens >= threshold * max_tokens`，
compact manager 就会启动一个背景 task。

## 我们怎么实现它

- `session/store.py` — 以 KohakuVault 为后端的持久化储存。
- `session/output.py` — 负责写入事件的 output consumer。
- `session/resume.py` — 把数据重播进新建好的 Agent。
- `session/memory.py` — FTS5 查询与向量搜寻。
- `session/embedding.py` — model2vec / sentence-transformer / API
  provider 的 embeddings。
- `core/compact.py` — 使用 atomic-splice 技巧的 `CompactManager`。
  见 [impl-notes/non-blocking-compaction](../impl-notes/non-blocking-compaction.md)。

Embedding provider（`kt embedding`）：

- **model2vec**（预设，不需要 torch；预设组合包含 `@tiny`、
  `@best`、`@multilingual-best` 等）
- **sentence-transformer**（需要 torch）
- **api**（外部 embedding 端点，例如 jina-v5-nano）

## 因此你可以做什么

- **从任何地方恢复**。 `kt resume` / `kt resume --last` 可以接回数小时前
  被中断的会话。
- **搜寻会话**。 `kt search <session> <query>` — 支持 FTS、语意、
  hybrid 或自动侦测模式。
- **Agent 端 RAG**。 Agent 在回合中调用 `search_memory`，取回相关过去事件，
  然后带着这些上下文继续。
- **长时间 ambient 执行**。 一只连跑数天的 Creature不会撞上上下文墙：压缩会让
  滚动摘要一直维持在最新 N 个回合之上。
- **跨会话记忆**。 更进阶的配置可以从 config 拉出 session store 路径，
  让相关Creature共享同一份储存。

## 不要被它框住

会话持久化是 opt-out（`--no-session`）。embeddings 是 opt-in。
压缩则是每个 Creature各自 opt-out。Creature完全可以不使用这些功能 — 记忆是方便性，
不是必要条件。

## 另见

- [impl-notes/session-persistence](../impl-notes/session-persistence.md) — 双储存细节。
- [impl-notes/non-blocking-compaction](../impl-notes/non-blocking-compaction.md) — atomic-splice 演算法。
- [reference/cli.md — kt embedding, kt search, kt resume 参考](../../reference/cli.md) — 指令介面。
- [guides/memory.md 指南](../../guides/memory.md) — 实现指南。
