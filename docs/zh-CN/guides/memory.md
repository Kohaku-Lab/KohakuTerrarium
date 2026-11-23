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

- `content`：命中的文字
- `agent`：由哪个Creature产生
- `block_type`：`text` / `tool` / `trigger` / `user`
- `round_num`, `block_num`：在会话中的位置
- `score`：命中品质
- `ts`：时间戳记

## Embedding 提供者

共有三种提供者，选一个符合你环境的：

| Provider | 需要什么 | 说明 |
|---|---|---|
| `model2vec`（默认） | 不需要 torch、纯 NumPy | 极快，安装最精简。对接近关键字的检索品质不错，但长文本语意搜索较弱。 |
| `sentence-transformer` | `torch` | 较慢，但语意品质强很多。也适合 GPU。 |
| `api` | 网路 + API key | 远端 embedder（OpenAI、Jina、Gemini）。品质最好，但按次计费。 |
| `auto` | （无） | 若可用 API，优先用 `jina-v5-nano`，否则退回 `model2vec`。 |

默认模型名称（可跨 provider 使用）：

- `@tiny`：最小、最快
- `@base`：默认平衡
- `@retrieval`：为检索调校
- `@best`：最高品质
- `@multilingual`, `@multilingual-best`：非英文会话
- `@science`, `@nomic`, `@gemma`：特化用途

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

`--dimensions` 是 Matryoshka truncation：如果模型支持，可用它在执行时直接缩小向量维度。

增量建立：通常再次执行 `kt embedding` 时，只处理新增事件。
当会话的记忆索引格式或 Embedding 模型发生变化时，整个 Session 会开启新的索引代际：
旧 FTS 和向量记录会先失效，不能继续参与检索。FTS 可在不加载 Embedding 模型时重建；
Semantic/Hybrid 检索则需要所有已索引 Agent 的向量完成重建。
中断或失败的重建会保持不可用，待安全重试，不会误用旧版本的向量。

Terrarium Session 可以包含多个 Creature 的事件，但共享同一个 SessionStore 的
Creature 必须使用相同的 Embedding 配置。配置冲突会在 Creature 启动时报错，
不会静默覆盖其他 Creature 的模型。需要切换 Session 模型时，应明确重建向量索引，
并同步调整各 Creature 的配置。

KT 比较的是**声明的向量空间配置**（Provider、模型、Endpoint、维度以及 KT 实际支持并传给
Embedding 实现的相关参数），并不验证远端服务是否在相同模型名称下更换权重或路由；
这类变化由服务提供方或用户负责，必要时应显式重建索引。API Key 和环境变量名
不属于向量索引身份，轮换凭据无需重新计算向量。

SessionStore 的 embedding_config 记录不再保存明文 API Key。离线 CLI / Studio 检索从当前环境变量
解析认证信息；持久化的环境变量名仅是非敏感的查找提示。旧版本保存在 SessionStore
中的 API Key 不会被再次用于认证；无法获取当前有效凭据时，应明确报告认证问题，
或者使用不需要 Embedding 的 FTS 模式。


## 索引格式升级后的版本回退

Session 的**原始事件文件格式仍为 v2**；全分支 Memory **派生索引**
使用 v3。旧版 KT 虽然能打开 Session，却可能静默复用不兼容的 FTS／
向量表。普通增量索引**不能**修复：旧游标可能已经将全部事件记为完成。

1. 停止所有使用该 Session 的 KT 进程（含 Agent、Studio、检索命令），
   等待写入结束。备份完整的 `.kohakutr` 文件及关联产物；如存在 SQLite
   `-wal`／`-shm` 文件也需保留。不要直接复制正在写入的数据库并将其
   视为一致性备份。
2. **不需要保留试用期间的新对话时**：优先恢复升级前的完整备份，再
   使用旧版 KT。
3. **需要保留新对话时**：切换至旧版 KT 代码后，必须**强制重建旧版
   单分支索引**，之后才能使用 Memory Search。旧版 `kt embedding`
   命令没有 `--force` 参数。仅恢复 **FTS** 时，用**旧版 KT 的 Python
   环境**执行以下命令（替换 Session 文件路径）：

   ```bash
   python -c "import sys; from pathlib import Path; from kohakuterrarium.studio.sessions.memory.build import build_index; print(build_index(Path(sys.argv[1]), provider='none', force=True)['stats'])" "/path/to/session.kohakutr"
   ```

   **注意：**`provider='none'` 会将持久化的 `embedding_config` 改为
   `none`，之后只能依赖 `--mode fts`。如需继续使用 Semantic／Hybrid，
   应使用原有且可用的 Embedding **Provider、Model、Dimensions**
   执行 `build_index(..., force=True)`，同时重建 FTS 与向量。例如，
   只有原配置确实为对应模型时才使用
   `provider='model2vec', model='@base'`。模型、凭据或自定义端点
   不可用时可能需要恢复升级前的备份。执行 FTS-only 恢复前，
   应另外保存原始 Embedding 配置。
4. 用 `kt search <session> "<phrase>" --mode fts` 验证所选分支的
   已知片段可以召回，而只存在于未选分支的片段**不会**召回；
   同时确认原始 Session 历史仍可打开。验证前不能视为回退完成。

以上操作只针对**派生索引**，不改写原始事件。之后再次升级到 v3 时，
由新版 Reconciler 重新构建索引。如果此前 FTS-only 回退改写了
Embedding 配置，则需显式恢复／重建目标模型，才能继续使用
Semantic／Hybrid 检索。

## 从 CLI 搜索

```bash
kt search swe "auth bug"                # auto 模式（若已有向量则 hybrid，否则 fts）
kt search swe "auth bug" --mode fts     # 仅关键字
kt search swe "auth bug" --mode semantic
kt search swe "auth bug" --mode hybrid
kt search swe "auth bug" --agent swe -k 5
```

模式：

- **`fts`**：在 FTS5 上跑 BM25。不需要 embedding。最快，适合精确片语。
- **`semantic`**：纯向量相似度。需要索引。适合同义改写。
- **`hybrid`**：先用 BM25 找候选，再以向量相似度重排。当两者都可用时会是默认。
- **`auto`**：自动选择该会话支持的最完整模式。

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

当 LLM 调用 `search_memory` 时，工具会对 *目前* 会话的索引执行搜索。这是 seamless-memory 的基本原语：agent 不需要额外搭 RAG 架构，就能查出自己（或队友）在前几轮说过什么。

记忆索引会保留**所有分支**的可检索内容，但普通搜索只返回各 Agent 当前选定的祖先链（未显式选择时使用最新兼容分支）。切换分支无需重新计算旧分支的 embedding。检索先按祖先链过滤，再截取 Top-K；当底层 Vault 无法在有界候选预算内证明结果完整时，工具会明确报错，而不会误报“没有结果”。

工具参数（形状；实际语法取决于你的 `tool_format`，下面示范默认 bracket 格式）：

```
[/search_memory]
@@query=auth bug
@@mode=hybrid
@@k=5
@@agent=swe
[search_memory/]
```

### 根据搜索命中展开上下文

Creature 的搜索结果会为每条命中返回一个不透明的 `anchor`。无需猜测下一组关键词，可以直接读取命中事件前后的时间线：

```text
[/search_memory]
@@anchor=<复制上一条搜索结果中的 anchor>
@@before=4
@@after=6
[search_memory/]
```

返回内容按事件时间排序，包括用户输入、工具调用与结果等相关事件。每条上下文事件也带有可继续导航的 Anchor，`has_before` / `has_after` 表示窗口两端是否仍有可读历史。`before` / `after` 各为 0～10（默认 3），单事件预览和扫描量也有限制。Anchor 固定其创建时的祖先链，后续切换分支不会把不同分支的事件混在一起。

长工具输出优先展示命中词附近的片段，而非只截取开头。对被截断的事件，可以根据 content_slice / next_offset 使用 search_memory(anchor=..., offset=..., before=0, after=0) 在事件内部继续分页读取。

Anchor 仅在当前附加的 SessionStore 生命周期内有效。普通追加和保留源事件的压缩不会使其失效；重启、Anchor 被淘汰或源记录变化会返回明确错误。上下文读取不加载 embedding 模型。


如果你要对 *外部* 数据源做 RAG，请自己做一个 custom tool，或做一个会调用向量数据库的 [插件指南](plugins.md)。

## 在Creature里设置记忆

```yaml
memory:
  embedding:
    provider: model2vec       # 或 sentence-transformer、api、auto
    model: "@retrieval"      # preset 或 HF 路径
```

带有这个区块的 agent，事件一进来就会自动建立索引，不需要再手动调用 `kt embedding`。没有这个区块的 agent，仍然会保留未嵌入的会话（但还是能用 FTS 搜索）。

## 用程式检查

```python
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.embedding import GeminiEmbedder

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
- **`search_memory` 没有返回任何结果**。 会话缺少 embedding 设置，或这个会话是在加入记忆设置之前启动的，请用 `kt embedding` 重新建立。

## 延伸阅读

- [会话](sessions.md)：记忆建立在 `.kohakutr` 格式之上。
- [插件指南](plugins.md)：seamless-memory 插件模式（`pre_llm_call` 检索）。
- [参考 / CLI](../reference/cli.md)：`kt embedding`、`kt search` 的旗标。
- [概念 / 记忆与压缩](../concepts/modules/memory-and-compaction.md)：背后的设计理由。
