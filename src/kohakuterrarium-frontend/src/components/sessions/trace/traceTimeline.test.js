import { describe, expect, it } from "vitest"

import {
  deriveTraceTimeline,
  formatTimelineDuration,
  laneForType,
  normalizeSpan,
  traceTimelineFocus,
} from "@/components/sessions/trace/traceTimeline"

const rec = (over) =>
  normalizeSpan({
    eid: 1,
    type: "text_chunk",
    ts: 1000,
    dur: 100,
    turn: 1,
    err: false,
    ...over,
  })

describe("laneForType", () => {
  it("routes user input to lane 0", () => {
    expect(laneForType("user_input")).toBe(0)
    expect(laneForType("user_message")).toBe(0)
  })
  it("routes tools/plugins to lane 2", () => {
    expect(laneForType("tool_call")).toBe(2)
    expect(laneForType("tool_result")).toBe(2)
    expect(laneForType("tool_wait")).toBe(2)
    expect(laneForType("plugin_hook_timing")).toBe(2)
  })
  it("routes subagents/background jobs to lane 3", () => {
    expect(laneForType("subagent_call")).toBe(3)
    expect(laneForType("subagent_result")).toBe(3)
    expect(laneForType("subagent_tool")).toBe(3)
    expect(laneForType("background_result")).toBe(3)
  })
  it("routes everything else to the model lane", () => {
    expect(laneForType("text_chunk")).toBe(1)
    expect(laneForType("assistant_reasoning")).toBe(1)
    expect(laneForType("compact_start")).toBe(1)
    expect(laneForType("")).toBe(1)
  })
})

describe("normalizeSpan", () => {
  it("rejects events without a usable timestamp", () => {
    expect(normalizeSpan({ type: "text_chunk" })).toBe(null)
    expect(normalizeSpan(null)).toBe(null)
  })
  it("accepts live-event field aliases", () => {
    const r = normalizeSpan({
      event_id: 7,
      type: "tool_call",
      ts: 5,
      duration_ms: 30,
      turn_index: 2,
      tool: "bash",
    })
    expect(r.eid).toBe(7)
    expect(r.durMs).toBe(30)
    expect(r.turn).toBe(2)
    expect(r.label).toBe("bash")
    expect(r.lane).toBe(2)
  })
  it("marks error types", () => {
    expect(rec({ type: "tool_error" }).err).toBe(true)
  })
})

describe("deriveTraceTimeline", () => {
  const records = [
    rec({ eid: 1, type: "user_message", ts: 1000, turn: 1 }),
    rec({ eid: 2, type: "text_chunk", ts: 1001, dur: 500, turn: 1 }),
    rec({ eid: 3, type: "tool_call", ts: 1002, dur: 2000, turn: 1 }),
    rec({ eid: 4, type: "user_message", ts: 1010, turn: 2 }),
    rec({ eid: 5, type: "text_chunk", ts: 1011, dur: 100, turn: 2 }),
  ]

  it("sequence mode gives equal-width blocks and turn boundaries", () => {
    const m = deriveTraceTimeline(records, "sequence")
    expect(m.start).toBe(0)
    expect(m.end).toBe(5)
    expect(m.spans.map((s) => [s.start, s.end])).toEqual([
      [0, 1],
      [1, 2],
      [2, 3],
      [3, 4],
      [4, 5],
    ])
    expect(m.turnBoundaries).toEqual([
      { turn: 1, time: 0 },
      { turn: 2, time: 3 },
    ])
    expect(m.spans[0].lane).toBe(0)
    expect(m.spans[2].lane).toBe(2)
  })

  it("duration mode compresses idle gaps", () => {
    const m = deriveTraceTimeline(records, "duration")
    // Idle gaps (900ms + 500ms) before the tool call are removed, so its
    // recorded 1002000→1004000 span shifts left by 1400ms; the 6s idle
    // before turn 2 is removed as well, making the two spans adjacent.
    const tool = m.spans.find((s) => s.index === 3)
    const nextInput = m.spans.find((s) => s.index === 4)
    expect(tool.end).toBe(1002600)
    expect(nextInput.start).toBe(1002600)
    expect(m.turnBoundaries).toEqual([
      { turn: 1, time: 1000000 },
      { turn: 2, time: 1002600 },
    ])
  })

  it("time mode gives equal zero-width blocks at compressed starts", () => {
    const m = deriveTraceTimeline(records, "time")
    for (const s of m.spans) expect(s.end).toBe(s.start)
  })

  it("actual mode keeps idle gaps", () => {
    const m = deriveTraceTimeline(records, "actual")
    const nextInput = m.spans.find((s) => s.index === 4)
    expect(nextInput.start).toBe(1010000)
    expect(m.end).toBe(1011100)
  })

  it("returns null for empty input", () => {
    expect(deriveTraceTimeline([], "sequence")).toBe(null)
    expect(deriveTraceTimeline([], "actual")).toBe(null)
  })
})

describe("traceTimelineFocus", () => {
  const records = [
    rec({ eid: 1, ts: 1000, dur: 100, turn: 1 }),
    rec({ eid: 2, ts: 1010, dur: 100, turn: 2 }),
  ]

  it("returns turns and event ids overlapping the range (inclusive)", () => {
    const m = deriveTraceTimeline(records, "actual")
    const f = traceTimelineFocus(m, { start: 1000100, end: 1010000 })
    expect([...f.turns].sort()).toEqual([1, 2])
    expect([...f.eventIds].sort()).toEqual([1, 2])
  })

  it("excludes spans fully outside the range", () => {
    const m = deriveTraceTimeline(records, "actual")
    const f = traceTimelineFocus(m, { start: 1000000, end: 1000050 })
    expect([...f.turns]).toEqual([1])
    expect([...f.eventIds]).toEqual([1])
  })

  it("handles null model/range", () => {
    const f = traceTimelineFocus(null, null)
    expect(f.turns.size).toBe(0)
    expect(f.eventIds.size).toBe(0)
  })
})

describe("formatTimelineDuration", () => {
  it("formats sub-second, seconds, and minutes", () => {
    expect(formatTimelineDuration(42)).toBe("42ms")
    expect(formatTimelineDuration(2500)).toBe("2.5s")
    expect(formatTimelineDuration(120000)).toBe("2.0min")
  })
})
