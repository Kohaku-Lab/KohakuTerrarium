import { describe, expect, it } from "vitest"

import { buildGraphModel } from "@/utils/graph/data/model"

import { buildSessions } from "./labSessions"
import { fitLabel, miniGraphLayout } from "./miniGraph"

const creature = (id, extra = {}) => ({
  creature_id: id,
  name: id,
  running: true,
  listen_channels: [],
  send_channels: [],
  ...extra,
})
const sessionOf = (graph) =>
  buildSessions(buildGraphModel({ graphs: [{ graph_id: "g", ...graph }] }))[0]
const inside = (box, w, h) => box.x >= 0 && box.y >= 0 && box.x + box.w <= w && box.y + box.h <= h

describe("mini graph layout", () => {
  const team = sessionOf({
    channels: [{ name: "tasks" }, { name: "reviews" }],
    creatures: [
      creature("lead", {
        is_privileged: true,
        send_channels: ["tasks", "reviews"],
        listen_channels: ["tasks", "reviews"],
      }),
      creature("reviewer", { listen_channels: ["reviews"] }),
      creature("coder", { send_channels: ["tasks"] }),
    ],
    output_edges: [{ edge_id: "e", from: "coder", to: "reviewer" }],
  })

  it("puts privileged nodes above channels above workers, all inside the box", () => {
    const g = miniGraphLayout(team, 300, 132)
    const y = (id) => g.nodes.find((n) => n.id === id).y
    const channelY = g.channels[0].y
    expect(y("lead")).toBeLessThan(channelY)
    expect(channelY).toBeLessThan(y("coder"))
    expect(y("coder")).toBe(y("reviewer"))
    for (const box of [...g.nodes, ...g.channels]) expect(inside(box, 300, 132)).toBe(true)
  })

  it("orders workers by the channels they use, so links cross less", () => {
    const g = miniGraphLayout(team, 300, 132)
    const x = (id) => g.nodes.find((n) => n.id === id).x
    expect(g.channels.map((c) => c.name)).toEqual(["tasks", "reviews"])
    expect(x("coder")).toBeLessThan(x("reviewer"))
  })

  it("draws one elbow per creature–channel membership, marks privileged links, and the wire", () => {
    const g = miniGraphLayout(team, 300, 132)
    expect(g.links).toHaveLength(4)
    expect(g.links.filter((l) => l.control)).toHaveLength(2)
    expect(g.links.find((l) => l.id.includes("reviewer")).sends).toBe(false)
    expect(g.links.every((l) => /^M[\d.]+,[\d.]+ V[\d.]+ H[\d.]+ V[\d.]+$/.test(l.d))).toBe(true)
    expect(g.wires).toHaveLength(1)
  })

  it("centres a lone creature and wraps many workers into rows that still fit", () => {
    const solo = miniGraphLayout(sessionOf({ creatures: [creature("only")] }), 300, 132)
    expect(solo.nodes).toHaveLength(1)
    const n = solo.nodes[0]
    expect(n.x + n.w / 2).toBeCloseTo(150)
    expect(n.y + n.h / 2).toBeCloseTo(66)
    const crowd = sessionOf({
      channels: [{ name: "c" }],
      creatures: Array.from({ length: 12 }, (_, i) => creature(`w${i}`, { send_channels: ["c"] })),
    })
    const g = miniGraphLayout(crowd, 300, 132)
    expect(new Set(g.nodes.map((b) => b.y)).size).toBeGreaterThan(1)
    for (const box of g.nodes) expect(inside(box, 300, 132)).toBe(true)
  })

  it("lays a session out in detail while every creature and channel gets a readable box", () => {
    const g = miniGraphLayout(team, 380, 196)
    expect(g.mode).toBe("detailed")
    expect(g.groups).toEqual([])
    expect(g.nodes).toHaveLength(3)
  })

  it("folds a big session into one column per channel, every worker counted exactly once", () => {
    const names = Array.from({ length: 10 }, (_, i) => `ch${i}`)
    const big = sessionOf({
      channels: names.map((name) => ({ name })),
      creatures: [
        creature("lead", { is_privileged: true, send_channels: names }),
        ...Array.from({ length: 30 }, (_, i) =>
          creature(`w${i}`, { send_channels: [names[i % 10]], is_processing: i % 3 === 0 }),
        ),
        creature("lonely"),
      ],
    })
    const g = miniGraphLayout(big, 380, 196)
    expect(g.mode).toBe("grouped")
    expect(g.nodes.map((n) => n.id)).toEqual(["lead"])
    const workerGroups = g.groups.filter((x) => !x.privileged)
    expect(workerGroups.reduce((n, x) => n + x.count, 0)).toBe(31)
    for (const x of workerGroups) expect(x.dots.length + x.more).toBe(x.count)
    const ids = workerGroups.flatMap((x) => x.dots.map((d) => d.id))
    expect(new Set(ids).size).toBe(ids.length)
    const rest = g.channels.find((c) => c.name.startsWith("+"))
    const shown = g.channels.filter((c) => !c.name.startsWith("+"))
    expect(Number(rest.name.slice(1)) + shown.length).toBe(10)
    expect(workerGroups.find((x) => x.id === "group:+loose").dots.map((d) => d.id)).toEqual([
      "lonely",
    ])
    expect(g.links.filter((l) => l.control)).toHaveLength(g.channels.length)
    for (const box of [...g.nodes, ...g.channels, ...g.groups])
      expect(inside(box, 380, 196)).toBe(true)
    for (const d of workerGroups.flatMap((x) => x.dots)) {
      expect(d.y).toBeLessThanOrEqual(196 - 10)
    }
  })

  it("sizes each group to its dots and centres the grouped preview in the box", () => {
    const names = Array.from({ length: 8 }, (_, i) => `ch${i}`)
    const s = sessionOf({
      channels: names.map((name) => ({ name })),
      creatures: [
        creature("lead", { is_privileged: true, send_channels: names }),
        ...Array.from({ length: 16 }, (_, i) =>
          creature(`w${i}`, { send_channels: [names[i % 8]] }),
        ),
      ],
    })
    const g = miniGraphLayout(s, 380, 196)
    expect(g.mode).toBe("grouped")
    for (const x of g.groups) {
      for (const d of x.dots) expect(d.y).toBeLessThan(x.y + x.h)
      expect(x.y + x.h - Math.max(...x.dots.map((d) => d.y))).toBeLessThan(12)
    }
    const boxes = [...g.nodes, ...g.channels, ...g.groups]
    const topGap = Math.min(...boxes.map((b) => b.y))
    const bottomGap = 196 - Math.max(...boxes.map((b) => b.y + b.h))
    expect(Math.abs(topGap - bottomGap)).toBeLessThan(1)
  })

  it("folds privileged nodes into one group when they do not fit a row", () => {
    const crowd = sessionOf({
      channels: [{ name: "c" }],
      creatures: Array.from({ length: 8 }, (_, i) =>
        creature(`p${i}`, { is_privileged: true, send_channels: ["c"] }),
      ),
    })
    const g = miniGraphLayout(crowd, 300, 196)
    expect(g.mode).toBe("grouped")
    expect(g.nodes).toEqual([])
    const priv = g.groups.find((x) => x.privileged)
    expect(priv.count).toBe(8)
    expect(inside(priv, 300, 196)).toBe(true)
  })

  it("cuts labels to the space they have, and drops them when nothing readable fits", () => {
    expect(fitLabel("reviewer", 100, 5)).toBe("reviewer")
    expect(fitLabel("reviewer", 30, 5)).toBe("revie…")
    expect(fitLabel("reviewer", 14, 5)).toBe("")
  })
})
