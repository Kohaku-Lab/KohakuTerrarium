---
name: summarize
description: Content summarization sub-agent
category: subagent
tags: [summarize, condense, analysis]
---

# summarize

Sub-agent for condensing long content into concise, actionable summaries.

## WHEN TO USE

- Tool output or file content is too long for the controller context
- Need to distill key findings from verbose results
- Combining information from multiple sources into a brief report
- Reducing noise before presenting results to the user

## WHEN NOT TO USE

- Content is already short (a few lines)
- You need the full unabridged content for precise edits
- Exact wording or formatting matters (e.g., copying code verbatim)

## HOW TO USE

```
tool call: summarize(
content or task description
)
```

## Arguments

| Arg  | Type    | Description                                                      |
| ---- | ------- | ---------------------------------------------------------------- |
| body | content | The content to summarize, or a task describing what to summarize |

## Examples

```
tool call: summarize(
Summarize the following build output:
<long build log here>
)
```

```
tool call: summarize(
Read and summarize src/kohakuterrarium/core/controller.py
)
```

```
tool call: summarize(
Condense these search results into key findings:
<grep/glob output>
)
```

```
tool call: summarize(
What are the main points in docs/architecture.md?
)
```

## CAPABILITIES

The summarize sub-agent has access to:

- `read` - Read file contents

It will autonomously:

1. Process the provided content or read specified files
2. Identify the most important information
3. Produce a structured summary with key points

## OUTPUT

Returns a structured summary including:

- Brief overview (1-2 sentences)
- Bulleted key points
- Additional details when relevant (file paths, line numbers, caveats)

## LIMITATIONS

- Read-only (cannot modify files)
- Limited turns (max 3) - best for straightforward summarization
- Cannot search the codebase (use explore for that, then summarize results)
- May lose nuance when condensing highly technical content

## TIPS

- Include the content directly in the body when possible (avoids extra file reads)
- Specify what aspects matter most (e.g., "focus on error messages" or "highlight API changes")
- Chain with explore: use explore to gather information, then summarize the results
