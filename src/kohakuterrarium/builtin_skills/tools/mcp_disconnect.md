---
name: mcp_disconnect
description: Disconnect from an MCP server
category: builtin
tags: [mcp, integration]
---

# mcp_disconnect

Disconnect from a connected MCP server. The server's tools will no longer
be available via `mcp_call`.

## Arguments

| Arg    | Type   | Description                                 |
| ------ | ------ | ------------------------------------------- |
| server | string | Name of the server to disconnect (required) |

## Example

```
mcp_disconnect(server="github")
```
