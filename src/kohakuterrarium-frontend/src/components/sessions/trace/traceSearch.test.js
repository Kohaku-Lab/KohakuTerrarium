import { describe, expect, it } from "vitest"

import {
  eventHaystack,
  matchesSearch,
  parseSearchTerms,
} from "@/components/sessions/trace/traceSearch"

describe("parseSearchTerms", () => {
  it("splits on whitespace and lowercases", () => {
    expect(parseSearchTerms("  Foo   BAR ")).toEqual(["foo", "bar"])
  })
  it("handles empty input", () => {
    expect(parseSearchTerms("")).toEqual([])
    expect(parseSearchTerms(null)).toEqual([])
  })
})

describe("eventHaystack", () => {
  it("covers type, tool, text fields, and turn labels", () => {
    const h = eventHaystack({
      type: "tool_call",
      tool: "bash",
      content: "ls -la",
      turn_index: 3,
    })
    expect(h).toContain("tool_call")
    expect(h).toContain("bash")
    expect(h).toContain("ls -la")
    expect(h).toContain("turn 3")
    expect(h).toContain("#3")
  })
  it("handles non-object input", () => {
    expect(eventHaystack(null)).toBe("")
  })
})

describe("matchesSearch", () => {
  const ev = {
    type: "tool_result",
    output: "permission denied for /etc/shadow",
    error: "exit 1",
  }

  it("empty terms match everything", () => {
    expect(matchesSearch(ev, [])).toBe(true)
    expect(matchesSearch(ev, null)).toBe(true)
  })
  it("ANDs space-separated terms case-insensitively", () => {
    expect(matchesSearch(ev, parseSearchTerms("PERMISSION denied"))).toBe(true)
    expect(matchesSearch(ev, parseSearchTerms("permission missing"))).toBe(false)
  })
  it("matches across fields", () => {
    expect(matchesSearch(ev, parseSearchTerms("tool_result exit"))).toBe(true)
  })
})
