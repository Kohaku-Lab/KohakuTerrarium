import { describe, expect, it } from "vitest"

import {
  deriveTraceTimeline,
  formatTimelineDuration,
  laneForType,
  normalizeSpan,
  rasterizeTimelineGeometry,
  rasterizeTurnBoundaries,
  summarizeTimelineColumn,
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

describe("rasterizeTimelineGeometry", () => {
  function referenceGeometry(spans, domain, columns) {
    const cells = new Map()
    const duration = domain.end - domain.start
    for (const span of spans) {
      let lane = cells.get(span.lane)
      if (!lane) {
        lane = Array(columns).fill(0)
        cells.set(span.lane, lane)
      }
      for (let column = 0; column < columns; column += 1) {
        const start = domain.start + (column / columns) * duration
        const end = domain.start + ((column + 1) / columns) * duration
        const point = span.start === span.end
        const overlaps = point
          ? span.start >= start &&
            (span.start < end || (column === columns - 1 && span.start === end))
          : span.end > start && span.start < end
        if (overlaps) lane[column] = Math.max(lane[column], span.isError ? 2 : 1)
      }
    }
    const runs = []
    for (const [lane, states] of cells) {
      let active = null
      for (let column = 0; column < states.length; column += 1) {
        const state = states[column]
        if (!state) {
          active = null
          continue
        }
        const error = state === 2
        if (active && active.error === error && active.col + active.spanCols === column) {
          active.spanCols += 1
          active.key = `${lane}:${active.col}:${column}`
        } else {
          active = { key: `${lane}:${column}:${column}`, lane, col: column, spanCols: 1, error }
          runs.push(active)
        }
      }
    }
    return runs
  }

  it("matches a per-column geometry reference without carrying semantic metadata", () => {
    const spans = [
      { start: 0, end: 4, lane: 1, isError: false },
      { start: 2, end: 6, lane: 1, isError: true },
      { start: 5, end: 5, lane: 2, isError: false },
      { start: 9, end: 10, lane: 3, isError: false },
    ]
    const domain = { start: 0, end: 10 }

    expect(rasterizeTimelineGeometry(spans, domain, 10)).toEqual(
      referenceGeometry(spans, domain, 10),
    )
  })

  it("excludes spans that only touch the half-open domain boundary", () => {
    const buckets = rasterizeTimelineGeometry(
      [
        { start: -1, end: 0, lane: 1, isError: true },
        { start: 10, end: 11, lane: 1, isError: true },
        { start: 0, end: 1, lane: 1, isError: false },
      ],
      { start: 0, end: 10 },
      10,
    )

    expect(buckets).toEqual([{ key: "1:0:0", lane: 1, col: 0, spanCols: 1, error: false }])
  })

  it("keeps zero-width point spans on the domain endpoints", () => {
    const buckets = rasterizeTimelineGeometry(
      [
        { start: 0, end: 0, lane: 1, isError: false },
        { start: 10, end: 10, lane: 1, isError: false },
      ],
      { start: 0, end: 10 },
      10,
    )

    expect(buckets.map((bucket) => bucket.col)).toEqual([0, 9])
  })

  it("never reads tooltip metadata while building geometry", () => {
    const spans = Array.from({ length: 32 }, () => ({
      start: 0,
      end: 10,
      lane: 1,
      isError: false,
      get turn() {
        throw new Error("geometry read turn")
      },
      get type() {
        throw new Error("geometry read type")
      },
      get label() {
        throw new Error("geometry read label")
      },
    }))

    expect(rasterizeTimelineGeometry(spans, { start: 0, end: 10 }, 256)).toEqual([
      { key: "1:0:255", lane: 1, col: 0, spanCols: 256, error: false },
    ])
  })

  it("keeps projected long spans as one multi-column bucket", () => {
    expect(
      rasterizeTimelineGeometry(
        [{ start: 2, end: 7, lane: 1, isError: false }],
        { start: 0, end: 10 },
        10,
      ),
    ).toEqual([{ key: "1:2:6", lane: 1, col: 2, spanCols: 5, error: false }])
  })
})

describe("summarizeTimelineColumn", () => {
  it("summarizes only spans overlapping the requested lane and column", () => {
    const summary = summarizeTimelineColumn(
      [
        {
          start: 1,
          end: 4,
          turn: 1,
          lane: 2,
          type: "tool_call",
          label: "bash",
          isError: false,
        },
        {
          start: 2,
          end: 3,
          turn: 2,
          lane: 2,
          type: "tool_result",
          label: "read",
          isError: true,
        },
        {
          start: 2,
          end: 3,
          turn: 3,
          lane: 3,
          type: "subagent_call",
          label: "explore",
          isError: false,
        },
      ],
      { start: 0, end: 10 },
      10,
      2,
      2,
    )

    expect(summary).toEqual({
      count: 2,
      error: true,
      turns: [1, 2],
      types: ["tool_call", "tool_result"],
      labels: ["bash", "read"],
      minStart: 1,
      maxEnd: 4,
    })
  })

  it("uses half-open columns while keeping point spans on the endpoints", () => {
    const spans = [
      { start: -1, end: 0, turn: 1, lane: 1, type: "before", label: "before", isError: true },
      { start: 0, end: 0, turn: 2, lane: 1, type: "point", label: "first", isError: false },
      { start: 1, end: 2, turn: 3, lane: 1, type: "inside", label: "inside", isError: false },
      { start: 10, end: 10, turn: 4, lane: 1, type: "point", label: "last", isError: false },
      { start: 10, end: 11, turn: 5, lane: 1, type: "after", label: "after", isError: true },
    ]
    const domain = { start: 0, end: 10 }

    expect(summarizeTimelineColumn(spans, domain, 10, 1, 0)).toMatchObject({
      count: 1,
      labels: ["first"],
      error: false,
    })
    expect(summarizeTimelineColumn(spans, domain, 10, 1, 1)).toMatchObject({
      count: 1,
      labels: ["inside"],
      error: false,
    })
    expect(summarizeTimelineColumn(spans, domain, 10, 1, 9)).toMatchObject({
      count: 1,
      labels: ["last"],
      error: false,
    })
  })

  it("keeps only the first five unique tooltip values in span order", () => {
    const spans = Array.from({ length: 8 }, (_, index) => ({
      start: 0,
      end: 10,
      turn: index + 1,
      lane: 1,
      type: `type-${index}`,
      label: `label-${index}`,
      isError: false,
    }))

    expect(summarizeTimelineColumn(spans, { start: 0, end: 10 }, 10, 1, 4)).toMatchObject({
      count: 8,
      turns: [1, 2, 3, 4, 5],
      types: ["type-0", "type-1", "type-2", "type-3", "type-4"],
      labels: ["label-0", "label-1", "label-2", "label-3", "label-4"],
    })
  })
})

describe("rasterizeTurnBoundaries", () => {
  it("collapses boundaries that land in the same pixel column", () => {
    expect(
      rasterizeTurnBoundaries(
        [
          { turn: 1, time: 1.1 },
          { turn: 2, time: 1.8 },
          { turn: 3, time: 5 },
        ],
        { start: 0, end: 10 },
        10,
      ),
    ).toEqual([
      { key: 1, x: 15, label: "1–2" },
      { key: 5, x: 55, label: "3" },
    ])
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

  it("keys event ids by member in cluster sessions", () => {
    const m = deriveTraceTimeline(
      [rec({ eid: 1, ts: 1000, dur: 100, turn: 1, member_sid: "node-a" })],
      "actual",
    )
    const f = traceTimelineFocus(m, { start: 0, end: 2000000 })
    expect([...f.eventIds]).toEqual(["node-a:1"])
  })
})

describe("formatTimelineDuration", () => {
  it("formats sub-second, seconds, and minutes", () => {
    expect(formatTimelineDuration(42)).toBe("42ms")
    expect(formatTimelineDuration(2500)).toBe("2.5s")
    expect(formatTimelineDuration(120000)).toBe("2.0min")
  })
})
