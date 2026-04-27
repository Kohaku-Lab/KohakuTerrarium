---
title: MCP
summary: 連接 Model Context Protocol 伺服器（stdio / SSE / streamable HTTP），並把它們的工具暴露給你的生物。
tags:
  - guides
  - mcp
  - integration
---

# MCP

給想把 MCP（Model Context Protocol）伺服器接到生物上的讀者。

MCP 是一種 client-server 協定，可透過 stdio 或 HTTP 暴露工具（以及其他原語）。KohakuTerrarium 是 client：你在設定裡註冊伺服器後，框架會啟動子程序或開啟 HTTP 連線，接著把該伺服器的工具，透過一組精簡的 meta-tool 暴露給 agent 呼叫。

概念先讀：[tool](../concepts/modules/tool.md) —— MCP 工具本質上「就只是工具」，只是以動態方式暴露。

## 宣告伺服器的兩個位置

### 每個 agent 各自宣告

在 `config.yaml` 裡：

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
    env:
      API_KEY: "${DOCS_API_KEY}"
```

只有這個生物會連上這些伺服器。

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

可用互動式指令管理：

```bash
kt config mcp list
kt config mcp add              # 互動式：transport、command、args、env、url
kt config mcp edit sqlite
kt config mcp delete sqlite
```

全域伺服器可被任何有參照它的生物使用。

## 傳輸方式

- **stdio** — 啟動一個子程序（`command` + `args` + `env`）。最適合本機伺服器，延遲低，每個 agent 都有獨立的程序生命週期。
- **http** / **sse** — 對 `url` 開一個傳統 SSE 連線。適合明確暴露 `/sse` 之類端點的舊式伺服器。
- **streamable_http** — 對 `url` 開一個現代 streamable HTTP 連線。許多新的 FastMCP 伺服器（例如 `FastMCP.streamable_http_app()`）預設就是這個模式。

本機 MCP 伺服器（sqlite、filesystem、git）通常選 stdio；大多數現代託管伺服器優先選 `streamable_http`；只有伺服器明確提供舊式 SSE 端點時再選 `http` / `sse`。

## MCP 工具如何進到 LLM

當伺服器連上後，KohakuTerrarium 會透過 **meta-tool** 暴露它的工具：

- `mcp_list` — 列出所有已連線伺服器上的 MCP 工具。
- `mcp_call` — 指定工具名稱與參數，呼叫某個 MCP 工具。
- `mcp_connect` / `mcp_disconnect` — 執行時管理連線。

system prompt 會多出一個「Available MCP Tools」區段，列出每台伺服器上的所有工具（名稱 + 一行說明）。接著 LLM 只要用 `server`、`tool`、`args` 呼叫 `mcp_call` 即可。在預設 bracket 格式下會長這樣：

```
[/mcp_call]
@@server=sqlite
@@tool=query
@@args={"sql": "SELECT 1"}
[mcp_call/]
```

如果你比較喜歡 `xml` 或 `native`，可以透過 [`tool_format`](creatures.md) 切換。整體語意不變：`mcp_call` 仍然使用 `server`、`tool`、`args`，而 native function schema 現在也會直接暴露這些欄位。

你不需要逐一把每個 MCP 工具接進設定；meta-tool 方式的好處，就是 controller 的工具清單可以保持精簡。

## 列出已連線伺服器

針對特定 agent：

```bash
kt mcp list --agent path/to/creature
```

會印出名稱、傳輸方式、命令、URL、參數、環境變數鍵名。

## 程式化使用

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

Agent 執行時底層就是用這套機制。

## 疑難排解

- **伺服器連不上（stdio）。** 先用 `kt config mcp list` 看解析後的命令。再把它直接拿去 shell 試跑（例如 `mcp-server-sqlite /path/to/db`），確認伺服器有正常印出 handshake。
- **伺服器連不上（http / sse）。** 確認 URL 支援 SSE。有些伺服器同時提供 `/sse` 與 `/ws`；KohakuTerrarium 的 `http` / `sse` transport 需要 SSE 端點。
- **伺服器連不上（streamable_http）。** 確認 URL 指向伺服器的 streamable HTTP 端點（例如 FastMCP 的 `streamable_http_app()` 路由），而不是舊式 SSE 路徑。
- **找不到工具。** Meta-tool 清單是在連線當下計算的。如果伺服器在執行中熱新增了工具，請重新連線（`mcp_disconnect` + `mcp_connect`）。
- **環境變數沒有替換。** MCP 設定支援 `${VAR}` 與 `${VAR:default}`，和生物設定一樣。
- **伺服器在工作階段中途崩潰。** Stdio 伺服器會在下一次 `mcp_call` 時重新啟動。也請查看伺服器自己的日誌。

## 延伸閱讀

- [設定檔](configuration.md) — `mcp_servers:` 欄位。
- [參考 / CLI](../reference/cli.md) — `kt config mcp`、`kt mcp list`。
- [概念 / tool](../concepts/modules/tool.md) — 為什麼 MCP 工具不被特別對待。
