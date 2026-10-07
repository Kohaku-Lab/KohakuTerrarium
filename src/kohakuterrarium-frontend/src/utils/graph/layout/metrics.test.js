import { describe, expect, it } from "vitest"

import {
  fittedArea,
  inkLength,
  measureLayout,
  segmentHitsBox,
  segmentsCross,
  sharedRun,
} from "./metrics"

const p = (x, y) => ({ x, y })
const box = (x, y, width, height) => ({ x, y, width, height })

describe("layout metrics", () => {
  it("counts a proper crossing but not segments that only share an endpoint", () => {
    expect(segmentsCross([p(0, 0), p(10, 10)], [p(0, 10), p(10, 0)])).toBe(true)
    expect(segmentsCross([p(0, 0), p(10, 0)], [p(10, 0), p(10, 10)])).toBe(false)
    expect(segmentsCross([p(0, 0), p(10, 0)], [p(0, 5), p(10, 5)])).toBe(false)
  })

  it("detects a segment passing through a box interior", () => {
    expect(segmentHitsBox([p(0, 50), p(200, 50)], box(80, 30, 40, 40))).toBe(true)
    expect(segmentHitsBox([p(0, 10), p(200, 10)], box(80, 30, 40, 40))).toBe(false)
    expect(segmentHitsBox([p(80, 31), p(80, 60)], box(80, 30, 40, 40))).toBe(false)
  })

  it("measures crossings, node hits, overlap, length and area of a drawing", () => {
    const boxes = new Map([
      ["a", box(0, 0, 10, 10)],
      ["b", box(100, 100, 10, 10)],
      ["c", box(0, 100, 10, 10)],
      ["d", box(100, 0, 10, 10)],
      ["m", box(50, 50, 10, 10)],
    ])
    const routes = [
      { id: "ab", source: "a", target: "b", points: [p(10, 10), p(100, 100)] },
      { id: "cd", source: "c", target: "d", points: [p(10, 100), p(100, 10)] },
    ]
    const m = measureLayout(boxes, routes)
    expect(m.crossings).toBe(1)
    expect(m.nodeHits).toBe(2)
    expect(m.overlap).toBe(0)
    expect(m.area).toBe(110 * 110)
    expect(m.length).toBe(Math.round(2 * Math.hypot(90, 90)))
  })

  it("fits the drawing into the smallest viewport-shaped box", () => {
    expect(fittedArea(400, 100, 4)).toBe(400 * 100)
    expect(fittedArea(400, 100, 1)).toBe(400 * 400)
    expect(fittedArea(100, 400, 2)).toBe(800 * 400)
    expect(fittedArea(300, 200, null)).toBe(300 * 200)
  })

  it("scores the same area cheaper when its shape matches the viewport", () => {
    const wide = new Map([["a", box(0, 0, 400, 100)]])
    const tall = new Map([["a", box(0, 0, 100, 400)]])
    const inWide = (b) => measureLayout(b, [], { aspect: 4 })
    expect(inWide(wide).area).toBe(inWide(tall).area)
    expect(inWide(wide).cost).toBeLessThan(inWide(tall).cost)
    expect(inWide(wide)).toMatchObject({ width: 400, height: 100, aspect: 4, fitArea: 40000 })
    expect(measureLayout(tall, [], { aspect: 0.25 }).cost).toBe(inWide(wide).cost)
  })

  it("penalises unrelated edges running on top of each other, but not a fan-out or fan-in", () => {
    const boxes = new Map([
      ["a", box(0, 0, 10, 10)],
      ["b", box(0, 100, 10, 10)],
      ["c", box(300, 0, 10, 10)],
      ["d", box(300, 100, 10, 10)],
    ])
    const along = (y0, y1) => [p(150, y0), p(150, y1)]
    const unrelated = measureLayout(boxes, [
      { id: "ac", source: "a", target: "c", points: along(0, 100) },
      { id: "bd", source: "b", target: "d", points: along(20, 120) },
    ])
    const fanOut = measureLayout(boxes, [
      { id: "ac", source: "a", target: "c", points: along(0, 100) },
      { id: "ad", source: "a", target: "d", points: along(20, 120) },
    ])
    const fanIn = measureLayout(boxes, [
      { id: "ac", source: "a", target: "c", points: along(0, 100) },
      { id: "bc", source: "b", target: "c", points: along(20, 120) },
    ])
    expect(unrelated.merged).toBe(80)
    expect(fanOut.merged).toBe(0)
    expect(fanIn.merged).toBe(0)
    expect(unrelated.cost).toBeGreaterThan(fanOut.cost)
    expect(sharedRun([p(0, 5), p(50, 5)], [p(40, 5), p(90, 5)])).toBe(10)
    expect(sharedRun([p(0, 5), p(50, 5)], [p(0, 6), p(50, 6)])).toBe(0)
  })

  it("counts a shared fan-in trunk as ink once, so a bundled fan-in is cheaper than parallel lines", () => {
    const seg = (x0, y0, x1, y1) => [
      { x: x0, y: y0 },
      { x: x1, y: y1 },
    ]
    expect(inkLength([seg(0, 0, 100, 0), seg(50, 0, 150, 0), seg(200, 0, 300, 0)])).toBe(250)
    expect(inkLength([seg(0, 0, 0, 100), seg(1, 0, 1, 100)])).toBe(200)
    expect(inkLength([seg(0, 0, 30, 40)])).toBe(50)
    const boxes = new Map([
      ["t", { x: 0, y: 0, width: 100, height: 40 }],
      ["a", { x: 300, y: 100, width: 100, height: 40 }],
      ["b", { x: 300, y: 200, width: 100, height: 40 }],
    ])
    const route = (source, points) => ({ id: source, source, target: "t", points })
    const trunk = [
      route("a", [
        { x: 300, y: 120 },
        { x: 200, y: 120 },
        { x: 200, y: 20 },
        { x: 100, y: 20 },
      ]),
      route("b", [
        { x: 300, y: 220 },
        { x: 200, y: 220 },
        { x: 200, y: 20 },
        { x: 100, y: 20 },
      ]),
    ]
    const parallel = [
      trunk[0],
      route("b", [
        { x: 300, y: 220 },
        { x: 210, y: 220 },
        { x: 210, y: 30 },
        { x: 100, y: 30 },
      ]),
    ]
    const a = measureLayout(boxes, trunk)
    const b = measureLayout(boxes, parallel)
    expect(a.length).toBeLessThanOrEqual(b.length + 20)
    expect(a.ink).toBeLessThan(b.ink)
    expect(a.cost).toBeLessThan(b.cost)
  })

  it("counts the same crossings, shared runs and node hits as a brute-force pass", () => {
    let seed = 11
    const rand = () => {
      seed = (seed * 16807) % 2147483647
      return seed / 2147483647
    }
    const boxes = new Map()
    for (let i = 0; i < 30; i++)
      boxes.set(`n${i}`, box(Math.round(rand() * 2000), Math.round(rand() * 1200), 120, 50))
    const ids = [...boxes.keys()]
    const routes = []
    for (let r = 0; r < 60; r++) {
      const points = [p(Math.round(rand() * 2000), Math.round(rand() * 1200))]
      for (let k = 0; k < 3; k++) {
        const last = points[points.length - 1]
        points.push(
          k % 2 ? p(last.x, Math.round(rand() * 40) * 30) : p(Math.round(rand() * 66) * 30, last.y),
        )
      }
      routes.push({ id: `r${r}`, source: ids[r % 30], target: ids[(r * 7) % 30], points })
    }
    const segs = routes.flatMap((r) =>
      r.points.slice(1).map((q, i) => ({ r, s: [r.points[i], q] })),
    )
    let crossings = 0
    let merged = 0
    for (let i = 0; i < segs.length; i++)
      for (let j = i + 1; j < segs.length; j++) {
        if (segs[i].r === segs[j].r) continue
        if (segmentsCross(segs[i].s, segs[j].s)) crossings += 1
        if (segs[i].r.source !== segs[j].r.source && segs[i].r.target !== segs[j].r.target)
          merged += sharedRun(segs[i].s, segs[j].s)
      }
    let nodeHits = 0
    for (const { r, s } of segs)
      for (const [id, b] of boxes)
        if (id !== r.source && id !== r.target && segmentHitsBox(s, b)) nodeHits += 1
    const m = measureLayout(boxes, routes)
    expect(crossings).toBeGreaterThan(0)
    expect(m.crossings).toBe(crossings)
    expect(m.merged).toBe(Math.round(merged))
    expect(m.nodeHits).toBe(nodeHits)
  })

  it("still prefers the smaller drawing when the fitted area ties", () => {
    const compact = new Map([["a", box(0, 0, 1600, 500)]])
    const taller = new Map([["a", box(0, 0, 1600, 900)]])
    const m = (b) => measureLayout(b, [], { aspect: 1.6 })
    expect(m(compact).fitArea).toBe(m(taller).fitArea)
    expect(m(compact).cost).toBeLessThan(m(taller).cost)
  })

  it("ranks a drawing with fewer crossings and overlaps as cheaper", () => {
    const boxes = new Map([
      ["a", box(0, 0, 10, 10)],
      ["b", box(5, 5, 10, 10)],
    ])
    const overlapping = measureLayout(boxes, [])
    const apart = measureLayout(
      new Map([
        ["a", box(0, 0, 10, 10)],
        ["b", box(40, 0, 10, 10)],
      ]),
      [],
    )
    expect(overlapping.overlap).toBe(25)
    expect(apart.cost).toBeLessThan(overlapping.cost)
  })
})
