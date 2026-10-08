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
import { useTabsStore } from "@/stores/tabs"

beforeEach(() => {
  setActivePinia(createPinia())
  workspace.isOpen = false
  workspace.root = ""
})

describe("RailNav", () => {
  it("starts a session from one button and opens the lab, history, library and graph", async () => {
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
      ["graph", "graph"],
    ]) {
      await w.find(`[data-test="rail-nav-${id}"]`).trigger("click")
      expect(openTab).toHaveBeenLastCalledWith({ kind, id: kind })
    }
    expect(w.text()).not.toContain("shell.quick.stats")
    expect(w.text()).not.toContain("shell.quick.extensions")
  })
})

describe("RailAppSwitch", () => {
  it("marks Studio while a Studio tab is active and crosses between the two apps", async () => {
    const tabs = useTabsStore()
    const w = mount(RailAppSwitch)
    const studio = () => w.find('[data-test="rail-app-studio"]')
    const terrarium = () => w.find('[data-test="rail-app-terrarium"]')
    expect(terrarium().attributes("aria-checked")).toBe("true")
    await studio().trigger("click")
    expect(tabs.tabs.find((t) => t.id === tabs.activeId)?.kind).toBe("studio-editor")
    expect(tabs.activeId).toContain("home")
    expect(studio().attributes("aria-checked")).toBe("true")
    await terrarium().trigger("click")
    expect(tabs.activeId).toBe("dashboard")
    expect(terrarium().attributes("aria-checked")).toBe("true")
  })

  it("opens the open workspace instead of the picker", async () => {
    workspace.isOpen = true
    workspace.root = "/work/space"
    const tabs = useTabsStore()
    const openTab = vi.spyOn(tabs, "openTab")
    const w = mount(RailAppSwitch)
    await w.find('[data-test="rail-app-studio"]').trigger("click")
    expect(openTab).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "studio-editor",
        entityKind: "workspace",
        workspace: "/work/space",
      }),
    )
  })
})
