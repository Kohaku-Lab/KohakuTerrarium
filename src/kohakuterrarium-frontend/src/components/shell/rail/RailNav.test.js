import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

const workspace = vi.hoisted(() => ({ isOpen: false, root: "" }))
vi.mock("@/stores/studio/workspace", () => ({ useStudioWorkspaceStore: () => workspace }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (k) => k }) }))
vi.mock("@/components/shell/newSession/NewSessionDialog.vue", () => ({
  default: {
    name: "NewSessionDialog",
    emits: ["close"],
    template: "<div data-test='new-dialog' @click=\"$emit('close')\" />",
  },
}))

import RailAppSwitch from "./RailAppSwitch.vue"
import RailNav from "./RailNav.vue"
import { _resetAppModeForTests, setAppMode, useAppMode } from "./useAppMode"
import { useTabsStore } from "@/stores/tabs"

beforeEach(() => {
  setActivePinia(createPinia())
  workspace.isOpen = false
  workspace.root = ""
  localStorage.removeItem("kt.appMode")
  _resetAppModeForTests()
})

describe("RailNav", () => {
  it("starts a session from one button and opens the lab, history and library", async () => {
    const tabs = useTabsStore()
    const openTab = vi.spyOn(tabs, "openTab")
    const w = mount(RailNav)
    expect(w.find('[data-test="new-dialog"]').exists()).toBe(false)
    await w.find('[data-test="rail-new-session"]').trigger("click")
    expect(w.find('[data-test="new-dialog"]').exists()).toBe(true)
    await w.find('[data-test="new-dialog"]').trigger("click")
    expect(w.find('[data-test="new-dialog"]').exists()).toBe(false)
    for (const [id, kind] of [
      ["lab", "dashboard"],
      ["history", "saved-sessions"],
      ["library", "catalog"],
    ]) {
      await w.find(`[data-test="rail-nav-${id}"]`).trigger("click")
      expect(openTab).toHaveBeenLastCalledWith({ kind, id: kind })
    }
    expect(w.find('[data-test="rail-nav-graph"]').exists()).toBe(false)
    expect(w.text()).not.toContain("shell.quick.stats")
    expect(w.text()).not.toContain("shell.quick.extensions")
  })
})

describe("RailAppSwitch", () => {
  it("flips the home tab between the lab and Studio, brings it forward, and remembers the mode", async () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "catalog", id: "catalog" })
    const w = mount(RailAppSwitch)
    const studio = () => w.find('[data-test="rail-app-studio"]')
    const terrarium = () => w.find('[data-test="rail-app-terrarium"]')
    expect(terrarium().attributes("aria-checked")).toBe("true")
    await studio().trigger("click")
    expect(tabs.activeId).toBe("dashboard")
    expect(tabs.tabs.some((t) => t.kind === "studio-editor")).toBe(false)
    expect(useAppMode().appMode.value).toBe("studio")
    expect(localStorage.getItem("kt.appMode")).toBe("studio")
    expect(studio().attributes("aria-checked")).toBe("true")
    tabs.openTab({ kind: "catalog", id: "catalog" })
    await terrarium().trigger("click")
    expect(tabs.activeId).toBe("dashboard")
    expect(useAppMode().appMode.value).toBe("terrarium")
  })

  it("the sidebar's Lab entry returns to Terrarium mode", async () => {
    setAppMode("studio")
    const w = mount(RailNav)
    expect(w.find('[data-test="rail-nav-lab"]').classes()).not.toContain("font-medium")
    await w.find('[data-test="rail-nav-lab"]').trigger("click")
    expect(useAppMode().appMode.value).toBe("terrarium")
    expect(w.find('[data-test="rail-nav-lab"]').classes()).toContain("font-medium")
  })
})
