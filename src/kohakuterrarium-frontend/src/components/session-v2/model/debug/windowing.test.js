import { describe, expect, it } from "vitest"

import { scrollTopFor, visibleRange } from "./windowing"

describe("visibleRange", () => {
  it("mounts only the rows in view plus overscan, with spacers summing to the full height", () => {
    const r = visibleRange({
      scrollTop: 2800,
      viewportHeight: 560,
      rowHeight: 28,
      count: 10_000,
      overscan: 5,
    })
    expect(r.start).toBe(95)
    expect(r.end).toBe(126)
    expect(r.end - r.start).toBeLessThan(40)
    expect(r.padTop + (r.end - r.start) * 28 + r.padBottom).toBe(10_000 * 28)
  })

  it("clamps at both ends and handles empty lists", () => {
    expect(visibleRange({ scrollTop: 0, viewportHeight: 100, rowHeight: 20, count: 3 })).toEqual({
      start: 0,
      end: 3,
      padTop: 0,
      padBottom: 0,
    })
    const bottom = visibleRange({
      scrollTop: 99_999,
      viewportHeight: 100,
      rowHeight: 20,
      count: 50,
      overscan: 2,
    })
    expect(bottom.end).toBe(50)
    expect(bottom.padBottom).toBe(0)
    expect(bottom.start).toBe(43)
    expect(bottom.padTop + (bottom.end - bottom.start) * 20).toBe(50 * 20)
    expect(visibleRange({ rowHeight: 20, count: 0 })).toEqual({
      start: 0,
      end: 0,
      padTop: 0,
      padBottom: 0,
    })
  })
})

describe("scrollTopFor", () => {
  const view = { scrollTop: 200, viewportHeight: 100, rowHeight: 20 }
  it("scrolls up or down just enough, and not at all when visible", () => {
    expect(scrollTopFor(5, view)).toBe(100)
    expect(scrollTopFor(20, view)).toBe(320)
    expect(scrollTopFor(11, view)).toBe(null)
  })
})
