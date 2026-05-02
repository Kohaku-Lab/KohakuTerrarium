---
name: mcp_call
description: Call a tool on an MCP server
category: builtin
tags: [mcp, integration]
---

# mcp_call

Call a tool on a connected MCP server.

## Arguments

| Arg    | Type   | Description                                 |
| ------ | ------ | ------------------------------------------- |
| server | string | Name of the MCP server (required)           |
| tool   | string | Name of the tool to call (required)         |
| args   | object | Arguments to pass to the tool (default: {}) |

## Usage

Always use `mcp_list` first to discover available tools and their arguments.

```
mcp_call(server="filesystem", tool="read_file", args={"path": "/tmp/test.txt"})
mcp_call(server="github", tool="create_issue", args={"repo": "user/project", "title": "Bug fix"})
mcp_call(server="db", tool="query", args={"sql": "SELECT * FROM users LIMIT 10"})
```

## Output

Returns the tool's output as text. If the tool returns an error,
the output is prefixed with `[MCP Error]`.

## Common Errors

- "MCP server not connected" — use `mcp_connect` first or check `mcp_list`
- "Tool not found on server" — check tool name with `mcp_list(server="...")`
- "Invalid JSON in args" — ensure args is a valid object, not a string
