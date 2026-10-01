import { parse } from "@vue/compiler-sfc"
import { baseParse } from "@vue/compiler-dom"
import { describe, expect, it } from "vitest"
import source from "./RailPane.vue?raw"

const root = baseParse(parse(source).descriptor.template.content)

function elements(node) {
  return (node.children || []).filter((child) => child.type === 1)
}

function classes(node) {
  return (node.props.find((prop) => prop.name === "class")?.value?.content || "").split(/\s+/)
}

function descendants(node) {
  return elements(node).flatMap((child) => [child, ...descendants(child)])
}

describe("RailPane scroll layout", () => {
  it("contains every navigation group in one bounded scroll viewport", () => {
    const viewports = descendants(root).filter((node) => classes(node).includes("overflow-y-auto"))
    expect(viewports).toHaveLength(1)
    const viewport = viewports[0]
    expect(classes(viewport)).toEqual(expect.arrayContaining(["flex-1", "min-h-0"]))
    expect(descendants(viewport).map((node) => node.tag)).toEqual(
      expect.arrayContaining([
        "RailGroupTop",
        "RailGroupAttached",
        "RailGroupQuick",
        "RailGroupPinned",
      ]),
    )
    expect(descendants(viewport).map((node) => node.tag)).not.toContain("HostStatusChip")
    expect(descendants(viewport).map((node) => node.tag)).not.toContain("BrandMark")
  })

  it("keeps the brand and footer from shrinking when the navigation overflows", () => {
    const nav = descendants(root).find((node) => node.tag === "nav")
    const fixedRows = elements(nav).filter((node) =>
      descendants(node).some((child) =>
        ["BrandMark", "HostStatusChip", "button"].includes(child.tag),
      ),
    )
    for (const row of fixedRows.filter((node) => !classes(node).includes("overflow-y-auto"))) {
      expect(classes(row)).toContain("shrink-0")
    }
  })
})
