import { readFileSync } from "node:fs"
import { describe, expect, it } from "vitest"

import { blockHash, blockIdsFor } from "./blockId.js"
import { splitMarkup } from "./markup.js"

const FIXTURE = JSON.parse(readFileSync("../../tests/fixtures/kt_ui_markup.json", "utf-8"))

describe("inline UI block ids", () => {
  it("hash is FNV-1a of the UTF-8 source, like the Python side", () => {
    expect(blockHash("")).toBe("811c9dc5")
    expect(blockHash("a")).toBe("e40c292c")
    expect(blockHash("é")).toBe("1e9de8c1")
  })

  it("numbers identical blocks and skips open blocks and markdown", () => {
    const block = "<ui><text>x</text></ui>"
    const segments = splitMarkup(`${block}\n${block}\nend <ui><text>`)
    const digest = blockHash(block)
    const ids = blockIdsFor(segments)
    expect(ids).toHaveLength(segments.length)
    expect(ids.filter(Boolean)).toEqual([`${digest}.0`, `${digest}.1`])
    segments.forEach((segment, i) =>
      expect(ids[i] !== null).toBe(segment.kind === "ui" && segment.closed),
    )
  })

  it("closed blocks of the shared split fixture hash their exact source", () => {
    for (const testCase of FIXTURE.split) {
      const expected = testCase.segments
        .filter((s) => s.kind === "ui" && s.closed)
        .map((s) => `${blockHash(s.source)}`)
      const ids = blockIdsFor(splitMarkup(testCase.text)).filter(Boolean)
      expect(
        ids.map((id) => id.split(".")[0]),
        testCase.name,
      ).toEqual(expected)
    }
  })
})
