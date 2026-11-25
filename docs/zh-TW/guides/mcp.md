---
title: MCP
summary: 連接 Model Context Protocol 伺服器（stdio / SSE / streamable HTTP），並把它們的工具暴露給你的生物。
tags:
  - guides
  - mcp
  - integration
---

# MCP

若要向外部用戶端提供 KT 自身的工具與本機任務委派，請參閱 [MCP 伺服器指南](mcp-server.md)。

給想把 MCP（Model Context Protocol）伺服器接到生物上的讀者。

MCP 是一種 client-server 協定，可透過 stdio 或 HTTP 暴露工具（以及其他原語）。KohakuTerrarium 是 client：你在設定裡註冊伺服器後，框架會啟動子程序或開啟 HTTP 連線，接著把該伺服器的工具，透過一組精簡的 meta-tool 暴露給 agent 呼叫。

概念先讀：[tool](../concepts/modules/tool.md)，MCP 工具本質上「就只是工具」，只是以動態方式暴露。

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
    headers:
      Authorization: "Bearer ${DOCS_API_KEY}"
```

`headers` 用於 `sse` 和 `streamable_http` 傳輸；`env` 用於 `stdio`。

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
kt config mcp add              # 互動式：transport（stdio、streamable_http、sse）、command、args、env、url、headers
kt config mcp edit sqlite
kt config mcp delete sqlite
```

全域伺服器可被任何有參照它的生物使用。

註冊表條目的 `command`、`args`、`env`、`url` 和 `headers` 可以使用 `${VAR}` 與
`${VAR:default}`，這樣權杖不必寫在檔案裡：

```yaml
- name: sandbox
  transport: streamable_http
  url: http://127.0.0.1:8765/mcp
  headers:
    Authorization: "Bearer ${SANDBOX_TOKEN}"
```

每次把條目變成連線時（生物啟動、`mcp_connect` 指定已註冊的伺服器、Settings > MCP >
**Test connection**），都會用 KT 程序的環境變數展開這些標記。檔案和 `GET /api/settings/mcp`
的回應保留原始的 `${VAR}` 文字，不會包含變數的值。沒有設定且沒有預設值的變數會讓該伺服器停下，
並給出 `environment variable SANDBOX_TOKEN is not set (used in headers of server sandbox)`，
而不是把字面的 `${SANDBOX_TOKEN}` 送出去；生物仍然會在沒有該伺服器的情況下啟動。
`${VAR:}` 表示「未設定時為空」。模型在 `mcp_connect` 裡寫的內嵌定義按原樣使用，不會展開。

## 傳輸方式

- **stdio**：啟動一個子程序（`command` + `args` + `env`）。最適合本機伺服器，延遲低，每個 agent 都有獨立的程序生命週期。
- **streamable_http**：對 `url`（通常以 `/mcp` 結尾）開一個現代 streamable HTTP 連線。目前的遠端伺服器都使用這種方式，包括 FastMCP 的 `streamable_http_app()`。
- **sse**：對 `url`（通常以 `/sse` 結尾）開一個傳統 SSE 連線。`http` 是這個 transport 的舊名稱，仍然可用。

所有編輯器提供同樣的三個選項：網頁介面（Settings > MCP 的 Add 表單和 Edit 對話框：
「stdio（子程序）」「Streamable HTTP（建議用於遠端）」「SSE（舊版）」）、`kt config mcp add`，
以及 rich CLI 和 ktui 的設定畫面。既有的 `http` 條目會顯示為 SSE。HTTP API 接受 `stdio`、
`streamable_http`、`sse` 和 `http`，其他值回傳 `422`。

本機 MCP 伺服器（sqlite、filesystem、git）通常選 stdio；大多數現代託管伺服器優先選 `streamable_http`；只有伺服器只提供舊式 SSE 端點時再選 `sse`。

## MCP 工具如何進到 LLM

當伺服器連上後，KohakuTerrarium 會透過 **meta-tool** 暴露它的工具：

- `mcp_list`：列出所有已連線伺服器上的 MCP 工具。
- `mcp_call`：指定工具名稱與參數，呼叫某個 MCP 工具。
- `mcp_connect` / `mcp_disconnect`：執行時管理連線。

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

tools = manager.get_server_tools("sqlite")
result = await manager.call_tool("sqlite", "query", {"sql": "SELECT 1"})
await manager.disconnect("sqlite")
```

Agent 執行時底層就是用這套機制。

`call_tool` 以文字回傳結果：文字部分以換行串接，二進位部分顯示為
`[binary data: N bytes]`，伺服器把結果標為錯誤時會加上 `[MCP Error] ` 前綴。
`call_tool_result` 接受相同參數，回傳原始的 `mcp.types.CallToolResult`
（結構化內容、圖片、`isError` 旗標），給需要自行呈現結果的呼叫端使用。
伺服器未連線或沒有列出該工具時，兩者都會拋出 `ValueError`。`connect` 之後，
`MCPServerInfo.instructions` 保存伺服器在 `initialize` 結果中送出的說明
（沒有送出時為空字串）。框架不會把這段說明加進系統提示詞。

## 疑難排解

連線失敗時會拋出一個帶型別的錯誤（`kohakuterrarium.errors.MCPConnectionError`、
`MCPTimeoutError` 或 `MCPCommandNotFoundError`，都屬於 `MCPError`），訊息裡寫明伺服器名稱、
URL（不含查詢字串和憑證）以及原因。Settings > MCP 的 **Test connection** 按鈕、`mcp_connect`
和 agent 日誌顯示的是同一段文字。收到 HTTP 錯誤狀態碼或連線被拒時會立即結束，不會等到
`connect_timeout`。header 的值不會出現在訊息或日誌裡。

| 訊息 | 處理方式 |
| --- | --- |
| `HTTP 401 Unauthorized from <url>; check the headers/token`（或 403） | `Authorization` header 缺少或錯誤。如果用了 `${VAR}`，確認 KT 程序裡設定了該變數。 |
| `HTTP 400 Bad Request from <url>; the server may speak streamable HTTP: try the streamable_http transport` | 條目用的是 `sse` / `http`，但伺服器只支援 streamable HTTP。 |
| `HTTP 404 Not Found from <url>; check the URL path (for example /mcp)` | 端點路徑錯誤。FastMCP 的 streamable HTTP 在 `/mcp`，SSE 在 `/sse`。 |
| `HTTP 5xx ...; server error: check the MCP server's logs` | 伺服器處理請求時出錯。 |
| `cannot connect to <url>: <reason>` | 該主機與連接埠沒有服務在監聽，或 DNS 解析失敗。 |
| `TLS handshake with <url> failed: <reason>` | 憑證或 TLS 問題。 |
| `timed out after Ns; last HTTP response was ...` | 伺服器有回應，但沒有完成 MCP 握手。 |
| `command not found: <command>` | stdio 伺服器的 `command` 不在 `PATH` 裡。 |
| `the server process exited before the handshake finished` | stdio 伺服器啟動時崩潰。在 shell 裡執行它的命令查看錯誤。 |

- **伺服器連不上（stdio）。** 先用 `kt config mcp list` 看解析後的命令。再把它直接拿去 shell 試跑（例如 `mcp-server-sqlite /path/to/db`），確認伺服器有正常印出 handshake。
- **伺服器連不上（http / sse）。** 確認 URL 支援 SSE。有些伺服器同時提供 `/sse` 與 `/ws`；KohakuTerrarium 的 `http` / `sse` transport 需要 SSE 端點。
- **伺服器連不上（streamable_http）。** 確認 URL 指向伺服器的 streamable HTTP 端點（例如 FastMCP 的 `streamable_http_app()` 路由），而不是舊式 SSE 路徑。
- **找不到工具。** Meta-tool 清單是在連線當下計算的。如果伺服器在執行中熱新增了工具，請重新連線（`mcp_disconnect` + `mcp_connect`）。
- **環境變數沒有替換。** MCP 設定支援 `${VAR}` 與 `${VAR:default}`，和生物設定一樣。變數必須設定在執行 KT 的程序環境裡（網頁介面對應 `kt serve` / `kt app` 程序），而不只是你的 shell。
- **伺服器在工作階段中途崩潰。** Stdio 伺服器會在下一次 `mcp_call` 時重新啟動。也請查看伺服器自己的日誌。

## 延伸閱讀

- [設定檔](configuration.md)：`mcp_servers:` 欄位。
- [參考 / CLI](../reference/cli.md)：`kt config mcp`、`kt mcp list`。
- [概念 / tool](../concepts/modules/tool.md)：為什麼 MCP 工具不被特別對待。
