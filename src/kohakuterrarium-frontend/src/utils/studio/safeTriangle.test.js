import { describe, expect, it } from "vitest"

import { pointInRect, pointInTriangle, safeTriangleBase } from "./safeTriangle"

describe("pointInTriangle", () => {
  // Triangle with vertex at origin (100, 100), base at destination
  // (500, 80) and (500, 400). Mimics the "pool → detail panel" layout:
  // user hovered a pool item on the left; detail panel sits to the
  // right. Mouse moves right + vertically stays within the wedge.
  const origin = { x: 100, y: 100 }
  const topBase = { x: 500, y: 80 }
  const bottomBase = { x: 500, y: 400 }

  it("accepts a point clearly inside the wedge", () => {
    expect(pointInTriangle({ x: 300, y: 200 }, origin, topBase, bottomBase)).toBe(true)
  })

  it("accepts the origin itself", () => {
    expect(pointInTriangle(origin, origin, topBase, bottomBase)).toBe(true)
  })

  it("accepts the base edge midpoint", () => {
    expect(pointInTriangle({ x: 500, y: 200 }, origin, topBase, bottomBase)).toBe(true)
  })

  it("rejects a point below the wedge", () => {
    expect(pointInTriangle({ x: 300, y: 500 }, origin, topBase, bottomBase)).toBe(false)
  })

  it("rejects a point behind the origin", () => {
    expect(pointInTriangle({ x: 50, y: 200 }, origin, topBase, bottomBase)).toBe(false)
  })

  it("rejects a point above the wedge", () => {
    expect(pointInTriangle({ x: 300, y: 0 }, origin, topBase, bottomBase)).toBe(false)
  })

  it("rejects a point past the destination", () => {
    expect(pointInTriangle({ x: 700, y: 200 }, origin, topBase, bottomBase)).toBe(false)
  })
})

describe("safeTriangleBase", () => {
  it("returns the left-edge endpoints of the destination rect", () => {
    const rect = { left: 800, top: 100, right: 1100, bottom: 700 }
    const [top, bottom] = safeTriangleBase(rect)
    expect(top).toEqual({ x: 800, y: 100 })
    expect(bottom).toEqual({ x: 800, y: 700 })
  })
})

describe("pointInRect", () => {
  const rect = { left: 200, top: 100, right: 400, bottom: 300 }

  it("accepts a point inside", () => {
    expect(pointInRect({ x: 300, y: 200 }, rect)).toBe(true)
  })

  it("accepts a corner", () => {
    expect(pointInRect({ x: 200, y: 100 }, rect)).toBe(true)
  })

  it("rejects a point to the right", () => {
    expect(pointInRect({ x: 500, y: 200 }, rect)).toBe(false)
  })

  it("rejects a point above", () => {
    expect(pointInRect({ x: 300, y: 50 }, rect)).toBe(false)
  })
})
