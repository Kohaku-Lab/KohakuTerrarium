import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { activeLayoutScope, activeLayoutStore } from "./useActiveLayout.js"
import { useLayoutStore } from "@/stores/layout"
import { useTabsStore } from "@/stores/tabs"

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("useActiveLayout — resolves the visible attach tab's layout store", () => {
  it("falls back to the default scope when no attach tab is active", () => {
    expect(activeLayoutScope()).toBeNull()
    expect(activeLayoutStore()).toBe(useLayoutStore(null))
  })

  it("returns the active attach tab's own scoped store", () => {
    const tabs = useTabsStore()
    tabs.byId["attach:g1"] = { kind: "attach", id: "attach:g1", target: "g1" }
    tabs.activeId = "attach:g1"
    expect(activeLayoutScope()).toBe("g1")
    const scoped = activeLayoutStore()
    expect(scoped).toBe(useLayoutStore("g1"))
    expect(scoped).not.toBe(useLayoutStore(null))
  })

  it("ignores non-attach active tabs", () => {
    const tabs = useTabsStore()
    tabs.byId["dashboard"] = { kind: "dashboard", id: "dashboard" }
    tabs.activeId = "dashboard"
    expect(activeLayoutScope()).toBeNull()
  })
})
