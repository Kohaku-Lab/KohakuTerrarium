import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, nextTick } from "vue"

import { useDesktopTrayItems } from "@/composables/useDesktopTrayItems"
import { dispatchToChannels, notificationChannels } from "@/ext/notificationChannels"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { extState, runtime } from "@/ext/state"
import { TRAY_ITEM_EVENT, trayItemList, trayItems } from "@/ext/trayItems"

const bridge = vi.hoisted(() => ({ setTrayItems: vi.fn(async () => true) }))
vi.mock("@/utils/hostBridge", async (importOriginal) => ({
  ...(await importOriginal()),
  setTrayItems: bridge.setTrayItems,
}))

function declare(contributes) {
  const rec = { package: "alpha", enabled: true, status: "idle", contributes }
  extState.packages = [rec]
  registerPackageContributions(rec)
}

const item = (id, label, extra = {}) => ({
  kind: "tray-item",
  id,
  label,
  owner: "package:alpha",
  ...extra,
})

describe("tray items", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    bridge.setTrayItems.mockClear()
    runtime.ensureLoaded = vi.fn(async () => {})
  })

  it("lists declared items in order for the shell and clears them with the rest", () => {
    declare([item("b", "Second", { order: 2 }), item("a", "First", { order: 1 })])
    expect(trayItemList()).toEqual([
      { id: "ext:alpha/a", label: "First" },
      { id: "ext:alpha/b", label: "Second" },
    ])
    unregisterAllContributions()
    expect(trayItems.size).toBe(0)
  })

  it("pushes the list to the shell when it changes and runs the chosen item", async () => {
    const Host = defineComponent({
      setup() {
        useDesktopTrayItems()
        return () => h("div")
      },
    })
    const wrapper = mount(Host)
    await nextTick()
    expect(bridge.setTrayItems).not.toHaveBeenCalled()

    const handler = vi.fn()
    declare([item("pause", "Pause bot")])
    provideContributionComponent("alpha", "tray-item", "pause", handler)
    await nextTick()
    expect(bridge.setTrayItems).toHaveBeenLastCalledWith([
      { id: "ext:alpha/pause", label: "Pause bot" },
    ])

    window.dispatchEvent(new CustomEvent(TRAY_ITEM_EVENT, { detail: { id: "ext:alpha/pause" } }))
    await vi.waitFor(() => expect(handler).toHaveBeenCalledTimes(1))
    window.dispatchEvent(new CustomEvent(TRAY_ITEM_EVENT, { detail: { id: "ext:alpha/unknown" } }))
    await nextTick()
    expect(handler).toHaveBeenCalledTimes(1)

    unregisterAllContributions()
    await nextTick()
    expect(bridge.setTrayItems).toHaveBeenLastCalledWith([])
    wrapper.unmount()
  })
})

describe("notification channels", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    runtime.ensureLoaded = vi.fn(async () => {})
  })

  const channel = (id) => ({ kind: "notification-channel", id, label: id, owner: "package:alpha" })

  it("hands every channel a frozen copy of the event and isolates failures", async () => {
    declare([channel("ok"), channel("broken"), channel("silent")])
    const received = []
    provideContributionComponent("alpha", "notification-channel", "ok", (payload) => {
      received.push(payload)
    })
    provideContributionComponent("alpha", "notification-channel", "broken", () => {
      throw new Error("boom")
    })
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})

    const payload = { kind: "waiting-input", title: "T", body: "B", target: "root" }
    await dispatchToChannels(payload)
    await dispatchToChannels(payload)

    expect(received).toHaveLength(2)
    expect(received[0]).toEqual(payload)
    expect(Object.isFrozen(received[0])).toBe(true)
    expect(received[0]).not.toBe(payload)
    const messages = warn.mock.calls.map((call) => call[0])
    expect(messages.filter((m) => m.includes("broken: boom"))).toHaveLength(1)
    expect(messages.filter((m) => m.includes("silent: no handler"))).toHaveLength(1)
    warn.mockRestore()
  })

  it("requires a handler function and clears with the other contributions", () => {
    declare([channel("x")])
    expect(() => provideContributionComponent("alpha", "notification-channel", "x", {})).toThrow(
      /needs a handler function/,
    )
    expect(notificationChannels.size).toBe(1)
    unregisterAllContributions()
    expect(notificationChannels.size).toBe(0)
  })
})
