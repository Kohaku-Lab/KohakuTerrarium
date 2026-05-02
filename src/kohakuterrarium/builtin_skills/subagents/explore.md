---
name: explore
description: Autonomous codebase exploration sub-agent
category: subagent
tags: [search, exploration, analysis]
license: internal
---

# explore

Autonomous sub-agent for codebase exploration and analysis.

## WHEN TO USE

- You don't know where relevant code is located
- Need to understand how something works across multiple files
- Complex search requiring multiple glob/grep/read operations
- Answering questions like "how does X work?" or "find all Y"

## WHEN NOT TO USE

- You already know the exact file to read
- Simple single-file operations
- Quick pattern match (use grep directly)

## HOW TO USE

```
tool call: explore(
task description
)
```

## Arguments

| Arg  | Type    | Description                                |
| ---- | ------- | ------------------------------------------ |
| body | content | Task description (what to find/understand) |

## Examples

```
tool call: explore(
Find all files related to authentication
)
```

```
tool call: explore(
How does the config loading system work?
)
```

```
tool call: explore(
Find where user permissions are checked
)
```

```
tool call: explore(
What modules import the database module?
)
```

## CAPABILITIES

The explore sub-agent has access to:

- `glob` - Find files by pattern
- `grep` - Search file contents
- `read` - Read file contents

It will autonomously:

1. Search for relevant files
2. Read and analyze contents
3. Follow imports/references
4. Return a summary of findings

## OUTPUT

Returns a summary of what was found, including:

- Relevant files discovered
- Key code sections
- How components connect

## LIMITATIONS

- Read-only (cannot modify files)
- Limited turns (may not explore everything)
- Returns text summary (not structured data)
