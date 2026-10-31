import { readFileSync } from "node:fs"
import { describe, expect, it } from "vitest"

import settingsRowSource from "@/components/settings/ui/SettingsRow.vue?raw"

const styleSource = readFileSync("src/style.css", "utf8")

describe("reading-size CSS contracts", () => {
  it("keeps the touch input floor while allowing larger reading text", () => {
    const coarsePointerRule = styleSource.match(
      /@media \(pointer: coarse\) \{[\s\S]*?\[contenteditable="true"\] \{[\s\S]*?\n  \}\n\}/,
    )?.[0]

    expect(coarsePointerRule).toContain("font-size: max(16px, 1rem) !important;")
    expect(coarsePointerRule).not.toContain("font-size: 16px !important;")
  })

  it("scales a setting's title and hint with the root reading size", () => {
    expect(settingsRowSource).toContain("kt-text-body")
    expect(settingsRowSource).toContain("kt-text-caption")
    expect(settingsRowSource).not.toMatch(/text-\[\d+px\]/)
  })

  it("makes 16px the default root size, with 14px as the smaller scale", () => {
    expect(styleSource).toContain("font-size: var(--kt-reading-root-size, 16px);")
    expect(styleSource).toMatch(
      /html\[data-reading-size="smaller"\] \{\s*--kt-reading-root-size: 14px;/,
    )
    expect(styleSource).toMatch(
      /html:not\(\[data-reading-size="smaller"\]\):not\(\[data-reading-size="larger"\]\) \{\s*--kt-reading-root-size: 16px;/,
    )
  })
})
