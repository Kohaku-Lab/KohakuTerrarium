---
name: mcp_list
description: 'List connected MCP servers and the tools they expose. Not for calling one - use mcp_call.'
category: builtin
tags: [mcp]
---

# mcp_list

Reports the connected servers and their available tools.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| server | string | no | Restrict the listing to one server |

## Behavior

- MCP tools are reached through this indirection rather than injected into
  your tool list, so the prompt stays small however many servers attach.
