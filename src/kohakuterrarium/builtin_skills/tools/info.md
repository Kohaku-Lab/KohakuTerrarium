---
name: info
description: Get full documentation for a tool or sub-agent by name
category: builtin
tags: [documentation, help]
---

# info

Get full documentation for any tool or sub-agent.

## Arguments

| Arg  | Type   | Description                                         |
| ---- | ------ | --------------------------------------------------- |
| name | string | Name of the tool or sub-agent to look up (required) |

## Behavior

Looks up documentation in this order:

1. Builtin tool docs (builtin_skills/tools/)
2. Builtin sub-agent docs (builtin_skills/subagents/)
3. Agent-local docs (prompts/tools/ or prompts/subagents/)
4. Tool instance's own get_full_documentation() method
5. Sub-agent config description

Returns an error if no documentation is found for the given name.

## WHEN TO USE

- When you need to understand a tool's full parameter set
- When you need to learn about edge cases or limitations
- Before using a tool that requires `info` first (tools marked with
  "Use info tool to read docs first" in their description)
- The tool list in your system prompt shows one-line descriptions;
  this gives you the complete reference.

## Output

Returns the full documentation content as markdown text.

## TIPS

- Some tools (like `edit`) require you to call `info` before first
  use. They will error until you do.
- Use `info` proactively when unsure about a tool's exact arguments.
