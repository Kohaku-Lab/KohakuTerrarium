---
name: search_memory
description: Search session history (keyword or semantic)
category: builtin
tags: [memory, search, session]
---

# search_memory

Search session history for relevant context from earlier in the conversation.
Use when you need to recall details that may have been compacted from context.

## Arguments

| Arg   | Type    | Description                                                           |
| ----- | ------- | --------------------------------------------------------------------- |
| query | string  | Search query (required)                                               |
| mode  | string  | Search mode: "fts", "semantic", "hybrid", or "auto" (default: "auto") |
| k     | integer | Max results to return (default: 5)                                    |
| agent | string  | Filter results by agent name (optional)                               |

## Search Modes

- **fts**: Full-text keyword search. Best for exact identifiers, file paths,
  error codes, and specific strings.
- **semantic**: Meaning-based vector search. Best for conceptual queries like
  "how did we fix the authentication bug?" or "what was the decision about
  the database?".
- **hybrid**: Combines both FTS and semantic results. Best overall recall.
  Default when vector embeddings are available.
- **auto**: Uses hybrid if vectors have been indexed, falls back to FTS
  otherwise.

## WHEN TO USE

- Recalling details from earlier in a long conversation
- Finding previous tool results or decisions
- Looking up specific errors or file paths discussed earlier
- Recovering context that was lost to compaction

## Output

Each result shows:

- Round number (which conversation turn)
- Block type (user, text, tool, trigger)
- Tool name (if applicable)
- Agent name (if multi-agent)
- Time ago
- Content excerpt (truncated to 500 chars)

## LIMITATIONS

- Requires a session store to be attached
- Semantic search requires an embedding provider to be configured
- FTS search indexes on event text, not all metadata
- Results are excerpts; use round numbers to understand conversation flow

## TIPS

- Use FTS mode for exact strings: `search_memory(query="FileNotFoundError", mode="fts")`
- Use semantic mode for concepts: `search_memory(query="database migration plan", mode="semantic")`
- Filter by agent in multi-agent setups: `search_memory(query="review", agent="reviewer")`
