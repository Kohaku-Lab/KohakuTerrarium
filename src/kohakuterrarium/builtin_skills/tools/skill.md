---
name: skill
description: Invoke a procedural skill by name and return its instructions
category: builtin
tags: [skills, procedures, guidance]
---

# skill

Invoke a procedural skill through the normal tool surface.

Skills are markdown how-to bundles discovered from project, user, or package skill directories. This tool returns the selected skill's instructions so you can follow them in subsequent actions.

## Arguments

| Arg | Type | Description |
|-----|------|-------------|
| name | string | Skill name to invoke (required) |
| arguments | string | Optional free-form arguments or task context for the skill |

## Behavior

- Looks up `name` in the active session skill registry.
- Verifies that the skill exists and is enabled.
- Returns the skill body as tool output.
- Includes `arguments` in the rendered skill context when provided.

## WHEN TO USE

- When the current task matches a listed procedural skill.
- When a path-based skill hint says a skill may apply.
- When the user asks you to follow or use a named skill.

## Related

- Use `info(name="<skill-name>")` to read details for a specific skill without invoking it.
- Users can trigger skills directly with `/<skill-name> [args]`.
