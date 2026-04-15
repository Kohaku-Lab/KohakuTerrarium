# Sessions

KohakuTerrarium persists sessions to `.kohakutr` files. A session captures everything needed to inspect, resume, search, and reuse past agent or terrarium work.

A session is not only a resume file.
It is also a searchable knowledge store for prior work, similar in spirit to a RAG database built from your agent history.

## Where sessions live

By default, sessions are stored under:

```text
~/.kohakuterrarium/sessions/
```

KohakuTerrarium creates session files automatically unless disabled.

## What gets saved

A session stores much more than a transcript.

| Data | Description |
|------|-------------|
| Conversation history | Message history, including tool-call metadata |
| Event log | Streaming text, tool calls, tool results, trigger activity, and runtime events |
| Scratchpad state | Key-value state from the scratchpad tool |
| Sub-agent state | Saved sub-agent runs and related metadata |
| Channel messages | Messages sent through terrarium channels |
| Jobs | Background tool and sub-agent job records |
| Resumable triggers | Trigger state for restoring autonomous behavior |
| Config metadata | Session type, config path, runtime metadata, topology details |

Session metadata also records useful runtime context such as the config path, working directory, host and Python version, and whether the session came from a standalone creature or a terrarium.

## Automatic session creation

When sessions are enabled, the runtime creates `.kohakutr` files automatically.

The filename pattern is based on the config name plus a generated ID, for example:

```text
swe_team_a1b2c3d4e5f6.kohakutr
```

This applies to both standalone creature runs and terrarium runs.

## Resume a session

```bash
kt resume
kt resume --last
kt resume swe_team
kt resume ~/.kohakuterrarium/sessions/swe_team_a1b2c3d4e5f6.kohakutr
```

Resume rebuilds the runtime from the original config and restores the saved operational state.

For standalone creatures, this means restoring the creature conversation and runtime state.
For terrariums, this means restoring the terrarium session, including root events, creature events, and channel history.

Terrarium sessions are resumed into the terrarium TUI.

## Creature sessions vs terrarium sessions

### Creature sessions

A standalone creature session captures the creature's runtime history and state, including:

- conversation history
- tool calls and results
- scratchpad state
- sub-agent runs
- resumable triggers

### Terrarium sessions

A terrarium session captures the whole multi-creature runtime, including:

- root-agent events if a root exists
- events from every creature
- conversation snapshots for each creature
- channel messages across the terrarium
- per-creature state and metadata
- terrarium topology metadata such as creature names and declared channels

This makes terrarium sessions especially useful for later inspection and memory search.

## Searchable session history

Session history is stored not only for resume, but also as a searchable knowledge base.
Agents can search prior work directly through the built-in memory search tools, and users can search sessions from the CLI.

### Search modes

KohakuTerrarium supports several search modes over session history:

| Mode | Description |
|------|-------------|
| `fts` | Full-text keyword search. Good for identifiers, filenames, error strings, and exact terms. |
| `semantic` | Vector similarity search. Good for natural-language queries and conceptual recall. |
| `hybrid` | Combines FTS and semantic search. |
| `auto` | Uses hybrid when vectors are available, otherwise falls back to FTS. This is the default behavior. |

### Agent memory search

When a session is active, agents can use the built-in memory search tools to retrieve useful history from prior work.

This is especially useful when:

- earlier work has been compacted out of the live context
- a previous session already solved a similar problem
- a terrarium produced useful intermediate results you want to recall later

### CLI session search

You can search saved sessions from the command line:

```bash
kt search "auth bug"
kt search "deploy failure after websocket reconnect" --mode fts
kt search "a similar incident involving retries and backoff" --mode semantic
```

Search defaults to `auto`, not plain FTS.

Depending on your CLI usage, you can also filter or narrow searches by session or agent when supported by the command.

## Embeddings for semantic search

Semantic and hybrid search use embeddings.

Check status or rebuild embeddings with:

```bash
kt embedding status
kt embedding rebuild
```

Embedding provider selection defaults to `auto`.
That mode prefers locally available providers such as `model2vec`, then `sentence-transformer`, before falling back to no vector support.

### Common embedding providers

| Provider | Description |
|----------|-------------|
| `model2vec` | Lightweight local embeddings and a common auto-selected default when installed |
| `sentence-transformer` | Higher-quality local semantic embeddings |
| `api` | OpenAI-compatible embedding APIs |

FTS search remains available even if no embedding provider is installed.

## Inspecting sessions

For deeper inspection, use the included script:

```bash
python scripts/inspect_session.py ~/.kohakuterrarium/sessions/my_session.kohakutr --all
python scripts/inspect_session.py ~/.kohakuterrarium/sessions/my_session.kohakutr --search "auth bug"
```

This is useful for examining event logs, metadata, channel messages, and stored conversation history.

## Web and service integration

The serving layer and web UI can also use session storage.

The API/server side uses the same session directory model and can replay saved runtime history into the UI. For terrariums, this includes:

- root tab state
- creature tabs
- `#channel` tabs for channel history

See [Serving](../concepts/serving.md) and [HTTP API](../reference/http.md).

## Programmatic usage

```python
from kohakuterrarium.session.store import SessionStore

store = SessionStore("my_session.kohakutr")

# Search stored history
results = store.search("authentication bug", k=10)

# Flush and close when done
store.flush()
store.close()
```

For the full API surface, see [Python API Reference](../reference/python.md).

## Why this matters

Sessions are one of the reasons KohakuTerrarium feels like a real agent runtime instead of only a prompt runner.

They let you:

- resume work later
- inspect what happened operationally
- search prior runs like a knowledge base
- let agents recall useful history directly
- preserve both standalone creature behavior and terrarium-level collaboration history
