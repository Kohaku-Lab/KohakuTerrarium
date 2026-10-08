import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { inject } from "vue"

const workspace = vi.hoisted(() => ({ isOpen: false, root: "" }))
vi.mock("@/stores/studio/workspace", () => ({ useStudioWorkspaceStore: () => workspace }))
vi.mock("@/components/lab/LabPage.vue", () => ({
  default: { name: "LabPage", template: "<div data-test='lab-page' />" },
}))
vi.mock("@/components/studio/pages/StudioHomePage.vue", () => ({
  default: {
    name: "StudioHomePage",
    setup() {
      return { nav: inject("kt.studioNav", null) }
    },
    template: "<button data-test='studio-home' @click=\"nav.openWorkspace('/picked')\" />",
  },
}))
vi.mock("@/components/studio/pages/StudioWorkspacePage.vue", () => ({
  default: {
    name: "StudioWorkspacePage",
    props: ["workspacePathProp"],
    setup() {
      return { nav: inject("kt.studioNav", null) }
    },
    template:
      "<button data-test='studio-workspace' :data-root='workspacePathProp' @click='nav.openHome()' />",
  },
}))
vi.mock("@/components/studio/pages/StudioCreaturePage.vue", () => ({
  default: { template: "<div />" },
}))
vi.mock("@/components/studio/pages/StudioModulePage.vue", () => ({
  default: { template: "<div />" },
}))
vi.mock("@/composables/useStudioNav", () => ({ STUDIO_NAV_INJECT_KEY: "kt.studioNav" }))

import HomeTab from "./HomeTab.vue"
import { _resetAppModeForTests, setAppMode } from "@/components/shell/rail/useAppMode"
import { useTabsStore } from "@/stores/tabs"

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.removeItem("kt.appMode")
  _resetAppModeForTests()
  workspace.isOpen = false
  workspace.root = ""
})

describe("HomeTab", () => {
  it("is the lab in Terrarium mode and Studio in Studio mode", async () => {
    const w = mount(HomeTab, { props: { tab: { id: "dashboard" } } })
    expect(w.find("[data-test='lab-page']").exists()).toBe(true)
    setAppMode("studio")
    await w.vm.$nextTick()
    expect(w.find("[data-test='lab-page']").exists()).toBe(false)
    expect(w.find("[data-test='studio-home']").exists()).toBe(true)
  })

  it("opens Studio on the open workspace, and swaps picker and workspace in place without tabs", async () => {
    setAppMode("studio")
    workspace.isOpen = true
    workspace.root = "/open"
    const tabs = useTabsStore()
    const openTab = vi.spyOn(tabs, "openTab")
    const w = mount(HomeTab, { props: { tab: { id: "dashboard" } } })
    expect(w.find("[data-test='studio-workspace']").attributes("data-root")).toBe("/open")
    await w.find("[data-test='studio-workspace']").trigger("click")
    expect(w.find("[data-test='studio-home']").exists()).toBe(true)
    await w.find("[data-test='studio-home']").trigger("click")
    expect(w.find("[data-test='studio-workspace']").attributes("data-root")).toBe("/picked")
    expect(openTab).not.toHaveBeenCalled()
  })
})
