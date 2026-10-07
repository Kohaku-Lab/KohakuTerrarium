import { describe, expect, it } from "vitest"

import { edgeBends } from "./edges"

const box = (x, y) => ({ x, y, width: 100, height: 40 })

describe("edge bends", () => {
  it("fans out several edges between the same pair and leaves single edges straight", () => {
    const bends = edgeBends([
      { id: "ab1", kind: "via", source: "a", target: "b" },
      { id: "ab2", kind: "via", source: "a", target: "b" },
      { id: "ba", kind: "via", source: "b", target: "a" },
      { id: "ac", kind: "via", source: "a", target: "c" },
    ])
    expect(bends.has("ac")).toBe(false)
    expect(new Set([bends.get("ab1"), bends.get("ab2"), -bends.get("ba")]).size).toBe(3)
  })

  it("bows a wire away from the centre of the drawing", () => {
    const boxes = new Map([
      ["a", box(0, 0)],
      ["b", box(400, 0)],
      ["mid", box(200, 300)],
    ])
    const top = edgeBends([{ id: "w", kind: "wire", source: "a", target: "b" }], boxes).get("w")
    // a → b runs left to right; its left-hand normal points down, toward the centre, so it bows the other way.
    expect(top).toBeLessThan(0)
    const reversed = edgeBends([{ id: "w", kind: "wire", source: "b", target: "a" }], boxes).get(
      "w",
    )
    expect(reversed).toBeGreaterThan(0)
  })
})
