---
name: memory_read
description: 'Search session memory and report what it found. Not for searching files - use grep.'
category: subagent
tags: [memory]
---

# memory_read

Searches recorded session memory for information relevant to a question.

## Task shape

State what you are trying to recall and roughly when it happened.

## Returns

Relevant recalled passages, or an explicit statement that nothing matched.

## Limits

- Reads only what was recorded in this session.
