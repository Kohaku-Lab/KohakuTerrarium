---
title: MCP
summary: Connect Model Context Protocol servers (stdio / SSE / streamable HTTP) and surface their tools to your creatures.
tags:
  - guides
  - mcp
  - integration
---

# MCP

To expose KT's own tools and local delegation to external clients, see the [MCP server guide](mcp-server.md).

For readers connecting MCP (Model Context Protocol) servers to a creature.

MCP is a client-server protocol that exposes tools (and other primitives) over stdio or HTTP. KohakuTerrarium is the client: you register a server in config, the framework spawns the subprocess or opens the HTTP session, and the server's tools become callable from the agent through a small set of meta-tools.

Concept primer: [tool](../concepts/modules/tool.md). MCP tools are "just tools," surfaced dynamically.

## Two places to declare servers

### Per-agent

Inside `config.yaml`:

```yaml
mcp_servers:
  - name: sqlite
    transport: stdio
    command: mcp-server-sqlite
    args: ["/var/db/my.db"]
  - name: docs_api
    transport: streamable_http
    url: https://mcp.example.com/mcp
    connect_timeout: 20
    headers:
      Authorization: "Bearer ${DOCS_API_KEY}"
```

`headers` apply to the `sse` and `streamable_http` transports; `env` applies to `stdio`.

Only this creature connects to these servers.

### Global

In `~/.kohakuterrarium/mcp_servers.yaml`:

```yaml
- name: sqlite
  transport: stdio
  command: mcp-server-sqlite
  args: ["/var/db/my.db"]

- name: filesystem
  transport: stdio
  command: npx
  args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/me/projects"]
```

Managed interactively:

```bash
kt config mcp list
kt config mcp add              # interactive: transport (stdio, streamable_http, sse), command, args, env, url, headers
kt config mcp edit sqlite
kt config mcp delete sqlite
```

Global servers are available to any creature that references them.

Registry entries may use `${VAR}` and `${VAR:default}` in `command`, `args`,
`env`, `url` and `headers`, so a token does not have to live in the file:

```yaml
- name: sandbox
  transport: streamable_http
  url: http://127.0.0.1:8765/mcp
  headers:
    Authorization: "Bearer ${SANDBOX_TOKEN}"
```

The markers are expanded from the KT process environment each time the entry
is turned into a connection: when a creature starts, when `mcp_connect` names a
registered server, and when Settings > MCP > **Test connection** runs. The file
and the `GET /api/settings/mcp` response keep the raw `${VAR}` text, never the
value. A variable that is not set and has no default stops that server with
`environment variable SANDBOX_TOKEN is not set (used in headers of server
sandbox)` instead of sending the literal `${SANDBOX_TOKEN}`; the creature still
starts without it. `${VAR:}` means "empty when unset". An inline
`mcp_connect` definition written by the model is used as given and is not
expanded.

## Transports

- **stdio**: launches a subprocess (`command` + `args` + `env`). Best for local servers, low latency, isolated-per-agent process lifetime.
- **streamable_http**: opens a modern streamable HTTP session to `url` (usually ending in `/mcp`). This is what current remote servers speak, including FastMCP's `streamable_http_app()`.
- **sse**: opens a legacy SSE session to `url` (usually ending in `/sse`). `http` is the old name of this transport and still works.

Pick stdio for local MCP servers (sqlite, filesystem, git), `streamable_http` for remote servers, and `sse` only when the server only exposes legacy SSE.

Every editor offers the same three choices: the web UI (Settings > MCP, both the
Add form and the Edit dialog: "stdio (subprocess)", "Streamable HTTP
(recommended for remote)", "SSE (legacy)"), `kt config mcp add`, and the rich
CLI and ktui settings screens. An existing `http` entry is shown as SSE. The
HTTP API accepts `stdio`, `streamable_http`, `sse` and `http`, and rejects
anything else with `422`.

## How MCP tools reach the LLM

Once a server is connected, KohakuTerrarium surfaces its tools through **meta-tools**:

- `mcp_list`: list available MCP tools across all connected servers.
- `mcp_call`: call a specific MCP tool by name with args.
- `mcp_connect` / `mcp_disconnect`: runtime connection management.

The system prompt gets an "Available MCP Tools" section listing every server's tools (name + one-line description). The LLM then calls `mcp_call` with `server`, `tool`, and `args`. In the default bracket format that reads:

```
[/mcp_call]
@@server=sqlite
@@tool=query
@@args={"sql": "SELECT 1"}
[mcp_call/]
```

Swap to `xml` or `native` via [`tool_format`](creatures.md) if preferred. The semantics are the same: `mcp_call` still takes `server`, `tool`, and `args`, and the native function schema now reflects those fields directly.

You don't need to wire each MCP tool individually; the meta-tool approach keeps the controller's tool list compact.

## Listing connected servers

For a specific agent:

```bash
kt mcp list --agent path/to/creature
```

Prints name, transport, command, URL, args, env keys.

## Programmatic use

```python
from kohakuterrarium.mcp import MCPClientManager, MCPServerConfig

manager = MCPClientManager()
await manager.connect(MCPServerConfig(
    name="sqlite",
    transport="stdio",
    command="mcp-server-sqlite",
    args=["/tmp/db.sqlite"],
))

tools = manager.get_server_tools("sqlite")
result = await manager.call_tool("sqlite", "query", {"sql": "SELECT 1"})
await manager.disconnect("sqlite")
```

The agent's runtime uses this under the hood.

`call_tool` returns the result as text: text parts joined by newlines, binary
parts shown as `[binary data: N bytes]`, and an `[MCP Error] ` prefix when the
server marks the result as an error. `call_tool_result` takes the same
arguments and returns the raw `mcp.types.CallToolResult` (structured content,
images, the `isError` flag) for callers that want to render it themselves.
Both raise `ValueError` when the server is not connected or does not list the
tool. After `connect`, `MCPServerInfo.instructions` holds the instructions the
server sent in its `initialize` result (an empty string when it sent none).
The framework does not add these instructions to the system prompt.

## Troubleshooting

A failed connect raises one typed error (`kohakuterrarium.errors.MCPConnectionError`,
`MCPTimeoutError` or `MCPCommandNotFoundError`, all `MCPError`) whose message
names the server, its URL (without query string or credentials) and the cause.
The Settings > MCP **Test connection** button, `mcp_connect` and the agent log
show the same text. An HTTP error status or a refused connection ends the
attempt at once instead of waiting for `connect_timeout`. Header values never
appear in messages or logs.

| Message | What to do |
| --- | --- |
| `HTTP 401 Unauthorized from <url>; check the headers/token` (or 403) | The `Authorization` header is missing or wrong. If it uses `${VAR}`, check the variable is set for the KT process. |
| `HTTP 400 Bad Request from <url>; the server may speak streamable HTTP: try the streamable_http transport` | The entry uses `sse` / `http` but the server only speaks streamable HTTP. |
| `HTTP 404 Not Found from <url>; check the URL path (for example /mcp)` | Wrong endpoint path. FastMCP serves streamable HTTP at `/mcp` and SSE at `/sse`. |
| `HTTP 5xx ...; server error: check the MCP server's logs` | The server failed while answering. |
| `cannot connect to <url>: <reason>` | Nothing listens at that host and port, or DNS failed. |
| `TLS handshake with <url> failed: <reason>` | Certificate or TLS problem. |
| `timed out after Ns; last HTTP response was ...` | The server answered but never finished the MCP handshake. |
| `command not found: <command>` | A stdio server whose `command` is not on `PATH`. |
| `the server process exited before the handshake finished` | A stdio server crashed on start. Run its command in a shell to see its error. |

- **Server not connecting (stdio).** Run `kt config mcp list` to see the resolved command. Try it in a shell first (`mcp-server-sqlite /path/to/db`) and check the server prints its handshake.
- **Server not connecting (http / sse).** Verify the URL is SSE-compatible. Some servers expose both `/sse` and `/ws`; KohakuTerrarium's `http` / `sse` transport expects the SSE endpoint.
- **Server not connecting (streamable_http).** Verify the URL points at the server's streamable HTTP endpoint (for example FastMCP's `streamable_http_app()` route) rather than a legacy SSE path.
- **Tool not found.** The meta-tools list is computed at connection time. Reconnect (`mcp_disconnect` + `mcp_connect`) if the server hot-added tools.
- **Env vars not substituted.** MCP config supports `${VAR}` and `${VAR:default}`, same as creature configs. The variable must be set in the environment of the process that runs KT (the `kt serve` / `kt app` process for the web UI), not only in your shell.
- **Server crashes mid-session.** Stdio servers respawn on next `mcp_call`. Check the server's own logs.

## See also

- [Configuration](configuration.md): `mcp_servers:` field.
- [Reference / CLI](../reference/cli.md): `kt config mcp`, `kt mcp list`.
- [Concepts / tool](../concepts/modules/tool.md): why MCP tools aren't treated specially.
