import { describe, expect, it } from "vitest"

import { formatTokens, hudRows, hudSession, sessionsToFetch } from "./hudModel"

describe("formatTokens", () => {
  it("keeps small counts exact and shortens large ones", () => {
    expect(formatTokens(0)).toBe("0")
    expect(formatTokens(950)).toBe("950")
    expect(formatTokens(1234)).toBe("1.2k")
    expect(formatTokens(12_345)).toBe("12k")
    expect(formatTokens(4_100_000)).toBe("4.1M")
    expect(formatTokens(undefined)).toBe("0")
    expect(formatTokens("x")).toBe("0")
  })
})

describe("hudSession", () => {
  it("lists each creature with its model and tokens and totals the session", () => {
    const session = hudSession({
      session_id: "s1",
      name: "Build",
      creatures: [
        {
          name: "root",
          running: true,
          llm_name: "gpt",
          token_usage: { total: 1500 },
          context_tokens: 300,
        },
        { creature_id: "c2", model: "m2", token_usage: { total: 500 } },
      ],
    })

    expect(session).toMatchObject({ id: "s1", name: "Build", running: true, tokens: 2000 })
    expect(session.creatures).toEqual([
      { name: "root", running: true, model: "gpt", tokens: 1500, context: 300 },
      { name: "c2", running: false, model: "m2", tokens: 500, context: 0 },
    ])
  })

  it("survives a session without creatures or usage", () => {
    expect(hudSession({ session_id: "s2" })).toMatchObject({
      id: "s2",
      name: "s2",
      running: false,
      tokens: 0,
      creatures: [],
    })
    expect(hudSession(null).id).toBe("")
  })
})

describe("hudRows", () => {
  it("puts working sessions first and drops missing ones", () => {
    const rows = hudRows([
      { session_id: "idle", creatures: [{ name: "a" }] },
      null,
      { session_id: "busy", creatures: [{ name: "b", running: true }] },
      { creatures: [] },
    ])

    expect(rows.map((row) => row.id)).toEqual(["busy", "idle"])
  })
})

describe("sessionsToFetch", () => {
  it("asks for working sessions first and no more than the limit", () => {
    const listing = [
      { session_id: "a", running: false },
      { session_id: "b", running: true },
      { session_id: "c", running: false },
      { name: "no id" },
    ]

    expect(sessionsToFetch(listing, 2).map((entry) => entry.session_id)).toEqual(["b", "a"])
    expect(sessionsToFetch(null)).toEqual([])
  })
})
