---
name: skill
description: Run a procedural skill by name and return its instructions. Not for reading its docs without running it - use info.
category: builtin
tags: [skills]
---

# skill

Loads a procedural skill and returns its body for you to follow.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| name | string | yes | Skill name |
| arguments | string | no | Text passed through to the skill |

## Behavior

- Skills listed in the prompt's `## Skills` index are enabled; the index is
  byte-budgeted, so a skill missing from it is still callable by name.
- The returned body is instructions for you, not a completed result.
