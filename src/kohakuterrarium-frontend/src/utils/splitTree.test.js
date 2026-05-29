import { describe, expect, it } from "vitest"

import {
  walkTree,
  firstLeafId,
  leafOrder,
  findLeafPath,
  splitLeaf,
  pruneLeaf,
  setRatioAt,
  leafTree,
  treeLeavesValid,
} from "./splitTree.js"

const leaf = (id) => ({ type: "leaf", id })
const hsplit = (ratio, l, r) => ({
  type: "split",
  direction: "horizontal",
  ratio,
  children: [l, r],
})
const vsplit = (ratio, t, b) => ({ type: "split", direction: "vertical", ratio, children: [t, b] })

describe("splitTree — walk / order / path", () => {
  it("walkTree visits leaves in reading order with paths", () => {
    const tree = hsplit(70, leaf("a"), vsplit(65, leaf("b"), leaf("c")))
    const seen = []
    walkTree(tree, (id, path) => seen.push([id, path.join("")]))
    expect(seen).toEqual([
      ["a", "0"],
      ["b", "10"],
      ["c", "11"],
    ])
  })

  it("walkTree no-ops on null", () => {
    const seen = []
    walkTree(null, (id) => seen.push(id))
    expect(seen).toEqual([])
  })

  it("firstLeafId / leafOrder", () => {
    const tree = hsplit(50, vsplit(50, leaf("x"), leaf("y")), leaf("z"))
    expect(firstLeafId(tree)).toBe("x")
    expect(leafOrder(tree)).toEqual(["x", "y", "z"])
    expect(firstLeafId(null)).toBeNull()
    expect(firstLeafId(leaf("solo"))).toBe("solo")
  })

  it("findLeafPath returns child-index path, or null when absent", () => {
    const tree = hsplit(50, leaf("a"), vsplit(50, leaf("b"), leaf("c")))
    expect(findLeafPath(tree, "a")).toEqual([0])
    expect(findLeafPath(tree, "b")).toEqual([1, 0])
    expect(findLeafPath(tree, "c")).toEqual([1, 1])
    expect(findLeafPath(tree, "nope")).toBeNull()
    expect(findLeafPath(leaf("a"), "a")).toEqual([])
  })
})

describe("splitTree — splitLeaf", () => {
  it("splits a single leaf, new leaf 'after' (right/bottom)", () => {
    const tree = leaf("a")
    const next = splitLeaf(tree, "a", "horizontal", "after", "b")
    expect(next).toEqual(hsplit(50, leaf("a"), leaf("b")))
  })

  it("splits with new leaf 'before' (left/top)", () => {
    const next = splitLeaf(leaf("a"), "a", "vertical", "before", "b")
    expect(next).toEqual(vsplit(50, leaf("b"), leaf("a")))
  })

  it("splits a nested leaf, leaving siblings intact", () => {
    const tree = hsplit(60, leaf("a"), leaf("b"))
    const next = splitLeaf(tree, "b", "vertical", "after", "c")
    expect(next).toEqual(hsplit(60, leaf("a"), vsplit(50, leaf("b"), leaf("c"))))
  })

  it("is a no-op when the target id is absent (returns same tree)", () => {
    const tree = hsplit(60, leaf("a"), leaf("b"))
    const next = splitLeaf(tree, "zzz", "horizontal", "after", "c")
    // structurally unchanged
    expect(leafOrder(next)).toEqual(["a", "b"])
  })

  it("does not mutate the input tree (immutable)", () => {
    const tree = leaf("a")
    splitLeaf(tree, "a", "horizontal", "after", "b")
    expect(tree).toEqual(leaf("a"))
  })
})

describe("splitTree — pruneLeaf", () => {
  it("removes a leaf and collapses the surviving sibling", () => {
    const tree = hsplit(60, leaf("a"), leaf("b"))
    expect(pruneLeaf(tree, "b")).toEqual(leaf("a"))
    expect(pruneLeaf(tree, "a")).toEqual(leaf("b"))
  })

  it("returns null when the last leaf is removed", () => {
    expect(pruneLeaf(leaf("a"), "a")).toBeNull()
  })

  it("collapses correctly in a deep tree", () => {
    const tree = hsplit(50, leaf("a"), vsplit(50, leaf("b"), leaf("c")))
    // remove b → the vsplit collapses to leaf c, parent keeps a | c
    expect(pruneLeaf(tree, "b")).toEqual(hsplit(50, leaf("a"), leaf("c")))
  })

  it("is a no-op for an absent id", () => {
    const tree = hsplit(50, leaf("a"), leaf("b"))
    expect(leafOrder(pruneLeaf(tree, "zzz"))).toEqual(["a", "b"])
  })
})

describe("splitTree — setRatioAt (in place)", () => {
  it("sets the root ratio at path []", () => {
    const tree = hsplit(50, leaf("a"), leaf("b"))
    setRatioAt(tree, [], 70)
    expect(tree.ratio).toBe(70)
  })

  it("sets a nested ratio and clamps to 10..90", () => {
    const tree = hsplit(50, leaf("a"), vsplit(50, leaf("b"), leaf("c")))
    setRatioAt(tree, [1], 5)
    expect(tree.children[1].ratio).toBe(10)
    setRatioAt(tree, [1], 999)
    expect(tree.children[1].ratio).toBe(90)
  })

  it("no-ops when the path does not point at a split", () => {
    const tree = hsplit(50, leaf("a"), leaf("b"))
    setRatioAt(tree, [0], 70) // child 0 is a leaf
    expect(tree.ratio).toBe(50)
    setRatioAt(null, [], 70) // defensive
  })
})

describe("splitTree — helpers", () => {
  it("leafTree builds a single-leaf node", () => {
    expect(leafTree("a")).toEqual({ type: "leaf", id: "a" })
  })

  it("treeLeavesValid checks membership (Set or array)", () => {
    const tree = hsplit(50, leaf("a"), leaf("b"))
    expect(treeLeavesValid(tree, new Set(["a", "b"]))).toBe(true)
    expect(treeLeavesValid(tree, ["a", "b", "c"])).toBe(true)
    expect(treeLeavesValid(tree, new Set(["a"]))).toBe(false)
    expect(treeLeavesValid(tree, [])).toBe(false)
  })
})
