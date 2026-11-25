import { describe, expect, it } from "vitest"

import { MCP_TRANSPORT_OPTIONS, transportChoice } from "./mcpTransports"
import { translate } from "./i18n"

describe("MCP transport options", () => {
  it("offers stdio, streamable HTTP and legacy SSE in that order", () => {
    expect(MCP_TRANSPORT_OPTIONS.map((o) => o.value)).toEqual(["stdio", "streamable_http", "sse"])
  })

  it("maps stored transports onto a dropdown value", () => {
    expect(transportChoice("stdio")).toBe("stdio")
    expect(transportChoice("streamable_http")).toBe("streamable_http")
    expect(transportChoice("Streamable-HTTP")).toBe("streamable_http")
    expect(transportChoice("streamablehttp")).toBe("streamable_http")
    expect(transportChoice("http")).toBe("sse")
    expect(transportChoice("sse")).toBe("sse")
    expect(transportChoice(undefined)).toBe("stdio")
    expect(transportChoice("bogus")).toBe("stdio")
  })

  it("has a label and a hint in every shipped locale", () => {
    for (const locale of ["en", "zh-CN", "zh-TW", "ja", "ko", "de"]) {
      for (const option of MCP_TRANSPORT_OPTIONS) {
        for (const key of [option.labelKey, option.hintKey]) {
          const text = translate(locale, key)
          expect(text, `${locale} ${key}`).not.toBe(key)
          if (locale !== "en") expect(text, `${locale} ${key}`).not.toBe(translate("en", key))
        }
      }
    }
    expect(translate("en", "settings.mcp.transportStreamableHttp")).toBe(
      "Streamable HTTP (recommended for remote)",
    )
    expect(translate("en", "settings.mcp.transportSse")).toBe("SSE (legacy)")
  })
})
