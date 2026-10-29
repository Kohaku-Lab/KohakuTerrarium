---
name: critic
description: 'Review code, a diff or a plan for concrete problems, read-only. Dispatch it after a change or before you ship; hand it the scope, requirements and diff.'
category: subagent
tags: [review]
---

# critic

A bounded independent review, not an implementation worker or full verifier.
Quick means narrow scope, not unsupported conclusions.

## Task shape

Supply the artifact or location, relevant requirements, and review boundaries.
For change reviews, include the diff in the task or a readable file. The preset
has no shell access and cannot obtain a git diff itself. Supply conversation
constraints explicitly; the sub-agent does not see the parent conversation.

## Returns

**Findings**, highest impact first, with locations, triggering conditions, and
consequences. **Coverage** states what was inspected and what remains unverified.
Uncertain concerns are labeled. No findings is a valid result, not proof that
the whole system is correct. Partial or blocked reviews are explicit.

## Limits

- Inspection tools only: read, glob, grep, tree. No modifications or delegation.
- Does not run builds or test suites; reading tests does not establish they pass.
- Budget plugin: 8/12 soft/hard model calls and 24/40 tool calls. The absolute
  loop cap is 14 model calls, leaving room for text reporting after tools stop.
- Emergency whole-run timeout: 600 seconds. No automatic compaction or extension
  is enabled by this preset.
- Inherits the parent's model unless the creature explicitly overrides it.
