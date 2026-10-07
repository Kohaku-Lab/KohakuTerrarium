import { describe, expect, it } from "vitest"

import { orthogonalRoute, roundedPath, routeMidpoint } from "./route"

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
