import { describe, expect, it } from "vitest"

import { presetLeafPanelIds } from "./presetTree.js"

const leaf = (panelId) => ({ type: "leaf", panelId })
const hsplit = (ratio, l, r) => ({
  type: "split",
  direction: "horizontal",
  ratio,
  children: [l, r],
})
const vsplit = (ratio, t, b) => ({
  type: "split",
  direction: "vertical",
  ratio,
  children: [t, b],
})

describe("presetLeafPanelIds", () => {
  it("returns empty for nullish preset", () => {
    expect(presetLeafPanelIds(null)).toEqual([])
    expect(presetLeafPanelIds(undefined)).toEqual([])
  })

  it("returns the panel id for a single-leaf tree", () => {
    expect(presetLeafPanelIds({ tree: leaf("chat") })).toEqual(["chat"])
  })

  it("walks splits in reading order (left/top before right/bottom)", () => {
    const preset = {
      tree: hsplit(70, leaf("chat"), vsplit(65, leaf("status"), leaf("modules"))),
    }
    expect(presetLeafPanelIds(preset)).toEqual(["chat", "status", "modules"])
  })

  it("handles deep nesting", () => {
    const preset = {
      tree: hsplit(
        20,
        leaf("files"),
        hsplit(62, leaf("editor"), vsplit(65, leaf("chat"), leaf("status"))),
      ),
    }
    expect(presetLeafPanelIds(preset)).toEqual(["files", "editor", "chat", "status"])
  })

  it("falls back to slots for legacy zone+slot presets", () => {
    const preset = {
      slots: [
        { zoneId: "main", panelId: "chat" },
        { zoneId: "aux", panelId: "status" },
      ],
    }
    expect(presetLeafPanelIds(preset)).toEqual(["chat", "status"])
  })

  it("skips malformed nodes silently", () => {
    const preset = {
      tree: {
        type: "split",
        direction: "horizontal",
        ratio: 50,
        children: [leaf("a"), null],
      },
    }
    expect(presetLeafPanelIds(preset)).toEqual(["a"])
  })
})
