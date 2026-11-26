---
title: MCP
summary: 连接 Model Context Protocol 服务器（stdio / SSE / streamable HTTP），并把它们的工具暴露给你的Creature。
tags:
 - guides
 - mcp
 - integration
---

# MCP

若要向外部客户端提供 KT 自身的工具和本地任务委派，请参阅 [MCP 服务器指南](mcp-server.md)。

给想把 MCP（Model Context Protocol）服务器接到Creature上的读者。

MCP 是一种 client-server 协定，可通过 stdio 或 HTTP 暴露工具（以及其他原语）。KohakuTerrarium 是 client：你在设置里注册服务器后，框架会启动子程序或开启 HTTP 连线，接著把该服务器的工具，通过一组精简的 meta-tool 暴露给 agent 调用。

概念先读：[tool 概念](../concepts/modules/tool.md)。MCP 工具本质上「就只是工具」，只是以动态方式暴露。

## 宣告服务器的两个位置

### 每个 agent 各自宣告

在 `config.yaml` 里：

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

`headers` 用于 `sse` 和 `streamable_http` 传输；`env` 用于 `stdio`。

只有这个 Creature会连上这些服务器。

### 全域宣告

在 `~/.kohakuterrarium/mcp_servers.yaml`：

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

可用交互式指令管理：

```bash
kt config mcp list
kt config mcp add              # 交互式：transport（stdio、streamable_http、sse）、command、args、env、url、headers
kt config mcp edit sqlite
kt config mcp delete sqlite
```

全域服务器可被任何有引用它的Creature使用。

注册表条目的 `command`、`args`、`env`、`url` 和 `headers` 可以使用 `${VAR}` 与
`${VAR:default}`，这样令牌不必写在文件里：

```yaml
- name: sandbox
  transport: streamable_http
  url: http://127.0.0.1:8765/mcp
  headers:
    Authorization: "Bearer ${SANDBOX_TOKEN}"
```

每次把条目变成连线时（Creature 启动、`mcp_connect` 指定已注册的服务器、Settings > MCP >
**Test connection**），都会用 KT 进程的环境变量展开这些标记。文件和 `GET /api/settings/mcp`
的响应保留原始的 `${VAR}` 文字，不会包含变量的值。没有设置且没有默认值的变量会让该服务器停下，
并给出 `environment variable SANDBOX_TOKEN is not set (used in headers of server sandbox)`，
而不是把字面的 `${SANDBOX_TOKEN}` 发出去；Creature 仍然会在没有该服务器的情况下启动。
`${VAR:}` 表示“未设置时为空”。模型在 `mcp_connect` 里写的内联定义按原样使用，不会展开。

## 传输方式

- **stdio**：启动一个子程序（`command` + `args` + `env`）。最适合本地服务器，延迟低，每个 agent 都有独立的程序生命周期。
- **streamable_http**：对 `url`（通常以 `/mcp` 结尾）开一个现代 streamable HTTP 连线。当前的远程服务器都使用这种方式，包括 FastMCP 的 `streamable_http_app()`。
- **sse**：对 `url`（通常以 `/sse` 结尾）开一个传统 SSE 连线。`http` 是这个 transport 的旧名称，仍然可用。

所有编辑器提供同样的三个选项：网页界面（Settings > MCP 的 Add 表单和 Edit 对话框：
“stdio（子进程）”“Streamable HTTP（推荐用于远程）”“SSE（旧版）”）、`kt config mcp add`，
以及 rich CLI 和 ktui 的设置界面。已有的 `http` 条目会显示为 SSE。HTTP API 接受 `stdio`、
`streamable_http`、`sse` 和 `http`，其他值返回 `422`。

本地 MCP 服务器（sqlite、filesystem、git）通常选 stdio；大多数现代托管服务器优先选 `streamable_http`；只有服务器只提供旧式 SSE 端点时再选 `sse`。

## MCP 工具如何进到 LLM

当服务器连上后，KohakuTerrarium 会通过 **meta-tool** 暴露它的工具：

- `mcp_list`：列出所有已连线服务器上的 MCP 工具。
- `mcp_call`：指定工具名称与参数，调用某个 MCP 工具。
- `mcp_connect` / `mcp_disconnect`：执行时管理连线。

system prompt 会多出一个「Available MCP Tools」区段，列出每台服务器上的所有工具（名称 + 一行说明）。接著 LLM 只要用 `server`、`tool`、`args` 调用 `mcp_call` 即可。在默认 bracket 格式下会长这样：

```
[/mcp_call]
@@server=sqlite
@@tool=query
@@args={"sql": "SELECT 1"}
[mcp_call/]
```

如果你比较喜欢 `xml` 或 `native`，可以通过 [Creatures 指南](creatures.md) 切换。整体语意不变：`mcp_call` 仍然使用 `server`、`tool`、`args`，而 native function schema 现在也会直接暴露这些字段。

你不需要逐一把每个 MCP 工具接进设置；meta-tool 方式的好处，就是 controller 的工具清单可以保持精简。

## 列出已连线服务器

针对特定 agent：

```bash
kt mcp list --agent path/to/creature
```

会打印名称、传输方式、命令、URL、参数、环境变量键名。

## 程序化使用

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

Agent 执行时底层就是用这套机制。

`call_tool` 以文字返回结果：文字部分以换行串接，二进制部分显示为
`[binary data: N bytes]`，服务器把结果标为错误时会加上 `[MCP Error] ` 前缀。
`call_tool_result` 接受相同参数，返回原始的 `mcp.types.CallToolResult`
（结构化内容、图片、`isError` 标记），给需要自行呈现结果的调用方使用。
服务器未连线或没有列出该工具时，两者都会抛出 `ValueError`。`connect` 之后，
`MCPServerInfo.instructions` 保存服务器在 `initialize` 结果中送出的说明
（没有送出时为空字符串）。`list_servers()` 以 `instructions` 字段返回它。在 Creature 中，
“Available MCP Tools” 提示词段落会在每个 `### Server: <name>` 标题下显示该服务器的说明
（上限 4000 个字符，超出部分以截断标记表示）；没有送出说明的服务器不增加任何内容。

## 疑难排解

连线失败时会抛出一个带类型的错误（`kohakuterrarium.errors.MCPConnectionError`、
`MCPTimeoutError` 或 `MCPCommandNotFoundError`，都属于 `MCPError`），消息里写明服务器名称、
URL（不含查询字符串和凭据）以及原因。Settings > MCP 的 **Test connection** 按钮、`mcp_connect`
和 agent 日志显示的是同一段文字。收到 HTTP 错误状态码或连线被拒时会立即结束，不会等到
`connect_timeout`。header 的值不会出现在消息或日志里。

| 消息 | 处理方式 |
| --- | --- |
| `HTTP 401 Unauthorized from <url>; check the headers/token`（或 403） | `Authorization` header 缺失或错误。如果用了 `${VAR}`，确认 KT 进程里设置了该变量。 |
| `HTTP 400 Bad Request from <url>; the server may speak streamable HTTP: try the streamable_http transport` | 条目用的是 `sse` / `http`，但服务器只支持 streamable HTTP。 |
| `HTTP 404 Not Found from <url>; check the URL path (for example /mcp)` | 端点路径错误。FastMCP 的 streamable HTTP 在 `/mcp`，SSE 在 `/sse`。 |
| `HTTP 5xx ...; server error: check the MCP server's logs` | 服务器处理请求时出错。 |
| `cannot connect to <url>: <reason>` | 该主机和端口没有服务在监听，或 DNS 解析失败。 |
| `TLS handshake with <url> failed: <reason>` | 证书或 TLS 问题。 |
| `timed out after Ns; last HTTP response was ...` | 服务器有响应，但没有完成 MCP 握手。 |
| `command not found: <command>` | stdio 服务器的 `command` 不在 `PATH` 里。 |
| `the server process exited before the handshake finished` | stdio 服务器启动时崩溃。在 shell 里执行它的命令查看错误。 |

- **服务器连不上（stdio）**。 先用 `kt config mcp list` 看解析后的命令。再把它直接拿去 shell 试跑（例如 `mcp-server-sqlite /path/to/db`），确认服务器有正常打印 handshake。
- **服务器连不上（http / sse）**。确认 URL 支持 SSE。有些服务器同时提供 `/sse` 与 `/ws`；KohakuTerrarium 的 `http` / `sse` transport 需要 SSE 端点。
- **服务器连不上（streamable_http）**。确认 URL 指向服务器的 streamable HTTP 端点（例如 FastMCP 的 `streamable_http_app()` 路由），而不是旧式 SSE 路径。
- **找不到工具**。 Meta-tool 清单是在连线当下计算的。如果服务器在执行中热新增了工具，请重新连线（`mcp_disconnect` + `mcp_connect`）。
- **环境变量没有替换**。 MCP 设置支持 `${VAR}` 与 `${VAR:default}`，和Creature 设置一样。变量必须设置在运行 KT 的进程环境里（网页界面对应 `kt serve` / `kt app` 进程），而不只是你的 shell。
- **服务器在会话中途崩溃**。 Stdio 服务器会在下一次 `mcp_call` 时重新启动。也请查看服务器自己的日志。

## 延伸阅读

- [配置指南](configuration.md)：`mcp_servers:` 字段。
- [参考 / CLI](../reference/cli.md)：`kt config mcp`、`kt mcp list`。
- [概念 / tool](../concepts/modules/tool.md)：为什么 MCP 工具不被特别对待。
