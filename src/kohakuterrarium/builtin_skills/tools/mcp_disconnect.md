---
name: mcp_disconnect
description: 'Disconnect a connected MCP server by name.'
category: builtin
tags: [mcp]
---

# mcp_disconnect

Closes the session to one MCP server.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| name | string | yes | Connected server name |

## Behavior

- Its tools stop being callable until reconnected.
