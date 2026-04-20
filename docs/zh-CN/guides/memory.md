---
title: 记忆
summary: 在会话储存之上建立 FTS5 + 向量记忆、选择 embedding 提供者，以及常见检索模式。
tags:
 - guides
 - memory
 - embedding
---

# 记忆

给想要搜索过去会话事件的读者：无论是从 CLI、从 Python，还是让 agent 在执行时自己查。

会话的事件日志，同时也是一个小型的本地知识库。替它建立搜索索引后，你会得到 FTS 关键字搜索（免费、快速）、语意搜索（需要 embedder），以及用 embedding 相似度重新排序关键字命中的混合搜索。Agent 也可以通过内置的 `search_memory` 工具，查询自己或其他会话的记忆。

概念先读：[记忆与压缩](../concepts/modules/memory-and-compaction.md)、[会话](sessions.md)。

## 哪些内容可搜索

`~/.kohakuterrarium/sessions/*.kohakutr` 里的每一个事件，都是一个可搜索的「区块」：用户输入、assistant 文字、工具调用、工具结果、子代理输出、频道消息。区块会依处理轮次分组，所以搜索结果可以把上下文带回正确的时间点。

搜索会回传 `SearchResult` 记录，包含：

- `content` — 命中的文字
- `agent` — 由哪个Creature产生
- `block_type` — `text` / `tool` / `trigger` / `user`
- `round_num`, `block_num` — 在会话中的位置
- `score` — 命中品质
- `ts` — 时间戳记

## Embedding 提供者

共有三种提供者，选一个符合你环境的：

| Provider | 需要什么 | 说明 |
|---|---|---|
| `model2vec`（默认） | 不需要 torch、纯 NumPy | 极快，安装最精简。对接近关键字的检索品质不错，但长文本语意搜索较弱。 |
| `sentence-transformer` | `torch` | 较慢，但语意品质强很多。也适合 GPU。 |
| `api` | 网路 + API key | 远端 embedder（OpenAI、Jina、Gemini）。品质最好，但按次计费。 |
| `auto` | — | 若可用 API，优先用 `jina-v5-nano`，否则退回 `model2vec`。 |

默认模型名称（可跨 provider 使用）：

- `@tiny` — 最小、最快
- `@base` — 默认平衡
- `@retrieval` — 为检索调校
- `@best` — 最高品质
- `@multilingual`, `@multilingual-best` — 非英文会话
- `@science`, `@nomic`, `@gemma` — 特化用途

你也可以直接传入 Hugging Face 路径。

## 建立索引

```bash
kt embedding ~/.kohakuterrarium/sessions/swe.kohakutr
```

指定明确选项：

```bash
kt embedding swe.kohakutr \
  --provider sentence-transformer \
  --model @best \
  --dimensions 384
```

`--dimensions` 是 Matryoshka truncation——如果模型支持，可用它在执行时直接缩小向量维度。

增量建立：再次执行 `kt embedding` 时，只会索引新增事件。

## 从 CLI 搜索

```bash
kt search swe "auth bug"                # auto 模式（若已有向量则 hybrid，否则 fts）
kt search swe "auth bug" --mode fts     # 仅关键字
kt search swe "auth bug" --mode semantic
kt search swe "auth bug" --mode hybrid
kt search swe "auth bug" --agent swe -k 5
```

模式：

- **`fts`** — 在 FTS5 上跑 BM25。不需要 embedding。最快，适合精确片语。
- **`semantic`** — 纯向量相似度。需要索引。适合同义改写。
- **`hybrid`** — 先用 BM25 找候选，再以向量相似度重排。当两者都可用时会是默认。
- **`auto`** — 自动选择该会话支持的最完整模式。

`-k` 用来限制结果数量。`--agent` 可把搜索范围限制在Terrarium会话中的单一Creature。

## 从 agent 搜索

内置的 `search_memory` 工具，把同一套搜索引擎暴露给 controller：

```yaml
# creatures/my-agent/config.yaml
tools:
  - read
  - write
  - search_memory
memory:
  embedding:
    provider: model2vec
    model: "@base"
```

当 LLM 调用 `search_memory` 时，工具会对 *目前* 会话的索引执行搜索。这是 seamless-memory 的基本原语——agent 不需要额外搭 RAG 架构，就能查出自己（或队友）在前几轮说过什么。

工具参数（形状；实际语法取决于你的 `tool_format`——下面示范默认 bracket 格式）：

```
[/search_memory]
@@query=auth bug
@@mode=hybrid
@@k=5
@@agent=swe
[search_memory/]
```

如果你要对 *外部* 数据源做 RAG，请自己做一个 custom tool，或做一个会调用向量数据库的 [插件指南](plugins.md)。

## 在Creature里设置记忆

```yaml
memory:
  embedding:
    provider: model2vec       # 或 sentence-transformer、api、auto
    model: "@retrieval"      # preset 或 HF 路径
```

带有这个区块的 agent，事件一进来就会自动建立索引——不需要再手动调用 `kt embedding`。没有这个区块的 agent，仍然会保留未嵌入的会话（但还是能用 FTS 搜索）。

## 用程式检查

```python
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.memory import SessionMemory
from kohakuterrarium.session.embedding import GeminiEmbedder

store = SessionStore("~/.kohakuterrarium/sessions/swe.kohakutr")
embedder = GeminiEmbedder("gemini-embedding-004", api_key="...")
memory = SessionMemory(store.path, embedder=embedder, store=store)

memory.index_events("swe")
results = await memory.search("refactor", mode="hybrid", k=5)
for r in results:
    print(f"{r.agent} r{r.round_num}: {r.content[:120]} ({r.score:.2f})")

store.close()
```

## 疑难排解

- **`No vectors in index`**。 你用了 `--mode semantic`，但还没先执行 `kt embedding`。请先建立索引，或改用 `--mode fts`。
- **`kt embedding` 很慢**。 `sentence-transformer` 默认是 CPU-bound。请安装支持 CUDA 的 torch，或改用 `model2vec`。
- **Provider 安装失败**。 `kt embedding --provider model2vec` 没有 native 依赖，在哪里都能跑。`sentence-transformer` 需要 `torch`；`api` 需要对应 provider 的 SDK（`openai`、`google-generativeai` 等）。
- **Hybrid 模式结果噪声很多**。 把 `-k` 调低；如果查询很多改写语句，偏向用 `semantic` 而不是 `hybrid`；如果查的是精确片语，偏向用 `fts`。
- **`search_memory` 没有返回任何结果**。 会话缺少 embedding 设置，或这个会话是在加入记忆设置之前启动的——请用 `kt embedding` 重新建立。

## 延伸阅读

- [会话](sessions.md) — 记忆建立在 `.kohakutr` 格式之上。
- [插件指南](plugins.md) — seamless-memory 插件模式（`pre_llm_call` 检索）。
- [参考 / CLI](../reference/cli.md) — `kt embedding`、`kt search` 的旗标。
- [概念 / 记忆与压缩](../concepts/modules/memory-and-compaction.md) — 背后的设计理由。
