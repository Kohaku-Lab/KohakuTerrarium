---
name: mcp_connect
description: Connect to an MCP server at runtime
category: builtin
tags: [mcp, integration]
---

# mcp_connect

Connect to a new MCP server at runtime. Once connected, use `mcp_list`
to see available tools and `mcp_call` to invoke them.

## Arguments

| Arg             | Type   | Description                                                                |
| --------------- | ------ | -------------------------------------------------------------------------- |
| name            | string | A name for this server connection (required)                               |
| command         | string | Executable command for stdio transport                                     |
| args            | list   | Command arguments for stdio transport                                      |
| transport       | string | Optional transport override: `stdio`, `http` / `sse`, or `streamable_http` |
| url             | string | Server URL for SSE or streamable HTTP transport                            |
| env             | object | Environment variables for stdio transport                                  |
| connect_timeout | number | Optional connection timeout in seconds                                     |

Provide either `command` (stdio) or `url` (HTTP transport). If `url` is given without an explicit `transport`, `streamable_http` is used by default.

## Examples

```
mcp_connect(name="fs", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "./"])
mcp_connect(name="github", command="npx", args=["-y", "@modelcontextprotocol/server-github"])
mcp_connect(name="remote", url="https://mcp.example.com/mcp")
mcp_connect(name="legacy", transport="http", url="https://mcp.example.com/sse")
```

## Output

Shows the number of tools discovered and their names.
