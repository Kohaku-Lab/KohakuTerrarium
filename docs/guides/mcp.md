# MCP

For readers connecting MCP (Model Context Protocol) servers to a creature.

MCP is a client-server protocol that exposes tools (and other primitives) over stdio or HTTP. KohakuTerrarium is the client: you register a server in config, the framework spawns the subprocess or opens the HTTP session, and the server's tools become callable from the agent through a small set of meta-tools.

Concept primer: [tool](../concepts/modules/tool.md) — MCP tools are "just tools," surfaced dynamically.

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
    transport: http
    url: https://mcp.example.com/sse
    env:
      API_KEY: "${DOCS_API_KEY}"
```

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
kt config mcp add              # interactive: transport, command, args, env, url
kt config mcp edit sqlite
kt config mcp delete sqlite
```

Global servers are available to any creature that references them.

## Transports

- **stdio** — launches a subprocess (`command` + `args` + `env`). Best for local servers, low latency, isolated-per-agent process lifetime.
- **http** — opens an SSE/streaming HTTP session to `url`. Best for shared/remote servers, lets multiple creatures share one server.

Pick stdio for local MCP servers (sqlite, filesystem, git) and http for hosted ones.

## How MCP tools reach the LLM

Once a server is connected, KohakuTerrarium surfaces its tools through **meta-tools**:

- `mcp_list` — list available MCP tools across all connected servers.
- `mcp_call` — call a specific MCP tool by name with args.
- `mcp_connect` / `mcp_disconnect` — runtime connection management.

The system prompt gets an "Available MCP Tools" section listing every server's tools (name + one-line description). The LLM then calls `mcp_call` with `server`, `tool`, and `args`. In the default bracket format that reads:

```
[/mcp_call]
@@server=sqlite
@@tool=query
@@args={"sql": "SELECT 1"}
[mcp_call/]
```

Swap to `xml` or `native` via [`tool_format`](creatures.md) if preferred — the semantics don't change.

You don't need to wire each MCP tool individually — the meta-tool approach keeps the controller's tool list compact.

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

tools = await manager.list_tools("sqlite")
result = await manager.call_tool("sqlite", "query", {"sql": "SELECT 1"})
await manager.disconnect("sqlite")
```

The agent's runtime uses this under the hood.

## Troubleshooting

- **Server not connecting (stdio).** Run `kt config mcp list` to see the resolved command. Try it in a shell first (`mcp-server-sqlite /path/to/db`) and check the server prints its handshake.
- **Server not connecting (http).** Verify the URL is SSE-compatible. Some servers expose both `/sse` and `/ws` — KohakuTerrarium uses SSE.
- **Tool not found.** The meta-tools list is computed at connection time. Reconnect (`mcp_disconnect` + `mcp_connect`) if the server hot-added tools.
- **Env vars not substituted.** MCP config supports `${VAR}` and `${VAR:default}`, same as creature configs.
- **Server crashes mid-session.** Stdio servers respawn on next `mcp_call`. Check the server's own logs.

## See also

- [Configuration](configuration.md) — `mcp_servers:` field.
- [Reference / CLI](../reference/cli.md) — `kt config mcp`, `kt mcp list`.
- [Concepts / tool](../concepts/modules/tool.md) — why MCP tools aren't treated specially.
