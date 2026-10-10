import { describe, expect, it } from "vitest"

import { activeSessionIds } from "./labActivity"

const session = (id, channelIds) => ({ id, channelIds })

describe("lab activity", () => {
  const sessions = [session("a", ["ch:a:x", "ch:a:y"]), session("b", ["ch:b:z"]), session("c", [])]

  it("marks a session active only while one of its channels pulsed within the window", () => {
    const pulses = { "ch:a:y": 10_000, "ch:b:z": 1_000 }
    expect([...activeSessionIds(sessions, pulses, 12_000)]).toEqual(["a"])
    expect([...activeSessionIds(sessions, pulses, 18_001)]).toEqual([])
    expect([...activeSessionIds(sessions, { "ch:b:z": 1_000 }, 6_000, 5_000)]).toEqual(["b"])
    expect([...activeSessionIds(sessions, { "ch:b:z": 1_000 }, 6_001, 5_000)]).toEqual([])
  })
})
