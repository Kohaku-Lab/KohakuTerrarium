---
name: explore
description: 'Investigate a workspace question and return file evidence, read-only. Dispatch it whenever an answer means reading across many files; hand it the scope.'
category: subagent
tags: [search, exploration, analysis]
---

# explore

A lightweight, read-only investigation in a fresh context. The parent retains
planning and decision-making; graph creatures own substantial delegated work.

## Task shape

Supply the question, relevant paths or search boundaries, existing findings, and
what remains uncertain. The sub-agent does not see the parent conversation.

## Returns

**Answer**, **Evidence** with paths/symbols/line references, and **Limits** when
coverage is partial. A failed search reports its scope, not a claim of absence.

## Limits

- Inspection tools only: glob, grep, read, tree. No shell, writes, web, or delegation.
- Budget plugin: 8/12 soft/hard model calls and 24/40 tool calls. Stop expanding
  scope at the soft limit; at the hard limit tools are blocked and text reporting
  remains available. The absolute loop cap is 14 model calls.
- Emergency whole-run timeout: 600 seconds. No automatic compaction or extension
  is enabled by this preset.
- The model follows the configured sub-agent mapping, otherwise the parent.
- Return useful partial findings rather than pretending the investigation is complete.
