import { parse } from "@vue/compiler-sfc"
import { baseParse } from "@vue/compiler-dom"
import { shallowMount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import RailPane from "./RailPane.vue"
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
      expect.arrayContaining(["RailNav", "RailGroupAttached"]),
    )
    expect(descendants(viewport).map((node) => node.tag)).not.toContain("RailGroupPinned")
    expect(descendants(viewport).map((node) => node.tag)).not.toContain("RailAppSwitch")
    expect(descendants(viewport).map((node) => node.tag)).not.toContain("HostStatusChip")
    expect(descendants(viewport).map((node) => node.tag)).not.toContain("BrandMark")
  })

  it("keeps the brand and footer from shrinking when the navigation overflows", () => {
    const nav = descendants(root).find(
      (node) => node.tag === "nav" && descendants(node).some((child) => child.tag === "RailNav"),
    )
    const fixedRows = elements(nav).filter(
      (node) =>
        descendants(node).some((child) =>
          ["BrandMark", "HostStatusChip", "button"].includes(child.tag),
        ) || node.tag === "RailAppSwitch",
    )
    for (const row of fixedRows.filter((node) => !classes(node).includes("overflow-y-auto"))) {
      expect(classes(row)).toContain("shrink-0")
    }
  })
})

describe("RailPane collapse", () => {
  const navs = descendants(root).filter((node) => node.tag === "nav")
  const directive = (node, name) => node.props.find((p) => p.type === 7 && p.name === name)

  it("renders the icon strip when collapsed and the full rail otherwise", () => {
    expect(navs).toHaveLength(2)
    expect(directive(navs[0], "if")?.exp.content).toBe("isCollapsed")
    expect(directive(navs[1], "else")).toBeTruthy()
    expect(descendants(navs[0]).map((n) => n.tag)).not.toContain("RailGroupAttached")
  })

  it("offers collapse in the full rail and expand in the strip, and hides the resize handle when collapsed", () => {
    const testIds = (node) =>
      descendants(node).map((n) => n.props.find((p) => p.name === "data-test")?.value?.content)
    expect(testIds(navs[1])).toContain("rail-collapse")
    expect(testIds(navs[0])).toContain("rail-expand")
    const handle = elements(elements(root)[0]).find((n) => classes(n).includes("cursor-col-resize"))
    expect(directive(handle, "if")?.exp.content).toBe("collapsible && !isCollapsed")
  })
})

describe("RailPane in the phone drawer", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.setItem("kt.rail.collapsed", "1")
  })
  afterEach(() => localStorage.removeItem("kt.rail.collapsed"))

  const render = (props) =>
    shallowMount(RailPane, { props, global: { stubs: { BrandMark: true } } })

  it("collapses to the icon strip in the shell when the pref says so", () => {
    const wrapper = render({})
    expect(wrapper.find("[data-test='rail-collapsed']").exists()).toBe(true)
  })

  it("ignores the collapsed pref, hides collapse and resize, and fills the drawer", () => {
    const wrapper = render({ collapsible: false })
    expect(wrapper.find("[data-test='rail-collapsed']").exists()).toBe(false)
    expect(wrapper.find("[data-test='rail-collapse']").exists()).toBe(false)
    expect(wrapper.find(".cursor-col-resize").exists()).toBe(false)
    expect(wrapper.classes()).toContain("w-full")
    expect(wrapper.attributes("style") || "").not.toContain("width")
  })
})
