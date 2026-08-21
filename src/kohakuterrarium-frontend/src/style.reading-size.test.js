import { readFileSync } from "node:fs"
import { describe, expect, it } from "vitest"

import settingsPageSource from "@/components/settings/SettingsPage.vue?raw"

const styleSource = readFileSync("src/style.css", "utf8")

describe("reading-size CSS contracts", () => {
  it("keeps the touch input floor while allowing larger reading text", () => {
    const coarsePointerRule = styleSource.match(
      /@media \(pointer: coarse\) \{[\s\S]*?\[contenteditable="true"\] \{[\s\S]*?\n  \}\n\}/,
    )?.[0]

    expect(coarsePointerRule).toContain("font-size: max(16px, 1rem) !important;")
    expect(coarsePointerRule).not.toContain("font-size: 16px !important;")
  })

  it("scales the reading-size hint with the root reading size", () => {
    const readingSizeBlock = settingsPageSource.match(
      /<div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-3">[\s\S]*?settings\.prefs\.readingSizeHint[\s\S]*?<\/div>/,
    )?.[0]

    expect(readingSizeBlock).toContain('class="kt-text-caption text-warm-400 mt-1"')
    expect(readingSizeBlock).not.toContain('class="text-[11px] text-warm-400 mt-1"')
  })
})
