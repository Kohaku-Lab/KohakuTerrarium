import { describe, expect, it } from "vitest"

import { buildGraphModel } from "@/utils/graph/data/model"

import {
  GAP,
  GLASS_W,
  LANE_PAD,
  TANK_W,
  buildTanks,
  fitView,
  glassHeight,
  layoutBench,
  tankHeight,
  tankStatus,
} from "./labLayout"

const creature = (id, extra = {}) => ({
  creature_id: id,
  name: id,
  running: true,
  listen_channels: [],
  send_channels: [],
  ...extra,
})
const model = (graphs) => buildGraphModel({ graphs })

describe("tanks", () => {
  it("groups each session's creatures by machine, privileged first, and shows the most urgent status", () => {
    const tanks = buildTanks(
      model([
        {
          graph_id: "a",
          name: "alpha",
          channels: [{ name: "tasks" }],
          creatures: [
            creature("w1", { is_processing: true }),
            creature("boss", { is_privileged: true }),
            creature("w2", { home_node: "gpu" }),
          ],
        },
        { graph_id: "b", creatures: [creature("solo", { running: false })] },
      ]),
    )
    expect(tanks.map((t) => [t.id, t.name, t.hosts, t.status, t.size])).toEqual([
      ["a", "alpha", ["_host", "gpu"], "busy", 3],
      ["b", "b", ["_host"], "stopped", 1],
    ])
    expect(tanks[0].compartments.map((c) => [c.hostId, c.creatures.map((x) => x.id)])).toEqual([
      ["_host", ["boss", "w1"]],
      ["gpu", ["w2"]],
    ])
    expect(tanks[0].counts).toEqual({ busy: 1, idle: 2 })
    expect(tanks[0].channelIds).toEqual(["ch:a:tasks"])
    expect(tankStatus([{ status: "idle" }, { status: "error" }, { status: "busy" }])).toBe("error")
    expect(tankStatus([])).toBe("stopped")
  })
})

describe("bench layout on one machine", () => {
  const tanks = buildTanks(
    model(["a", "b", "c"].map((id) => ({ graph_id: id, creatures: [creature(`${id}1`)] }))),
  )

  it("fills rows to the width, puts the start tile last, and draws no lanes", () => {
    const wide = layoutBench(tanks, { width: 2 * TANK_W + GAP })
    expect(wide.lanes).toEqual([])
    expect(wide.items.map((i) => [i.kind, i.x, i.y > 0])).toEqual([
      ["tank", 0, false],
      ["tank", TANK_W + GAP, false],
      ["tank", 0, true],
      ["start", TANK_W + GAP, true],
    ])
    const narrow = layoutBench(tanks, { width: 100 })
    expect(new Set(narrow.items.map((i) => i.x))).toEqual(new Set([0]))
    expect(narrow.bounds.w).toBe(TANK_W)
  })

  it("grows a tank's glass with its creatures and caps it", () => {
    expect(glassHeight(40, GLASS_W)).toBe(glassHeight(400, GLASS_W))
    expect(glassHeight(1, GLASS_W)).toBeLessThan(glassHeight(40, GLASS_W))
    expect(tankHeight(12)).toBeGreaterThan(tankHeight(2))
  })

  it("an empty bench still has the start tile", () => {
    expect(layoutBench([], { width: 800 }).items.map((i) => i.kind)).toEqual(["start"])
  })
})

describe("bench layout across machines", () => {
  const tanks = buildTanks(
    model([
      { graph_id: "local", creatures: [creature("l1")] },
      { graph_id: "wide", creatures: [creature("x1"), creature("x2", { home_node: "w2" })] },
      { graph_id: "remote", creatures: [creature("r1", { home_node: "w1" })] },
      {
        graph_id: "pair",
        creatures: [creature("p1", { home_node: "w1" }), creature("p2", { home_node: "w2" })],
      },
    ]),
  )
  const laid = layoutBench(tanks)
  const item = (id) => laid.items.find((i) => i.tank?.id === id)
  const lane = (h) => laid.lanes.find((l) => l.hostId === h)

  it("draws one lane per machine, host first", () => {
    expect(laid.lanes.map((l) => l.hostId)).toEqual(["_host", "w1", "w2"])
    expect(laid.lanes.every((l) => l.h === laid.bounds.h)).toBe(true)
  })

  it("keeps a one-machine session inside its lane", () => {
    expect(item("local").x).toBe(lane("_host").x + LANE_PAD)
    expect(item("remote").x).toBe(lane("w1").x + LANE_PAD)
    expect(item("remote").w).toBe(TANK_W)
  })

  it("spans a cross-machine session over its lanes, each compartment aligned with its machine", () => {
    const wide = item("wide")
    expect(wide.x).toBe(lane("_host").x + LANE_PAD)
    expect(wide.x + wide.w).toBe(lane("w2").x + lane("w2").w - LANE_PAD)
    for (const c of wide.compartments) expect(wide.x + c.x).toBe(lane(c.hostId).x + LANE_PAD)
    expect(wide.compartments.map((c) => c.hostId)).toEqual(["_host", "w2"])
    // Spanning tanks sit above the lanes' own tanks.
    expect(Math.max(item("wide").y, item("pair").y)).toBeLessThan(item("local").y)
    // "pair" overlaps "wide" (w2), so it takes its own row.
    expect(item("pair").y).not.toBe(item("wide").y)
  })

  it("puts the start tile at the end of the host lane, and nothing overlaps", () => {
    const start = laid.items.find((i) => i.kind === "start")
    expect(start.x).toBe(lane("_host").x + LANE_PAD)
    expect(start.y).toBeGreaterThan(item("local").y)
    const overlap = (a, b) =>
      a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
    for (const a of laid.items)
      for (const b of laid.items) if (a !== b) expect(overlap(a, b)).toBe(false)
  })
})

describe("fit", () => {
  it("fits large benches by shrinking, never enlarges small ones, and centres both ways", () => {
    expect(fitView({ w: 400, h: 200 }, 1000, 800)).toEqual({ k: 1, x: 300, y: 300 })
    const big = fitView({ w: 2000, h: 1000 }, 1000, 800)
    expect(big.k).toBeCloseTo((1000 - 64) / 2000)
    expect(big.x).toBeCloseTo((1000 - 2000 * big.k) / 2)
    expect(big.y).toBeCloseTo((800 - 1000 * big.k) / 2)
    expect(fitView({ w: 40000, h: 100 }, 1000, 800).k).toBe(0.25)
  })

  it("starts from the top when the bench is taller than the view even at the smallest scale", () => {
    expect(fitView({ w: 400, h: 8000 }, 1000, 800)).toEqual({ k: 0.25, x: 450, y: 32 })
  })
})
