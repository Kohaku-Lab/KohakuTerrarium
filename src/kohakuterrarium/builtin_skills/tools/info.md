---
name: info
description: Load the full documentation for a tool, sub-agent, or procedural skill by name. Not for running a skill - use skill.
category: builtin
tags: [docs]
---

# info

Returns the complete reference for one named function, including material the
system prompt never carries.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| name | string | yes | Tool, sub-agent, or skill name |

## Behavior

- Resolution order: built-in tool docs, built-in sub-agent docs, the creature's
  own `prompts/tools/` and `prompts/subagents/`, the tool instance, then
  procedural skills.
- Reading a tool's docs here is what unlocks any tool that requires a manual
  read before first use.
