import { readFileSync } from "node:fs"
import { describe, expect, it } from "vitest"

import moduleEditFormSource from "@/components/panels/modules/ModuleEditForm.vue?raw"
import modulesPanelSource from "@/components/panels/modules/ModulesPanel.vue?raw"

const styleSource = readFileSync("src/style.css", "utf8")

describe("descriptive hint tooltip contracts", () => {
  it("marks every module documentation tooltip with the bounded hint class", () => {
    const tooltipTags = `${modulesPanelSource}\n${moduleEditFormSource}`.match(
      /<el-tooltip\b[^>]*>/g,
    )

    expect(tooltipTags).toHaveLength(3)
    for (const tag of tooltipTags) {
      expect(tag).toContain('popper-class="kt-hint-tooltip"')
    }
  })

  it("keeps long hints inside the viewport and permits wrapping", () => {
    const hintRule = styleSource.match(/\.el-popper\.kt-hint-tooltip \{[\s\S]*?\n\}/)?.[0]

    expect(hintRule).toContain("box-sizing: border-box;")
    expect(hintRule).toContain("max-width: min(28rem, calc(100vw - 24px));")
    expect(hintRule).toContain("white-space: normal;")
    expect(hintRule).toContain("overflow-wrap: anywhere;")
    expect(hintRule).toContain("word-break: break-word;")
  })
})
