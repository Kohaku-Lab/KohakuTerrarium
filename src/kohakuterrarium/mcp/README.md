# mcp/

MCP (Model Context Protocol) client integration. Lets agents talk to
external MCP servers without injecting their tools directly into the
agent's tool list — agents call MCP tools through four meta-tools
(`mcp_list`, `mcp_call`, `mcp_connect`, `mcp_disconnect`).

## Responsibility

Owns the client-side connection manager for stdio + HTTP MCP servers,
and the meta-tool shims that route the agent's tool calls to the right
session. Does NOT try to mirror MCP tools as native framework tools — the
meta-tool indirection keeps the agent's tool list short and makes MCP
failures contained.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Package docstring |
| `client.py` | `MCPClientManager`, `MCPServerConfig`, `MCPServerInfo` — per-server `ClientSession`, tool discovery, lock-protected connect/disconnect, tool dispatch |
| `tools.py` | Four builtin tools (`@register_builtin`) — `mcp_list`, `mcp_call`, `mcp_connect`, `mcp_disconnect` |

## Dependency direction

Imported by: `core/agent.py` (conditionally — `Agent._init_mcp` creates and
attaches a manager when the config has `mcp_servers`), `builtins/tool_catalog`
(via `tools.py` at import time).

Imports: `mcp` SDK (optional runtime dep), `builtins.tool_catalog`
(`register_builtin`), `modules/tool/base`, `utils/logging`.

`mcp` (the SDK package) imports are deferred inside `connect()` so the
framework starts without it installed — only agents that configure MCP
servers need the dep.

## Key entry points

- `MCPClientManager()` — owns connections, tools, sessions
- `MCPClientManager.connect(MCPServerConfig)` — open session, discover tools
- `MCPClientManager.call_tool(server, name, args)` — dispatch
- `MCPListTool` / `MCPCallTool` / `MCPConnectTool` / `MCPDisconnectTool`
  (in `tools.py`) — the four meta-tools the agent uses

## Notes

- Tool names exposed to the LLM stay short (`mcp_list`, `mcp_call`) — the
  LLM first calls `mcp_list`, then `mcp_call` with the right
  `server`/`tool`/`args`. This keeps the system prompt small even with
  dozens of MCP tools available.
- `MCPClientManager._lock` serializes connect / disconnect; call_tool does
  not take the lock, so parallel tool calls work.
- Transport support: `stdio` (local process) and `http` (streamable HTTP).
  The `transport` field on `MCPServerConfig` selects which.
- The manager is attached to `Agent._mcp_manager`; `tools.py:_get_mcp_manager`
  reads it from the tool's `context.agent` at call time.

## See also

- `../core/agent.py` — `_init_mcp` (attaches the manager when configured)
- `../builtins/tools/` — meta-tools live alongside other builtins once
  `tools.py` is imported
