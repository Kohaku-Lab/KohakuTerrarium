import { describe, expect, it } from "vitest"

import { canExpand, flattenTree, sortedChildren, visibleRange } from "./treeRows"

const dir = (path, children, extra = {}) => ({
  path,
  name: path.split("/").pop(),
  type: "directory",
  children,
  ...extra,
})
const file = (path) => ({ path, name: path.split("/").pop(), type: "file" })

describe("flattenTree", () => {
  const root = dir("/r", [
    file("/r/b.txt"),
    dir("/r/src", [file("/r/src/z.js"), file("/r/src/a.js")]),
    file("/r/a.txt"),
  ])

  it("lists directories first, then files by name, and only descends into expanded directories", () => {
    expect(flattenTree(root, new Set()).map((r) => r.node.path)).toEqual([
      "/r/src",
      "/r/a.txt",
      "/r/b.txt",
    ])
    const open = flattenTree(root, new Set(["/r/src"]))
    expect(open.map((r) => [r.node.path, r.depth])).toEqual([
      ["/r/src", 0],
      ["/r/src/a.js", 1],
      ["/r/src/z.js", 1],
      ["/r/a.txt", 0],
      ["/r/b.txt", 0],
    ])
  })

  it("sorts each children array once and does not mutate it", () => {
    const first = sortedChildren(root)
    expect(sortedChildren(root)).toBe(first)
    expect(root.children[0].path).toBe("/r/b.txt")
  })

  it("expands by advertised has_children, else by loaded children", () => {
    expect(canExpand(dir("/d", [], { has_children: true }))).toBe(true)
    expect(canExpand(dir("/d", [file("/d/x")]))).toBe(true)
    expect(canExpand(dir("/d", []))).toBe(false)
    expect(canExpand(file("/f"))).toBe(false)
  })
})

describe("visibleRange", () => {
  it("windows the rows around the viewport with overscan, clamped to the list", () => {
    expect(visibleRange(1000, 2400, 480, 24, 8)).toEqual([92, 128])
    expect(visibleRange(10, 0, 480, 24, 8)).toEqual([0, 10])
    expect(visibleRange(0, 0, 480, 24)).toEqual([0, 0])
  })

  it("still shows the last rows when the scroll offset outlives a shrunken list", () => {
    expect(visibleRange(30, 24_000, 480, 24, 8)).toEqual([2, 30])
  })
})
