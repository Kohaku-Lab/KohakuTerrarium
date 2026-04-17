# Memory

For readers who want to search past session events — from the CLI, from Python, or from an agent at runtime.

A session's event log is also a small local knowledge base. Building a search index over it gives you FTS keyword search (free, fast), semantic search (needs an embedder), and hybrid search that reranks keyword hits with embedding similarity. Agents can query their own or another session's memory through the built-in `search_memory` tool.

Concept primer: [memory and compaction](../concepts/modules/memory-and-compaction.md), [sessions](sessions.md).

## What's searchable

Every event in `~/.kohakuterrarium/sessions/*.kohakutr` is a searchable "block": user input, assistant text, tool calls, tool results, sub-agent outputs, channel messages. Blocks are grouped by processing round so search results carry context back to the right moment.

Search returns `SearchResult` records with:

- `content` — the matched text
- `agent` — which creature produced it
- `block_type` — `text` / `tool` / `trigger` / `user`
- `round_num`, `block_num` — position in the session
- `score` — match quality
- `ts` — timestamp

## Embedding providers

Three providers, pick one that matches your environment:

| Provider | What it needs | Notes |
|---|---|---|
| `model2vec` (default) | no torch, pure NumPy | Extremely fast, smallest install. Quality is good for keyword-adjacent retrieval, weaker for long-form semantic. |
| `sentence-transformer` | `torch` | Slower but much stronger semantic quality. GPU-friendly. |
| `api` | network + API key | Remote embedders (OpenAI, Jina, Gemini). Best quality, pay per call. |
| `auto` | — | Prefers `jina-v5-nano` if API available, else falls back to `model2vec`. |

Preset model names (portable across providers):

- `@tiny` — smallest/fastest
- `@base` — default balance
- `@retrieval` — tuned for retrieval
- `@best` — highest quality
- `@multilingual`, `@multilingual-best` — non-English sessions
- `@science`, `@nomic`, `@gemma` — specialized

You can also pass a Hugging Face path directly.

## Build an index

```bash
kt embedding ~/.kohakuterrarium/sessions/swe.kohakutr
```

With explicit options:

```bash
kt embedding swe.kohakutr \
  --provider sentence-transformer \
  --model @best \
  --dimensions 384
```

`--dimensions` is Matryoshka truncation — use it to shrink vectors on the fly when the model supports it.

Incremental: running `kt embedding` again only indexes new events.

## Search from the CLI

```bash
kt search swe "auth bug"                # auto mode (hybrid if vectors exist, else fts)
kt search swe "auth bug" --mode fts     # keyword only
kt search swe "auth bug" --mode semantic
kt search swe "auth bug" --mode hybrid
kt search swe "auth bug" --agent swe -k 5
```

Modes:

- **`fts`** — BM25 over FTS5. No embeddings needed. Fastest. Great for literal phrases.
- **`semantic`** — pure vector similarity. Needs an index. Great for paraphrases.
- **`hybrid`** — BM25 candidates reranked by vector similarity. Default when both are available.
- **`auto`** — picks the richest mode the session supports.

`-k` caps results. `--agent` filters to one creature inside a terrarium session.

## Search from an agent

The built-in `search_memory` tool exposes the same engine to the controller:

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

When the LLM calls `search_memory`, the tool runs over the *current* session's index. This is the seamless-memory primitive — agents can look up what they (or their teammates) said in earlier rounds without explicit RAG scaffolding.

Tool args (shape; concrete syntax depends on your `tool_format` — default bracket shown):

```
[/search_memory]
@@query=auth bug
@@mode=hybrid
@@k=5
@@agent=swe
[search_memory/]
```

For RAG over *external* sources, build a custom tool or a [prompt plugin](plugins.md) that calls your vector store.

## Configuring memory in a creature

```yaml
memory:
  embedding:
    provider: model2vec       # or sentence-transformer, api, auto
    model: "@retrieval"       # preset or HF path
```

Agents with this block indexed automatically as events land — no `kt embedding` call needed. Agents without it keep an unembedded session (still FTS-searchable).

## Inspecting programmatically

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

## Troubleshooting

- **`No vectors in index`.** You ran `--mode semantic` without first running `kt embedding`. Either build the index or use `--mode fts`.
- **Slow `kt embedding`.** `sentence-transformer` is CPU-bound by default. Install torch with CUDA, or drop to `model2vec`.
- **Provider install fails.** `kt embedding --provider model2vec` has no native deps and works everywhere. `sentence-transformer` needs `torch`; `api` needs the provider SDK (`openai`, `google-generativeai`, etc.).
- **Hybrid mode returns noise.** Lower `-k` and prefer `semantic` over `hybrid` for paraphrase-heavy queries; prefer `fts` for literal-phrase queries.
- **search_memory returns nothing.** The session's embedding config is missing or the session started before you added memory config — rebuild with `kt embedding`.

## See also

- [Sessions](sessions.md) — the `.kohakutr` format that memory sits on top of.
- [Plugins](plugins.md) — seamless-memory plugin pattern (`pre_llm_call` retrieval).
- [Reference / CLI](../reference/cli.md) — `kt embedding`, `kt search` flags.
- [Concepts / memory and compaction](../concepts/modules/memory-and-compaction.md) — the design rationale.
