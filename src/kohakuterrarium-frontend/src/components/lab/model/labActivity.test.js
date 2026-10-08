import { describe, expect, it } from "vitest"

import { activeTankIds, benchStructure, latestMessages } from "./labActivity"

const tank = (id, channelIds, hosts = ["_host"], sizes = [1]) => ({
  id,
  channelIds,
  hosts,
  compartments: sizes.map((n) => ({ creatures: Array(n).fill({}) })),
})

describe("lab activity", () => {
  const tanks = [tank("a", ["ch:a:x", "ch:a:y"]), tank("b", ["ch:b:z"]), tank("c", [])]

  it("marks a tank active only while one of its channels pulsed within the window", () => {
    const pulses = { "ch:a:y": 10_000, "ch:b:z": 1_000 }
    expect([...activeTankIds(tanks, pulses, 12_000)]).toEqual(["a"])
    expect([...activeTankIds(tanks, pulses, 18_001)]).toEqual([])
    expect([...activeTankIds(tanks, { "ch:b:z": 1_000 }, 6_000, 5_000)]).toEqual(["b"])
    expect([...activeTankIds(tanks, { "ch:b:z": 1_000 }, 6_001, 5_000)]).toEqual([])
  })

  it("picks each tank's newest message across its channels", () => {
    const last = {
      "ch:a:x": { sender: "old", preview: "1", ts: "2026-10-09T01:00:00Z" },
      "ch:a:y": { sender: "new", preview: "2", ts: "2026-10-09T01:05:00Z" },
    }
    expect(latestMessages(tanks, last)).toEqual({ a: last["ch:a:y"] })
  })

  it("changes its structure key with sessions, machines and sizes only", () => {
    const base = benchStructure([tank("a", [], ["_host"], [2])])
    expect(benchStructure([tank("a", ["ch:a:x"], ["_host"], [2])])).toBe(base)
    expect(benchStructure([tank("a", [], ["_host"], [3])])).not.toBe(base)
    expect(benchStructure([tank("a", [], ["_host", "w1"], [1, 1])])).not.toBe(base)
  })
})
