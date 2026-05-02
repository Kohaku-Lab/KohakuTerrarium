---
name: scratchpad
description: Read/write session-scoped key-value working memory
category: builtin
tags: [memory, session, planning]
---

# scratchpad

Read/write session-scoped working memory. Data persists within the current
session but is cleared on restart. Unlike file-based memory, scratchpad is
framework-managed, structured (key-value), and cheap (no LLM cost to read/write).

## WHEN TO USE

- Storing a multi-step plan before executing it
- Tracking progress through a complex task (completed steps, remaining work)
- Saving notes or intermediate results during multi-step reasoning
- Keeping a todo list or checklist across multiple turns
- Remembering decisions or constraints discovered mid-task
- Accumulating findings from searches before synthesizing

## HOW TO USE

```
tool call: scratchpad(
  action: set
  key: key_name
value content here
)
```

Supported actions: `get`, `set`, `delete`, `list`, `clear`.

## Arguments

| Arg    | Type    | Description                                                  |
| ------ | ------- | ------------------------------------------------------------ |
| action | @@arg   | Action to perform: get, set, delete, list, or clear          |
| key    | @@arg   | Key name (required for get, set, delete)                     |
| value  | content | Value to store (required for set; everything after the args) |

## Examples

Store a plan:

```
tool call: scratchpad(
  action: set
  key: plan
1. Read the config file
2. Identify the broken handler
3. Fix the handler
4. Run tests
)
```

Retrieve a value:

```
tool call: scratchpad(
  action: get
  key: plan
)
```

List all stored keys:

```
tool call: scratchpad(
  action: list
)
```

Delete a key:

```
tool call: scratchpad(
  action: delete
  key: plan
)
```

Clear all data:

```
tool call: scratchpad(
  action: clear
)
```

## Output Format

- **get**: Returns the stored value, or "Key 'x' not found" if missing
- **set**: Returns "Set 'key_name'" as confirmation
- **delete**: Returns "Deleted 'key_name'" or "Key 'x' not found"
- **list**: Returns a bullet list of all keys, or "(empty)"
- **clear**: Returns "Scratchpad cleared"

## LIMITATIONS

- Session-scoped only (data is lost on restart)
- Keys are flat strings (no nested namespaces)
- Values are stored as plain text (no binary data)

## TIPS

- Use descriptive key names: `plan`, `progress`, `findings`, `constraints`
- Multi-line values work well for plans and checklists
- Use `list` to review what you have stored before deciding next steps
- Scratchpad contents are auto-injected into context as "Working Memory"
- Prefer scratchpad over repeating information in your messages
- Use `clear` when starting a fresh subtask to avoid stale data
