---
title: 記憶
summary: 在工作階段儲存之上建立 FTS5 + 向量記憶、選擇 embedding 提供者，以及常見檢索模式。
tags:
  - guides
  - memory
  - embedding
---

# 記憶

給想要搜尋過去工作階段事件的讀者：無論是從 CLI、從 Python，還是讓 agent 在執行時自己查。

工作階段的事件日誌，同時也是一個小型的本機知識庫。替它建立搜尋索引後，你會得到 FTS 關鍵字搜尋（免費、快速）、語意搜尋（需要 embedder），以及用 embedding 相似度重新排序關鍵字命中的混合搜尋。Agent 也可以透過內建的 `search_memory` 工具，查詢自己或其他工作階段的記憶。

概念先讀：[記憶與壓縮](../concepts/modules/memory-and-compaction.md)、[工作階段](sessions.md)。

## 哪些內容可搜尋

`~/.kohakuterrarium/sessions/*.kohakutr` 裡的每一個事件，都是一個可搜尋的「區塊」：使用者輸入、assistant 文字、工具呼叫、工具結果、子代理輸出、頻道訊息。區塊會依處理輪次分組，所以搜尋結果可以把上下文帶回正確的時間點。

搜尋會回傳 `SearchResult` 紀錄，包含：

- `content` — 命中的文字
- `agent` — 由哪個生物產生
- `block_type` — `text` / `tool` / `trigger` / `user`
- `round_num`, `block_num` — 在工作階段中的位置
- `score` — 命中品質
- `ts` — 時間戳記

## Embedding 提供者

共有三種提供者，選一個符合你環境的：

| Provider | 需要什麼 | 說明 |
|---|---|---|
| `model2vec`（預設） | 不需要 torch、純 NumPy | 極快，安裝最精簡。對接近關鍵字的檢索品質不錯，但長文本語意搜尋較弱。 |
| `sentence-transformer` | `torch` | 較慢，但語意品質強很多。也適合 GPU。 |
| `api` | 網路 + API key | 遠端 embedder（OpenAI、Jina、Gemini）。品質最好，但按次計費。 |
| `auto` | — | 若可用 API，優先用 `jina-v5-nano`，否則退回 `model2vec`。 |

預設模型名稱（可跨 provider 使用）：

- `@tiny` — 最小、最快
- `@base` — 預設平衡
- `@retrieval` — 為檢索調校
- `@best` — 最高品質
- `@multilingual`, `@multilingual-best` — 非英文工作階段
- `@science`, `@nomic`, `@gemma` — 特化用途

你也可以直接傳入 Hugging Face 路徑。

## 建立索引

```bash
kt embedding ~/.kohakuterrarium/sessions/swe.kohakutr
```

指定明確選項：

```bash
kt embedding swe.kohakutr \
  --provider sentence-transformer \
  --model @best \
  --dimensions 384
```

`--dimensions` 是 Matryoshka truncation——如果模型支援，可用它在執行時直接縮小向量維度。

增量建立：再次執行 `kt embedding` 時，只會索引新增事件。

## 從 CLI 搜尋

```bash
kt search swe "auth bug"                # auto 模式（若已有向量則 hybrid，否則 fts）
kt search swe "auth bug" --mode fts     # 僅關鍵字
kt search swe "auth bug" --mode semantic
kt search swe "auth bug" --mode hybrid
kt search swe "auth bug" --agent swe -k 5
```

模式：

- **`fts`** — 在 FTS5 上跑 BM25。不需要 embedding。最快，適合精確片語。
- **`semantic`** — 純向量相似度。需要索引。適合同義改寫。
- **`hybrid`** — 先用 BM25 找候選，再以向量相似度重排。當兩者都可用時會是預設。
- **`auto`** — 自動選擇該工作階段支援的最完整模式。

`-k` 用來限制結果數量。`--agent` 可把搜尋範圍限制在生態瓶工作階段中的單一生物。

## 從 agent 搜尋

內建的 `search_memory` 工具，把同一套搜尋引擎暴露給 controller：

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

當 LLM 呼叫 `search_memory` 時，工具會對 *目前* 工作階段的索引執行搜尋。這是 seamless-memory 的基本原語——agent 不需要額外搭 RAG 架構，就能查出自己（或隊友）在前幾輪說過什麼。

工具參數（形狀；實際語法取決於你的 `tool_format`——下面示範預設 bracket 格式）：

```
[/search_memory]
@@query=auth bug
@@mode=hybrid
@@k=5
@@agent=swe
[search_memory/]
```

如果你要對 *外部* 資料源做 RAG，請自己做一個 custom tool，或做一個會呼叫向量資料庫的 [prompt plugin](plugins.md)。

## 在生物裡設定記憶

```yaml
memory:
  embedding:
    provider: model2vec       # 或 sentence-transformer、api、auto
    model: "@retrieval"      # preset 或 HF 路徑
```

帶有這個區塊的 agent，事件一進來就會自動建立索引——不需要再手動呼叫 `kt embedding`。沒有這個區塊的 agent，仍然會保留未嵌入的工作階段（但還是能用 FTS 搜尋）。

## 用程式檢查

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

## 疑難排解

- **`No vectors in index`。** 你用了 `--mode semantic`，但還沒先執行 `kt embedding`。請先建立索引，或改用 `--mode fts`。
- **`kt embedding` 很慢。** `sentence-transformer` 預設是 CPU-bound。請安裝支援 CUDA 的 torch，或改用 `model2vec`。
- **Provider 安裝失敗。** `kt embedding --provider model2vec` 沒有 native 依賴，在哪裡都能跑。`sentence-transformer` 需要 `torch`；`api` 需要對應 provider 的 SDK（`openai`、`google-generativeai` 等）。
- **Hybrid 模式結果很多雜訊。** 把 `-k` 調低；如果查詢很多改寫語句，偏向用 `semantic` 而不是 `hybrid`；如果查的是精確片語，偏向用 `fts`。
- **`search_memory` 沒有回傳任何結果。** 工作階段缺少 embedding 設定，或這個工作階段是在加入記憶設定之前啟動的——請用 `kt embedding` 重新建立。

## 延伸閱讀

- [工作階段](sessions.md) — 記憶建立在 `.kohakutr` 格式之上。
- [外掛](plugins.md) — seamless-memory 外掛模式（`pre_llm_call` 檢索）。
- [參考 / CLI](../reference/cli.md) — `kt embedding`、`kt search` 的旗標。
- [概念 / 記憶與壓縮](../concepts/modules/memory-and-compaction.md) — 背後的設計理由。
