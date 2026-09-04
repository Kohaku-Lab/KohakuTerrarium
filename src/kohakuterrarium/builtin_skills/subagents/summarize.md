---
name: summarize
description: 'Condense a long conversation or document into a structured summary. Not for reviewing quality - use critic.'
category: subagent
tags: [context]
---

# summarize

Produces a structured summary of supplied material.

## Task shape

Say what the summary is for and what must survive it. Detail that is not named
as important may be dropped.

## Returns

A structured summary: goal, decisions, progress, and key facts.

## Limits

- Read-only. Automatic context compaction is a separate mechanism.
