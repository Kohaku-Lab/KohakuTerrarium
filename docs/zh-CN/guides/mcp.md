---
title: MCP
summary: 连接 Model Context Protocol 服务器（stdio / HTTP / SSE），并把它们的工具暴露给你的Creature。
tags:
 - guides
 - mcp
 - integration
---

# MCP

给想把 MCP（Model Context Protocol）服务器接到Creature上的读者。

MCP 是一种 client-server 协定，可通过 stdio 或 HTTP 暴露工具（以及其他原语）。KohakuTerrarium 是 client：你在设置里注册服务器后，框架会启动子程序或开启 HTTP 连线，接著把该服务器的工具，通过一组精简的 meta-tool 暴露给 agent 调用。

概念先读：[tool 概念](../concepts/modules/tool.md) —— MCP 工具本质上「就只是工具」，只是以动态方式暴露。

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
    transport: http
    url: https://mcp.example.com/sse
    env:
      API_KEY: "${DOCS_API_KEY}"
```

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
kt config mcp add              # 交互式：transport、command、args、env、url
kt config mcp edit sqlite
kt config mcp delete sqlite
```

全域服务器可被任何有引用它的Creature使用。

## 传输方式

- **stdio** — 启动一个子程序（`command` + `args` + `env`）。最适合本地服务器，延迟低，每个 agent 都有独立的程序生命周期。
- **http** — 对 `url` 开一个 SSE/streaming HTTP 连线。最适合共享或远端服务器，也方便多个Creature共用同一台服务器。

本地 MCP 服务器（sqlite、filesystem、git）通常选 stdio；托管型服务器则选 http。

## MCP 工具如何进到 LLM

当服务器连上后，KohakuTerrarium 会通过 **meta-tool** 暴露它的工具：

- `mcp_list` — 列出所有已连线服务器上的 MCP 工具。
- `mcp_call` — 指定工具名称与参数，调用某个 MCP 工具。
- `mcp_connect` / `mcp_disconnect` — 执行时管理连线。

system prompt 会多出一个「Available MCP Tools」区段，列出每台服务器上的所有工具（名称 + 一行说明）。接著 LLM 只要用 `server`、`tool`、`args` 调用 `mcp_call` 即可。在默认 bracket 格式下会长这样：

```
[/mcp_call]
@@server=sqlite
@@tool=query
@@args={"sql": "SELECT 1"}
[mcp_call/]
```

如果你比较喜欢 `xml` 或 `native`，可以通过 [Creatures 指南](creatures.md) 切换——语意不变。

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

tools = await manager.list_tools("sqlite")
result = await manager.call_tool("sqlite", "query", {"sql": "SELECT 1"})
await manager.disconnect("sqlite")
```

Agent 执行时底层就是用这套机制。

## 疑难排解

- **服务器连不上（stdio）**。 先用 `kt config mcp list` 看解析后的命令。再把它直接拿去 shell 试跑（例如 `mcp-server-sqlite /path/to/db`），确认服务器有正常打印 handshake。
- **服务器连不上（http）**。 确认 URL 支持 SSE。有些服务器同时提供 `/sse` 与 `/ws`——KohakuTerrarium 用的是 SSE。
- **找不到工具**。 Meta-tool 清单是在连线当下计算的。如果服务器在执行中热新增了工具，请重新连线（`mcp_disconnect` + `mcp_connect`）。
- **环境变量没有替换**。 MCP 设置支持 `${VAR}` 与 `${VAR:default}`，和Creature 设置一样。
- **服务器在会话中途崩溃**。 Stdio 服务器会在下一次 `mcp_call` 时重新启动。也请查看服务器自己的日志。

## 延伸阅读

- [配置指南](configuration.md) — `mcp_servers:` 字段。
- [参考 / CLI](../reference/cli.md) — `kt config mcp`、`kt mcp list`。
- [概念 / tool](../concepts/modules/tool.md) — 为什么 MCP 工具不被特别对待。
