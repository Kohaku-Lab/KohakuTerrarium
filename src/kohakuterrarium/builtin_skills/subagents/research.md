---
name: research
description: Deep research sub-agent with web access
category: subagent
tags: [research, web, analysis, http]
---

# research

Autonomous sub-agent for deep research combining local codebase analysis with external web sources.

## WHEN TO USE

- You need information from both local files and external sources
- Answering questions that require web lookups (API docs, library usage, standards)
- Investigating topics that go beyond what is in the codebase
- Need to verify local assumptions against authoritative external sources
- Complex research requiring multiple search and fetch operations

## WHEN NOT TO USE

- Pure codebase exploration (use explore instead)
- You already know the answer from context
- Simple file reads or grep searches (use tools directly)
- Tasks that require modifying files

## HOW TO USE

```
tool call: research(
task description
)
```

## Arguments

| Arg  | Type    | Description                               |
| ---- | ------- | ----------------------------------------- |
| body | content | Research question or topic to investigate |

## Examples

```
tool call: research(
What authentication methods does the GitHub API v4 support?
)
```

```
tool call: research(
How does our config loader compare to standard TOML/YAML loading practices?
)
```

```
tool call: research(
Find the correct endpoint and payload format for the Slack Web API chat.postMessage method
)
```

```
tool call: research(
What are the best practices for async Python error handling in long-running event loops?
)
```

## CAPABILITIES

The research sub-agent has access to:

- `http` - Fetch external web pages, API docs, and data
- `read` - Read local file contents
- `grep` - Search local file contents by regex

It will autonomously:

1. Search local files for existing context
2. Fetch external sources for additional information
3. Cross-reference local and external findings
4. Synthesize a structured answer with citations

## OUTPUT

Returns a structured research report including:

- Restated research question
- Numbered findings with source citations
- Synthesized conclusion with confidence level
- Full list of references (file paths, URLs)

## LIMITATIONS

- Read-only (cannot modify files)
- External fetches depend on network availability
- Limited turns (may not exhaust all sources)
- Cannot execute code or run tests
- Returns text summary (not structured data)

## TIPS

- Be specific in your research question to get focused results
- Mention whether you need local codebase info, external sources, or both
- For pure codebase questions, prefer explore (it has glob access; research does not)
