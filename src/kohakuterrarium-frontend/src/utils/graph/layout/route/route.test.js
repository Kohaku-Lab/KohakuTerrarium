import { describe, expect, it } from "vitest"

import { segmentHitsBox } from "@/utils/graph/layout/metrics"

import { orthogonalRoute, roundedPath, routeMidpoint, sideRoute } from "./route"

const box = (x, y, width = 100, height = 40) => ({ x, y, width, height })
const axisAligned = (points) =>
  points.every((p, i) => i === 0 || p.x === points[i - 1].x || p.y === points[i - 1].y)

describe("orthogonal routes", () => {
  it("leaves the facing side and turns at the midline when the target is mostly to the right", () => {
    const route = orthogonalRoute(box(0, 0), box(300, 100))
    expect(route[0]).toEqual({ x: 100, y: 20 })
    expect(route[route.length - 1]).toEqual({ x: 300, y: 120 })
    expect(axisAligned(route)).toBe(true)
    expect(route).toHaveLength(4)
  })

  it("routes vertically when the target is mostly below, and straight when aligned", () => {
    const down = orthogonalRoute(box(0, 0), box(30, 300))
    expect(down[0].y).toBe(40)
    expect(down[down.length - 1].y).toBe(300)
    expect(axisAligned(down)).toBe(true)
    expect(orthogonalRoute(box(0, 0), box(300, 0))).toEqual([
      { x: 100, y: 20 },
      { x: 300, y: 20 },
    ])
  })

  it("shifts attach points by the offset but never off the node", () => {
    const shifted = orthogonalRoute(box(0, 0), box(300, 0), 10)
    expect(shifted[0].y).toBe(30)
    const clamped = orthogonalRoute(box(0, 0), box(300, 0), 500)
    expect(clamped[0].y).toBe(34)
  })

  it("enters a card from the side across a downward flow, in a lane outside it", () => {
    // Source above-left of the target: enters the target's left side below its input port.
    const left = sideRoute(box(0, 0), box(300, 200), "down")
    expect(left[left.length - 1]).toEqual({ x: 300, y: 232 })
    expect(left.some((p) => p.x === 282)).toBe(true)
    expect(axisAligned(left)).toBe(true)
    // Directly below: still a side entry, never the top edge where the flow comes in.
    const below = sideRoute(box(0, 0), box(0, 200), "down")
    expect(below[below.length - 1]).toEqual({ x: 0, y: 232 })
    // Source to the right: enters the right side.
    const right = sideRoute(box(500, 0), box(0, 200), "down")
    expect(right[right.length - 1]).toEqual({ x: 100, y: 220 })
    expect(axisAligned(right)).toBe(true)
  })

  it("enters a card from the top or bottom across a rightward flow", () => {
    const top = sideRoute(box(0, 0), box(300, 200), "right")
    expect(top[top.length - 1]).toEqual({ x: 350, y: 200 })
    expect(top.some((p) => p.y === 182)).toBe(true)
    const bottom = sideRoute(box(0, 300), box(300, 0), "right")
    expect(bottom[bottom.length - 1]).toEqual({ x: 350, y: 40 })
    expect(axisAligned(bottom)).toBe(true)
  })

  it("moves the run off a card it would cross, on either axis", () => {
    const source = box(600, 0)
    const target = box(0, 200)
    const blocker = box(250, 0)
    const crosses = (points, b) =>
      points.some((p, i) => i > 0 && segmentHitsBox([points[i - 1], p], b))
    expect(crosses(sideRoute(source, target, "down"), blocker)).toBe(true)
    const down = sideRoute(source, target, "down", [source, target, blocker])
    expect(crosses(down, blocker)).toBe(false)
    expect(down[down.length - 1]).toEqual({ x: 100, y: 220 })
    expect(axisAligned(down)).toBe(true)
    const t = (b) => ({ x: b.y, y: b.x, width: b.height, height: b.width })
    const right = sideRoute(t(source), t(target), "right", [t(source), t(target), t(blocker)])
    expect(crosses(right, t(blocker))).toBe(false)
    expect(right[right.length - 1]).toEqual({ x: 220, y: 100 })
  })

  it("draws rounded corners and finds the halfway point along the route", () => {
    const points = [
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      { x: 100, y: 100 },
    ]
    expect(roundedPath(points)).toMatch(/^M 0 0 L 92 0 Q 100 0 100 8 L 100 100$/)
    expect(routeMidpoint(points)).toEqual({ x: 100, y: 0 })
    expect(roundedPath([])).toBe("")
  })
})
