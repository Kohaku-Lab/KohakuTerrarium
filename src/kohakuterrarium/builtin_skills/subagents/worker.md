---
name: worker
description: General-purpose implementation worker sub-agent
category: subagent
tags: [implementation, coding, bugfix, refactor]
---

# worker

Autonomous sub-agent for implementing code changes, fixing bugs, and refactoring.

## WHEN TO USE

- A specific coding task needs to be carried out (write, fix, refactor)
- Bug needs to be located and fixed across one or more files
- Code needs refactoring while preserving behavior
- Changes require reading context, editing, and verifying with tests

## WHEN NOT TO USE

- You only need to read or search code (use explore instead)
- Task is a single trivial edit you can do directly
- You need long-running or interactive work (exceeds turn/timeout limits)

## HOW TO USE

```
tool call: worker(
task description
)
```

## Arguments

| Arg  | Type    | Description                                            |
| ---- | ------- | ------------------------------------------------------ |
| body | content | Task description (what to implement, fix, or refactor) |

## Examples

```
tool call: worker(
Add input validation to the parse_config function in src/config.py
)
```

```
tool call: worker(
Fix the off-by-one error in pagination logic in src/api/routes.py
)
```

```
tool call: worker(
Refactor the UserAuth class to separate token generation from validation
)
```

```
tool call: worker(
Add error handling for network timeouts in src/client.py and write tests
)
```

## CAPABILITIES

The worker sub-agent has access to:

- `read` - Read file contents
- `write` - Create or overwrite files
- `edit` - Make targeted edits to existing files
- `bash` - Run shell commands (tests, linters, builds)
- `glob` - Find files by pattern
- `grep` - Search file contents

It will autonomously:

1. Read and understand relevant code
2. Make targeted modifications
3. Run tests or checks to verify changes
4. Report what was changed and why

## OUTPUT

Returns a structured summary including:

- Task that was performed
- Files modified with descriptions of changes
- Test results (if applicable)
- Any concerns or follow-up items

## LIMITATIONS

- Read-write (makes real changes to the codebase)
- Limited to 15 turns and 300s timeout
- Returns text summary (not structured data)
- Cannot handle interactive or multi-session workflows

## TIPS

- Give clear, specific task descriptions with file paths when known
- Mention any constraints (e.g., "don't change the public API")
- For multi-file changes, describe the desired end state, not just individual edits
- Use critic after worker to verify the changes
