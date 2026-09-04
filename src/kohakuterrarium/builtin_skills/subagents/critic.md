---
name: critic
description: Review code, a plan, or output and report problems. Use before shipping something consequential. Not for fixing what it finds - use worker.
category: subagent
tags: [review]
---

# critic

Reviews a supplied artifact and reports defects, ranked by severity.

## Task shape

Include the artifact or its location, what it is meant to do, and the standard
to judge it against. Without a standard, review degrades into style opinions.

## Returns

Findings with locations and severity. No edits.

## Limits

- Read-only.
- Reports problems; it does not resolve disagreements about requirements.
