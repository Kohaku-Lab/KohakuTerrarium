---
name: mcp_list
description: List MCP servers and their tools
category: builtin
tags: [mcp, integration]
---

# mcp_list

List connected MCP servers and their available tools.

## Arguments

| Arg    | Type   | Description                                   |
| ------ | ------ | --------------------------------------------- |
| server | string | (optional) Server name for detailed tool info |

## Behavior

Without arguments: shows overview of all connected servers with tool names.

With `server=`: shows detailed tool info including argument schemas.

## Usage

Call this BEFORE `mcp_call` to discover what tools are available:

1. `mcp_list()` — see all servers and their tools
2. `mcp_list(server="github")` — see detailed args for github tools
3. `mcp_call(server="github", tool="create_issue", args={...})` — call a tool

## Output Format

Overview:

```
Connected MCP servers:

  github (stdio, connected, 5 tools)
    - create_issue — Create a GitHub issue
    - list_repos — List repositories
    ...
```

Detailed (with server=):

```
MCP server: github
Tools (5):

  create_issue
    Create a GitHub issue
    - repo: string (required)
    - title: string (required)
    - body: string
```
