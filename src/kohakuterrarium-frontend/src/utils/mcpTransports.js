/**
 * MCP transports offered by the Settings > MCP forms. Mirrors
 * MCP_TRANSPORT_CHOICES in src/kohakuterrarium/mcp/transports.py; the
 * backend also accepts the old name "http", shown here as "sse".
 */
export const MCP_TRANSPORT_OPTIONS = [
  {
    value: "stdio",
    labelKey: "settings.mcp.transportStdio",
    hintKey: "settings.mcp.transportStdioHint",
  },
  {
    value: "streamable_http",
    labelKey: "settings.mcp.transportStreamableHttp",
    hintKey: "settings.mcp.transportStreamableHttpHint",
  },
  {
    value: "sse",
    labelKey: "settings.mcp.transportSse",
    hintKey: "settings.mcp.transportSseHint",
  },
]

const STREAMABLE_ALIASES = new Set(["streamable_http", "streamablehttp", "http_streamable"])

/** Return the dropdown value that shows a stored transport ("stdio" when unknown). */
export function transportChoice(stored) {
  const raw = String(stored || "stdio")
    .trim()
    .toLowerCase()
    .replace(/-/g, "_")
  if (raw === "http" || raw === "sse") return "sse"
  if (STREAMABLE_ALIASES.has(raw)) return "streamable_http"
  return "stdio"
}

/** Return the option record for a dropdown value. */
export function transportOption(value) {
  return MCP_TRANSPORT_OPTIONS.find((o) => o.value === value) || MCP_TRANSPORT_OPTIONS[0]
}
