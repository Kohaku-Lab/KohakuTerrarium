import { describe, expect, it } from "vitest"

import {
  CONTEXT_SUGGESTIONS,
  contextSuggestions,
  contextText,
  exceedsWindow,
  formatContext,
  parseContext,
  takeContext,
  withContext,
} from "./contextLength"

describe("parseContext", () => {
  it("reads plain counts and k / m suffixes in any case", () => {
    expect(parseContext("500000")).toBe(500000)
    expect(parseContext("500k")).toBe(500000)
    expect(parseContext("500K")).toBe(500000)
    expect(parseContext("1.5m")).toBe(1500000)
    expect(parseContext("1M")).toBe(1000000)
    expect(parseContext(" 272k ")).toBe(272000)
    expect(parseContext("200,000")).toBe(200000)
    expect(parseContext("1_000_000")).toBe(1000000)
    expect(parseContext(".5m")).toBe(500000)
    expect(parseContext(131072)).toBe(131072)
  })

  it("rejects blanks, junk, zero and negatives instead of guessing", () => {
    for (const bad of [
      "",
      "  ",
      null,
      undefined,
      "abc",
      "5g",
      "1.2.3",
      "k",
      "-5",
      "0",
      "0k",
      0,
      -1,
      NaN,
      Infinity,
    ]) {
      expect(parseContext(bad)).toBe(null)
    }
  })
})

describe("formatContext and contextText", () => {
  it("labels lengths compactly", () => {
    expect(formatContext(1000000)).toBe("1M")
    expect(formatContext(1500000)).toBe("1.5M")
    expect(formatContext(272000)).toBe("272k")
    expect(formatContext(131072)).toBe("131.1k")
    expect(formatContext(512)).toBe("512")
    expect(formatContext(null)).toBe("")
    expect(formatContext(0)).toBe("")
  })

  it("echoes a length in a form that parses back to the same number", () => {
    for (const n of [1000000, 1500000, 272000, 131072, 1234000, 999]) {
      expect(parseContext(contextText(n))).toBe(n)
    }
    expect(contextText(1500000)).toBe("1.5m")
    expect(contextText(272000)).toBe("272k")
    expect(contextText(131072)).toBe("131072")
    expect(contextText(0)).toBe("")
  })
})

describe("exceedsWindow", () => {
  it("warns only above a known window", () => {
    expect(exceedsWindow(1000001, 1000000)).toBe(true)
    expect(exceedsWindow("2m", 1000000)).toBe(true)
    expect(exceedsWindow(1000000, 1000000)).toBe(false)
    expect(exceedsWindow(500000, 1000000)).toBe(false)
    expect(exceedsWindow(5000000, null)).toBe(false)
    expect(exceedsWindow(5000000, 0)).toBe(false)
    expect(exceedsWindow(null, 1000)).toBe(false)
  })
})

describe("contextSuggestions", () => {
  it("adds the model window to the common lengths, sorted and unique", () => {
    expect(contextSuggestions(null)).toEqual(CONTEXT_SUGGESTIONS)
    expect(contextSuggestions(128000)).toEqual([128000, 200000, 272000, 400000, 1000000])
    expect(contextSuggestions(1000000)).toEqual([200000, 272000, 400000, 1000000])
  })
})

describe("takeContext and withContext", () => {
  it("splits context= from the variation selections", () => {
    expect(takeContext({ reasoning: "high", context: "500k" }, { reasoning: {} })).toEqual({
      selections: { reasoning: "high" },
      context: "500k",
    })
    expect(takeContext({ reasoning: "high" })).toEqual({
      selections: { reasoning: "high" },
      context: "",
    })
  })

  it("keeps context= as a group choice when the preset has a context group", () => {
    const groups = { context: { "1m": { max_context: 1000000 } } }
    expect(takeContext({ context: "1m" }, groups)).toEqual({
      selections: { context: "1m" },
      context: "",
    })
  })

  it("appends context=<n> as a plain integer after any variations", () => {
    expect(withContext("codex/gpt-6-sol", "500k")).toBe("codex/gpt-6-sol@context=500000")
    expect(withContext("codex/gpt-6-sol@reasoning=high", 1500000)).toBe(
      "codex/gpt-6-sol@reasoning=high,context=1500000",
    )
  })

  it("leaves the selector alone for a blank or invalid length", () => {
    expect(withContext("codex/gpt-6-sol", "")).toBe("codex/gpt-6-sol")
    expect(withContext("codex/gpt-6-sol", null)).toBe("codex/gpt-6-sol")
    expect(withContext("codex/gpt-6-sol", "lots")).toBe("codex/gpt-6-sol")
    expect(withContext("", "500k")).toBe("")
  })
})
