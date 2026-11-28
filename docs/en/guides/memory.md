---
title: Memory
summary: FTS5 + vector memory over the session store, embedding provider choice, and retrieval patterns.
tags:
  - guides
  - memory
  - embedding
---

# Memory

For readers who want to search past session events from the CLI, from Python, or from an agent at runtime.

A session's event log is also a small local knowledge base. Building a search index over it gives you FTS keyword search (free, fast), semantic search (needs an embedder), and hybrid search that reranks keyword hits with embedding similarity. Agents can query their own or another session's memory through the built-in `search_memory` tool.

Concept primer: [memory and compaction](../concepts/modules/memory-and-compaction.md), [sessions](sessions.md).

## What's searchable

Every event in `~/.kohakuterrarium/sessions/*.kohakutr` is a searchable "block": user input, assistant text, tool calls, tool results, sub-agent outputs, channel messages. Blocks are grouped by processing round so search results carry context back to the right moment.

Search returns `SearchResult` records with:

- `content`: the matched text
- `agent`: which creature produced it
- `block_type`: `text` / `tool` / `trigger` / `user`
- `round_num`, `block_num`: position in the session
- `score`: match quality
- `ts`: timestamp

## Embedding providers

Three providers, pick one that matches your environment:

| Provider | What it needs | Notes |
|---|---|---|
| `model2vec` (default) | no torch, pure NumPy | Extremely fast, smallest install. Quality is good for keyword-adjacent retrieval, weaker for long-form semantic. |
| `sentence-transformer` | `torch` | Slower but much stronger semantic quality. GPU-friendly. |
| `api` | network + API key | Remote embedders (OpenAI, Jina, Gemini). Best quality, pay per call. |
| `auto` | (none) | Prefers `jina-v5-nano` if API available, else falls back to `model2vec`. |

Preset model names (portable across providers):

- `@tiny`: smallest/fastest
- `@base`: default balance
- `@retrieval`: tuned for retrieval
- `@best`: highest quality
- `@multilingual`, `@multilingual-best`: non-English sessions
- `@science`, `@nomic`, `@gemma`: specialized

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

`--dimensions` is Matryoshka truncation; use it to shrink vectors on the fly when the model supports it.

Incremental: running `kt embedding` again only indexes new events.

### Navigate from a search hit

`search_memory(anchor=..., scope="selected")` follows the selected branch
view saved with the Anchor. If legacy ancestry cannot be resolved within
the read budget, selected context may be partial.

`search_memory(anchor=..., scope="raw")` reads bounded physical context
from the same Agent, including other branches and unknown lineage, with
recorded turn/branch coordinates. It does not broaden keyword or semantic
search. Both scopes verify the exact source and support continuation and
offset pagination.

## Rebuild memory indexes for an older Session

Close all agents, Studio instances and standalone Memory readers/writers
that use this Session. Run one command for the **whole Session**, without
selecting individual Agents:

```bash
kt memory rebuild /path/to/session.kohakutr
```

The command inspects existing indexes first. If old vector rows are present,
the interactive CLI asks whether to **recompute** their embeddings (may
download a model or incur API charges) or **drop** the obsolete semantic
index. In scripts, select the behavior explicitly:

```bash
kt memory rebuild /path/to/session.kohakutr --dry-run
kt memory rebuild /path/to/session.kohakutr --vectors drop
kt memory rebuild /path/to/session.kohakutr --vectors rebuild --provider sentence-transformer --model @best
```

A rebuild recreates selected-lineage FTS and Source Anchors from the original
event log. Existing all-branch indexes may become narrower: no recorded
events are deleted, but some previously indexed blocks may no longer be
searchable in the current projection. Rebuild refuses to clear a previously
indexed Agent's FTS/vector entries if no physical source events remain, and
uses the ordinary Session format gate to reject future archive-family versions.
This guards the derived Search index; it does not diagnose or repair missing
Session history.

Before updating the original Session through its normal Store and Memory
interfaces, the command creates a verified logical SQLite backup, including
committed WAL data. Rebuild never replaces the Session file or changes
external artifacts. Original events are not rewritten. If an index update is
interrupted, that index remains invalid until refreshed or rebuilt; retry
the command with the same vector policy. The backup preserves prior indexes,
including paid embeddings.

Existing vectors are not silently reused across incompatible index formats.
If you choose to drop vectors, semantic retrieval is disabled until an
embedding index is explicitly rebuilt. A vector reset does not clear the
previous rows for an Agent until its new embeddings have been computed.

A saved provider with no model name uses its default model. A failed model
load can be retried. SQLite manages its own WAL/SHM bookkeeping; **do not
delete sidecar files manually**. Offline maintenance requires stopping all
readers/writers, including consumers that do not participate in the Session
writer lock.

Old Search results may report `ANCHOR_REBUILD_REQUIRED` until the Session's
derived indexes have been rebuilt. Normal Search never silently triggers
this maintenance command.

## Search from the CLI

```bash
kt search swe "auth bug"                # auto mode (hybrid if vectors exist, else fts)
kt search swe "auth bug" --mode fts     # keyword only
kt search swe "auth bug" --mode semantic
kt search swe "auth bug" --mode hybrid
kt search swe "auth bug" --agent swe -k 5
```

Modes:

- **`fts`**: BM25 over FTS5. No embeddings needed. Fastest. Great for literal phrases.
- **`semantic`**: pure vector similarity. Needs an index. Great for paraphrases.
- **`hybrid`**: BM25 candidates reranked by vector similarity. Default when both are available.
- **`auto`**: picks the richest mode the session supports.

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

When the LLM calls `search_memory`, the tool runs over the *current* session's index. This is the seamless-memory primitive: agents can look up what they (or their teammates) said in earlier rounds without explicit RAG scaffolding.

Live searches keep model loading and encoding off the session's storage thread,
so a stalled embedder does not prevent history reads, event persistence, or graph
shutdown. `fts` never loads or calls an embedder. `auto` and `hybrid` fall back to
keywords if a model operation fails or takes more than 30 seconds; the tool result
reports that fallback. Explicit `semantic` tool calls return an error instead.
The Studio live-search adapter retains its existing semantic-to-FTS fallback and
includes a `warning` field when it degrades.

Only one model operation may remain outstanding per live store. Cancelling or
timing out a search does not forcibly terminate a native model call; later searches
use keywords while it remains busy. The isolated daemon worker owns no database
handles and cannot write late results into a closed session. Session database work
still drains in its original serial order. Keyword and vector indexing keep
separate progress, allowing semantic indexing to catch up after keyword searches.

Tool args (shape; concrete syntax depends on your `tool_format`; default bracket shown):

```
[/search_memory]
@@query=auth bug
@@mode=hybrid
@@k=5
@@agent=swe
[search_memory/]
```

For RAG over *external* sources, build a custom tool or a [plugin](plugins.md) that calls your vector store.

## Configuring memory in a creature

```yaml
memory:
  embedding:
    provider: model2vec       # or sentence-transformer, api, auto
    model: "@retrieval"       # preset or HF path
```

Agents with this block indexed automatically as events land; no `kt embedding` call needed. Agents without it keep an unembedded session (still FTS-searchable).

## Inspecting programmatically

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

## Troubleshooting

- **`No vectors in index`.** You ran `--mode semantic` without first running `kt embedding`. Either build the index or use `--mode fts`.
- **Slow `kt embedding`.** `sentence-transformer` is CPU-bound by default. Install torch with CUDA, or drop to `model2vec`.
- **Provider install fails.** `kt embedding --provider model2vec` has no native deps and works everywhere. `sentence-transformer` needs `torch`; `api` needs the provider SDK (`openai`, `google-generativeai`, etc.).
- **Hybrid mode returns noise.** Lower `-k` and prefer `semantic` over `hybrid` for paraphrase-heavy queries; prefer `fts` for literal-phrase queries.
- **search_memory returns nothing.** The session's embedding config is missing or the session started before you added memory config; rebuild with `kt embedding`.

## See also

- [Sessions](sessions.md): the `.kohakutr` format that memory sits on top of.
- [Plugins](plugins.md): seamless-memory plugin pattern (`pre_llm_call` retrieval).
- [Reference / CLI](../reference/cli.md): `kt embedding`, `kt search` flags.
- [Concepts / memory and compaction](../concepts/modules/memory-and-compaction.md): the design rationale.
