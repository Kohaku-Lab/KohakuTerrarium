import { describe, expect, it } from "vitest"

import { fitViewport } from "./fit"

const canvas = { width: 1000, height: 800 }
const map = { width: 200, height: 150, right: 15, bottom: 15 }
const onScreen = (vp, b) => ({
  x0: b.x * vp.zoom + vp.x,
  y0: b.y * vp.zoom + vp.y,
  x1: (b.x + b.width) * vp.zoom + vp.x,
  y1: (b.y + b.height) * vp.zoom + vp.y,
})

describe("fitViewport", () => {
  it("fits the whole canvas when the drawing clears the minimap", () => {
    const small = { x: 0, y: 0, width: 200, height: 100 }
    const vp = fitViewport(small, canvas, map, 0.55)
    expect(vp.zoom).toBe(1.1)
    const s = onScreen(vp, small)
    expect((s.x0 + s.x1) / 2).toBeCloseTo(500)
    expect((s.y0 + s.y1) / 2).toBeCloseTo(400)
  })

  it("keeps a drawing that would reach under the minimap out of its corner", () => {
    const big = { x: 100, y: 50, width: 900, height: 700 }
    const vp = fitViewport(big, canvas, map, 0.3)
    const s = onScreen(vp, big)
    const mapLeft = canvas.width - map.right - map.width
    const mapTop = canvas.height - map.bottom - map.height
    expect(s.x1 <= mapLeft || s.y1 <= mapTop).toBe(true)
    expect(s.x0).toBeGreaterThanOrEqual(0)
    expect(s.y0).toBeGreaterThanOrEqual(0)
  })

  it("never zooms below the legible floor, and then keeps the drawing's top-left in view", () => {
    const huge = { x: 40, y: -30, width: 9000, height: 9000 }
    for (const m of [map, null]) {
      const vp = fitViewport(huge, canvas, m, 0.55)
      expect(vp.zoom).toBe(0.55)
      const s = onScreen(vp, huge)
      expect(s.x0).toBeGreaterThanOrEqual(0)
      expect(s.y0).toBeGreaterThanOrEqual(0)
      expect(s.y0).toBeLessThan(40)
    }
  })
})
