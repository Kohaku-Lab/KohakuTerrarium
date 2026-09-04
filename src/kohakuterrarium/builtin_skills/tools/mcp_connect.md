---
name: mcp_connect
description: 'Connect to an MCP server by name or definition. Not for calling its tools - use mcp_call.'
category: builtin
tags: [mcp]
---

# mcp_connect

Opens a session to an MCP server so its tools become callable.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| name | string | yes | Registry name, or a name for an inline definition |
| command | string | no | Stdio server command |
| args | array | no | Arguments for the command |
| url | string | no | HTTP server URL |

## Behavior

- Give either a registry `name`, or `command`/`args` for stdio, or `url` for
  HTTP.
- Connecting is idempotent; an already-connected server returns success.

## Limits

- Failures are contained to this call and do not affect other servers.
