import { describe, expect, it } from "vitest"

import { splitFences } from "@/ext/fenceSplit"

const IDS = ["pkg.poll"]

describe("splitFences", () => {
  it("returns the text untouched without renderer ids or fences", () => {
    expect(splitFences("a\n```js\nx\n```", [])).toEqual([
      { kind: "markdown", text: "a\n```js\nx\n```" },
    ])
    expect(splitFences("plain", IDS)).toEqual([{ kind: "markdown", text: "plain" }])
  })

  it("splits a closed fence named after a renderer from the text around it", () => {
    const text = 'before\n```pkg.poll\n{"q":"x"}\n```\nafter'
    expect(splitFences(text, IDS)).toEqual([
      { kind: "markdown", text: "before" },
      { kind: "fence", id: "pkg.poll", source: '{"q":"x"}' },
      { kind: "markdown", text: "after" },
    ])
  })

  it("keeps a fence that is still streaming as plain text", () => {
    const text = 'intro\n```pkg.poll\n{"q":'
    expect(splitFences(text, IDS)).toEqual([{ kind: "markdown", text }])
    const closedLater = splitFences(`${text}"x"}\n\`\`\``, IDS)
    expect(closedLater.map((s) => s.kind)).toEqual(["markdown", "fence"])
  })

  it("leaves other languages and unrelated fences as text", () => {
    const text = "```json\n{}\n```\n```pkg.other\nx\n```"
    expect(splitFences(text, IDS)).toEqual([{ kind: "markdown", text }])
  })

  it("does not treat a renderer fence nested in a longer fence as a block", () => {
    const text = "````markdown\n```pkg.poll\nx\n```\n````"
    expect(splitFences(text, IDS)).toEqual([{ kind: "markdown", text }])
  })

  it("requires a closing fence at least as long, of the same character", () => {
    const text = "````pkg.poll\nbody\n```\nstill body\n````"
    expect(splitFences(text, IDS)).toEqual([
      { kind: "fence", id: "pkg.poll", source: "body\n```\nstill body" },
    ])
    const tildes = "~~~pkg.poll\nx\n```\n~~~"
    expect(splitFences(tildes, IDS)).toEqual([{ kind: "fence", id: "pkg.poll", source: "x\n```" }])
  })

  it("accepts an info string with extra words and CRLF line endings", () => {
    const text = "```pkg.poll title=x\r\nbody\r\n```\r\n"
    const parts = splitFences(text, IDS)
    expect(parts[0]).toEqual({ kind: "fence", id: "pkg.poll", source: "body" })
  })

  it("ignores indented code that only looks like a fence", () => {
    const text = "    ```pkg.poll\n    x\n    ```"
    expect(splitFences(text, IDS)).toEqual([{ kind: "markdown", text }])
  })

  it("finds several blocks in one reply", () => {
    const text = "```pkg.poll\na\n```\nmid\n```pkg.poll\nb\n```"
    expect(splitFences(text, IDS).map((s) => s.source ?? s.text)).toEqual(["a", "mid", "b"])
  })

  it("never drops or reorders text", () => {
    const text = "one\n```js\ncode\n```\ntwo\n```pkg.poll\nx\n```\nthree"
    const rebuilt = splitFences(text, IDS)
      .map((s) => (s.kind === "fence" ? "```pkg.poll\n" + s.source + "\n```" : s.text))
      .join("\n")
    expect(rebuilt).toBe(text)
  })
})
