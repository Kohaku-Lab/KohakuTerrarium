import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

import { dockPanelComponent, dockPanelList, dockPanelTitle, isDockPanel } from "@/ext/dockPanels"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { extState } from "@/ext/state"
import { useLayoutStore } from "@/stores/layout"

const C = { template: "<div />" }

function declare(contributes) {
  const rec = { package: "alpha", enabled: true, status: "idle", contributes }
  extState.packages = [rec]
  registerPackageContributions(rec)
}

describe("dock panels", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
  })

  it("docks only the panels that ask for it, in order, and keeps them in the layout store", () => {
    declare([
      { kind: "panel", id: "b", label: "B", owner: "plugin:p", order: 2, dock: true },
      { kind: "panel", id: "a", label: "A", owner: "plugin:p", order: 1, dock: true, icon: "i-a" },
      { kind: "panel", id: "side", label: "Side", owner: "plugin:p" },
    ])
    expect(dockPanelList().map((p) => p.label)).toEqual(["A", "B"])
    expect(dockPanelList()[0].icon).toBe("i-a")
    expect(isDockPanel("ext:alpha/side")).toBe(false)
    expect(useLayoutStore().getPanel("ext:alpha/side").label).toBe("Side")
    expect(useLayoutStore().getPanel("ext:alpha/a").label).toBe("A")

    unregisterAllContributions()
    expect(dockPanelList()).toEqual([])
  })

  it("resolves title and component for a docked id and falls back for any other", () => {
    declare([{ kind: "panel", id: "a", label: "Board", owner: "plugin:p", dock: true }])
    expect(dockPanelTitle("ext:alpha/a", () => "fallback")).toBe("Board")
    expect(dockPanelTitle("usage", () => "Usage")).toBe("Usage")
    expect(dockPanelComponent("ext:alpha/a")).toBeTruthy()
    expect(dockPanelComponent("usage")).toBeNull()
  })

  it("docks an undeclared panel registered from code with dock: true", () => {
    extState.packages = [{ package: "alpha", enabled: true, status: "idle", contributes: [] }]
    provideContributionComponent("alpha", "panel", "p", C, { owner: "plugin:q", dock: true })
    expect(isDockPanel("ext:alpha/p")).toBe(true)
  })
})
