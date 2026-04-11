# Sessions

KohakuTerrarium persists sessions to `.kohakutr` files (SQLite via KohakuVault). A session captures everything needed to inspect, search, and resume an agent or terrarium.

## What Gets Saved

| Data | Description |
|------|-------------|
| Conversation snapshots | Raw `list[dict]` via msgpack, preserving full `tool_calls` metadata |
| Event log | Append-only log of every text chunk, tool call, tool result, trigger fire, token usage |
| Scratchpad state | Key-value pairs from the `scratchpad` tool |
| Token usage | Cumulative prompt/completion/total token counts per agent |
| Sub-agent conversations | Saved before sub-agent destruction, including run metadata |
| Channel messages | All messages sent through channels (via `on_send` callbacks) |
| Resumable triggers | Trigger state for restoring autonomous behavior |

## Automatic Session Creation

When you pass `--session` to `kt run` or `kt terrarium run`, a `.kohakutr` file is created in `.kohaku/sessions/`:

```bash
kt run examples/agent-apps/swe_agent --session
# Creates: .kohaku/sessions/swe_agent_20260404_120000.kohakutr
```

The filename includes the agent (or terrarium) name and a timestamp.

## Resuming a Session

```bash
# Resume a specific session file
kt resume .kohaku/sessions/swe_agent_20260404_120000.kohakutr

# Resume the most recent session
kt resume --last
```

Resume rebuilds the agent from its original config and injects the saved conversation. The agent picks up right where it left off, with full context of previous tool calls and responses.

## Listing and Inspecting Sessions

```bash
# List all saved sessions
kt list
```

### Inspect Script

For deeper inspection, use the included script:

```bash
# Show everything (events, conversations, metadata)
python scripts/inspect_session.py session.kohakutr --all

# Full-text search across all session content
python scripts/inspect_session.py session.kohakutr --search "auth bug"
```

The inspect script reads the `.kohakutr` SQLite file and prints formatted event logs, conversation history, token usage, and channel messages.

## Terrarium Sessions

When a terrarium runs with `--session`, the `KohakuManager` creates a single `SessionStore` for the entire terrarium. This store records:

- Events from every creature (keyed by creature name)
- Conversation snapshots for each creature independently
- All channel messages across the terrarium
- Per-creature scratchpad and token usage

Each creature gets a `SessionOutput` module added as a secondary output on its output router. This captures events without modifying the creature's processing loop.

```bash
# Start terrarium with session
kt terrarium run terrariums/swe_team/ --session

# Resume terrarium session
kt resume .kohaku/sessions/swe_team_20260404_120000.kohakutr
```

The resume function detects whether a `.kohakutr` file contains an agent or terrarium session and rebuilds accordingly.

## Web Dashboard

A Vue 3 web frontend provides real-time management of agents and terrariums:

```bash
# Start API server
cd KohakuTerrarium && python -m kohakuterrarium.api.main

# Start frontend dev server
npm run dev --prefix src/kohakuterrarium-frontend
```

Features: terrarium topology graph, multi-tab chat (root + creatures + channels), real-time streaming, sub-agent tool activity, channel message feed, token usage tracking, dark/light mode with gemstone color theme.

The API server uses `KohakuManager` (from `src/kohakuterrarium/serving/`) which automatically records sessions when `session_dir` is configured. See [Concepts: Serving](../concepts/serving.md) for the serving layer architecture.

## Memory Search

Sessions support full-text (FTS5) and semantic (vector) search over the event history. This lets agents recall specific details from earlier in the conversation, even after context compaction.

### The `search_memory` Tool

Agents automatically have access to `search_memory` when a session is active. It searches the session's indexed event log.

Search modes:

| Mode | Description |
|------|-------------|
| `fts` | Keyword search via SQLite FTS5. Fast, good for identifiers, error codes, file paths. |
| `semantic` | Vector similarity search. Requires an embedding model. Good for natural-language queries. |
| `hybrid` | Combines FTS + semantic with reciprocal rank fusion. Best overall quality. |
| `auto` | Uses `hybrid` if vectors are available, otherwise falls back to `fts`. This is the default. |

Example tool call (bracket format):

```
[/search_memory]
query: authentication token refresh
mode: auto
k: 5
[search_memory/]
```

Results include the matched content, round number, timestamp, block type (user, text, tool), and relevance score.

### CLI: Searching Sessions

Search a session file from the command line:

```bash
# Basic search (uses FTS by default)
kt search .kohaku/sessions/my_session.kohakutr "auth bug"

# Semantic search with result limit
kt search .kohaku/sessions/my_session.kohakutr "how did we fix the login issue" --mode semantic -k 5

# Filter by agent name (useful for terrarium sessions)
kt search .kohaku/sessions/my_session.kohakutr "deploy" --agent swe
```

### CLI: Building Embeddings

Before semantic or hybrid search works, you need to build embeddings for a session:

```bash
# Build embeddings with the default provider (model2vec if installed)
kt embedding .kohaku/sessions/my_session.kohakutr

# Use a specific provider and model
kt embedding .kohaku/sessions/my_session.kohakutr --provider sentence-transformer --model jinaai/jina-embeddings-v5-text-nano

# Use OpenAI API embeddings with custom dimensions
kt embedding .kohaku/sessions/my_session.kohakutr --provider api --model text-embedding-3-small --dimensions 512
```

### Embedding Providers

| Provider | Description | Install |
|----------|-------------|---------|
| `model2vec` | Lightweight static embeddings (~8 MB, microsecond inference). Default when available. | `pip install model2vec` |
| `sentence-transformer` | Higher quality (Jina, Gemma, bge, etc.). Better for nuanced queries. | `pip install sentence-transformers` |
| `api` | OpenAI-compatible `/v1/embeddings` endpoint. Works with OpenAI, Google, Jina. | (none, uses httpx) |

FTS keyword search is always available without any embedding setup.

For embedding configuration in agent YAML, see the `memory.embedding` section in [Configuration Reference](configuration.md).

## Programmatic Usage

```python
from kohakuterrarium.session.store import SessionStore

# Open or create a session file
store = SessionStore(".kohaku/sessions/my_session.kohakutr")

# Record events
store.append_event("swe_agent", "text", {"content": "Hello"})

# Save conversation snapshot
store.save_conversation("swe_agent", messages)

# Save agent state
store.save_state("swe_agent", scratchpad={"key": "value"}, token_usage={"total": 1500})

# Full-text search across all content
results = store.search("authentication bug", k=10)

# Channel messages
store.save_channel_message("ideas", {"sender": "brainstorm", "content": "..."})
messages = store.get_channel_messages("ideas")

# Cleanup
store.flush()
store.close()
```

For the full `SessionStore` API, see [Python API Reference](../reference/python.md).

### Resume Functions

```python
from kohakuterrarium.session.resume import resume_agent, resume_terrarium, detect_session_type

# Detect session type
session_type = detect_session_type("session.kohakutr")  # "agent" or "terrarium"

# Resume
agent = resume_agent("session.kohakutr")
runtime = resume_terrarium("session.kohakutr")
```
