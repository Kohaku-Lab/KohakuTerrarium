import MarkdownIt from "markdown-it"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { IncrementalMarkdownRenderer, splitMarkdownBlocks } from "./markdownIncremental.js"

const md = new MarkdownIt({ html: false, linkify: true, typographer: false })

function normalize(html) {
  return html.replace(/\n+/g, "\n").trim()
}

function assertRenderEquivalence(text) {
  const renderer = new IncrementalMarkdownRenderer((t) => md.render(t))
  expect(normalize(renderer.render(text))).toBe(normalize(md.render(text)))
}

describe("splitMarkdownBlocks", () => {
  it("returns [] for empty input", () => {
    expect(splitMarkdownBlocks("")).toEqual([])
    expect(splitMarkdownBlocks(null)).toEqual([])
  })

  it("splits paragraphs at blank lines", () => {
    expect(splitMarkdownBlocks("alpha\n\nbeta")).toEqual(["alpha", "beta"])
  })

  it("collapses blank-line runs and drops leading/trailing blanks", () => {
    expect(splitMarkdownBlocks("\n\none\n\n\n\ntwo\n\n")).toEqual(["one", "two"])
  })

  it("keeps fenced code with internal blank lines as one block", () => {
    const text = ["para", "", "```js", "const a = 1", "", "const b = 2", "```", "", "tail"].join(
      "\n",
    )
    expect(splitMarkdownBlocks(text)).toEqual([
      "para",
      "```js\nconst a = 1\n\nconst b = 2\n```",
      "tail",
    ])
  })

  it("keeps an unterminated fence as one block to the end", () => {
    const text = ["```py", "x = 1", "", "y = 2"].join("\n")
    expect(splitMarkdownBlocks(text)).toEqual(["```py\nx = 1\n\ny = 2"])
  })

  it("does not split inside $$ display math", () => {
    const text = ["intro", "", "$$", "a + b", "", "= c", "$$", "", "outro"].join("\n")
    expect(splitMarkdownBlocks(text)).toEqual(["intro", "$$\na + b\n\n= c\n$$", "outro"])
  })

  it("merges adjacent same-marker unordered list blocks", () => {
    expect(splitMarkdownBlocks("- one\n\n- two")).toEqual(["- one\n\n- two"])
  })

  it("does not merge mixed unordered markers", () => {
    expect(splitMarkdownBlocks("- one\n\n* two")).toEqual(["- one", "* two"])
  })

  it("merges adjacent ordered list blocks", () => {
    expect(splitMarkdownBlocks("1. one\n\n2. two")).toEqual(["1. one\n\n2. two"])
  })

  it("does not treat emphasis or negative numbers as list starts", () => {
    expect(splitMarkdownBlocks("*emphasis*\n\n-1 degrees")).toEqual(["*emphasis*", "-1 degrees"])
  })

  it("returns null for reference-style link definitions", () => {
    expect(splitMarkdownBlocks("see [ref]\n\n[ref]: https://example.com")).toBeNull()
  })

  it("ignores reference-like lines inside code fences", () => {
    const text = "```\n[not]: a ref\n```\n\npara"
    expect(splitMarkdownBlocks(text)).toEqual(["```\n[not]: a ref\n```", "para"])
  })
})

describe("IncrementalMarkdownRenderer equivalence", () => {
  it("matches whole-document rendering for paragraphs", () => {
    assertRenderEquivalence("first paragraph\n\nsecond paragraph")
  })

  it("matches for headings, lists, tables, quotes, and rules", () => {
    assertRenderEquivalence(
      "# Title\n\n- a\n- b\n\n> quoted\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n---\n\n1. one\n\n2. two",
    )
  })

  it("matches for fenced code with blank lines inside", () => {
    assertRenderEquivalence("before\n\n```js\nconst a = 1\n\nconst b = 2\n```\n\nafter")
  })

  it("matches for display math blocks", () => {
    assertRenderEquivalence("text\n\n$$\nx^2\n$$\n\ntail")
  })

  it("matches for unterminated fence during streaming", () => {
    assertRenderEquivalence("text\n\n```python\ndef f():")
  })

  it("matches when blocks need no merging and no trailing newline", () => {
    assertRenderEquivalence("single")
  })

  it("falls back to a full render for reference definitions", () => {
    const renderFn = vi.fn((t) => md.render(t))
    const renderer = new IncrementalMarkdownRenderer(renderFn)
    const text = "see [ref]\n\n[ref]: https://example.com"
    renderer.render(text)
    expect(renderFn).toHaveBeenCalledWith(text)
  })
})

describe("IncrementalMarkdownRenderer caching", () => {
  let renderFn
  let renderer

  beforeEach(() => {
    renderFn = vi.fn((t) => md.render(t))
    renderer = new IncrementalMarkdownRenderer(renderFn)
  })

  it("re-renders only the changed tail block on append", () => {
    renderer.render("one\n\ntwo")
    expect(renderFn).toHaveBeenCalledTimes(2)
    renderer.render("one\n\ntwo three")
    expect(renderFn).toHaveBeenCalledTimes(3)
    expect(renderFn).toHaveBeenLastCalledWith("two three")
  })

  it("re-renders one block when a new block is appended", () => {
    renderer.render("one")
    renderFn.mockClear()
    renderer.render("one\n\ntwo")
    expect(renderFn).toHaveBeenCalledTimes(1)
    expect(renderFn).toHaveBeenCalledWith("two")
  })

  it("re-renders from the first changed block on a middle edit", () => {
    renderer.render("one\n\ntwo\n\nthree")
    renderFn.mockClear()
    renderer.render("ONE\n\ntwo\n\nthree")
    expect(renderFn).toHaveBeenCalledTimes(3)
  })

  it("truncates the cache when the document shrinks", () => {
    renderer.render("one\n\ntwo\n\nthree")
    renderFn.mockClear()
    renderer.render("one")
    expect(renderFn).toHaveBeenCalledTimes(0)
    expect(renderer.render("one")).toBe(md.render("one"))
  })

  it("reset drops the cache", () => {
    renderer.render("one\n\ntwo")
    renderer.reset()
    renderFn.mockClear()
    renderer.render("one\n\ntwo")
    expect(renderFn).toHaveBeenCalledTimes(2)
  })
})
