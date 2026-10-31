import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"
import { nextTick } from "vue"

import StatusItems from "@/ext/StatusItems.vue"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import {
  DEFAULT_STATUS_WIDTH,
  statusItemWidth,
  statusItems,
  statusItemsFor,
} from "@/ext/statusItems"
import { extState } from "@/ext/state"

const C = { template: "<span>x</span>" }

function declare(name, contributes) {
  const rec = { package: name, enabled: true, status: "idle", contributes }
  extState.packages = [rec]
  registerPackageContributions(rec)
  return rec
}

describe("status items", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
  })

  it("registers declared items by side and order and clears them with the rest", () => {
    declare("alpha", [
      { kind: "status-item", id: "b", label: "B", order: 20, status: { side: "left" } },
      { kind: "status-item", id: "a", label: "A", order: 10, status: { side: "left" } },
      { kind: "status-item", id: "r", label: "R", status: { side: "right", maxWidth: 60 } },
      { kind: "tab", id: "t", label: "T", owner: "package:alpha" },
    ])
    expect(statusItemsFor("left").map((i) => i.id)).toEqual(["a", "b"])
    expect(statusItemsFor("right").map((i) => i.id)).toEqual(["r"])
    expect(statusItemWidth(statusItems.get("ext:alpha/r"))).toBe(60)
    expect(statusItemWidth(statusItems.get("ext:alpha/a"))).toBe(DEFAULT_STATUS_WIDTH)

    unregisterAllContributions()
    expect(statusItems.size).toBe(0)
  })

  it("registers an undeclared item from code with its placement options", () => {
    extState.packages = [{ package: "beta", enabled: true, status: "idle", contributes: [] }]
    provideContributionComponent("beta", "status-item", "git", C, {
      side: "left",
      maxWidth: 90,
    })
    const item = statusItems.get("ext:beta/git")
    expect(item.options).toEqual({ side: "left", maxWidth: 90 })
    expect(statusItemsFor("left")).toHaveLength(1)
  })

  it("refuses a runtime owner for a status item", () => {
    extState.packages = [{ package: "beta", enabled: true, status: "idle", contributes: [] }]
    expect(() =>
      provideContributionComponent("beta", "status-item", "x", C, { owner: "plugin:p" }),
    ).toThrow(/undeclared status-item/)
  })

  it("draws only the items of its own side, each capped in width", async () => {
    declare("alpha", [
      { kind: "status-item", id: "l", label: "L", status: { side: "left", maxWidth: 70 } },
      { kind: "status-item", id: "r", label: "R", status: { side: "right" } },
    ])
    const left = mount(StatusItems, { props: { side: "left" } })
    await nextTick()
    expect(left.find('[data-test="status-item-l"]').exists()).toBe(true)
    expect(left.find('[data-test="status-item-r"]').exists()).toBe(false)
    expect(left.find('[data-test="status-item-l"]').attributes("style")).toContain(
      "max-width: 70px",
    )
    const empty = mount(StatusItems, { props: { side: "right" } })
    expect(empty.find('[data-test="status-items"]').exists()).toBe(true)
    unregisterAllContributions()
    await nextTick()
    expect(left.find('[data-test="status-items"]').exists()).toBe(false)
  })
})
