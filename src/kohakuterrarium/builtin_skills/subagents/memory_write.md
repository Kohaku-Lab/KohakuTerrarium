---
name: memory_write
description: 'Record a durable note into session memory. Not for files the user should see - use write.'
category: subagent
tags: [memory]
---

# memory_write

Writes a note into session memory for later recall.

## Task shape

Give the fact and why it will matter later. Notes without context are not
findable.

## Returns

Confirmation of what was stored.

## Limits

- Session-scoped; not a substitute for writing a file.
