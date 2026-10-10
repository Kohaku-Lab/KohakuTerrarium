import { describe, expect, it } from "vitest"

import { digestRead, sessionDigest } from "./sessionDigest"

const session = (busy, savedName = "s_1") => ({ savedName, counts: busy ? { busy: 1 } : {} })

describe("session digest", () => {
  it("carries the summary, its source and the title, never a prompt in the summary's place", () => {
    expect(
      sessionDigest({
        title: " Triage ",
        summary: "Fixing the flaky CI timeout",
        summary_source: "auto",
        exchanges: [{ turn: 4, user: "Why is CI red?", reply: "A flaky timeout." }],
      }),
    ).toEqual({ summary: "Fixing the flaky CI timeout", summaryFrom: "auto", title: "Triage" })
    expect(
      sessionDigest({ summary: "  ", exchanges: [{ turn: 1, user: "Hi there", reply: "" }] }),
    ).toEqual({ summary: "", summaryFrom: "", title: "" })
    expect(sessionDigest(null)).toEqual({ summary: "", summaryFrom: "", title: "" })
  })

  it("reads on first sight and after each burst of work, never while working on", () => {
    let prev = ""
    const step = (s) => {
      const r = digestRead(s, prev)
      prev = r.key
      return r.fetch
    }
    expect(step(session(true))).toBe(true)
    expect(step(session(true))).toBe(false)
    expect(step(session(false))).toBe(true)
    expect(step(session(false))).toBe(false)
    expect(step(session(true))).toBe(false)
    expect(step(session(true))).toBe(false)
    expect(step(session(false))).toBe(true)
    expect(step(session(true, "s_2"))).toBe(true)
    expect(digestRead({ savedName: "", counts: {} }, "x")).toEqual({ key: "", fetch: false })
  })
})
