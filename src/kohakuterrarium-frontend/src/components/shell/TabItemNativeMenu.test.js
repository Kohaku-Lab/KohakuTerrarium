import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import TabItem from "@/components/shell/TabItem.vue"
import { tabMenuItems } from "@/components/shell/tabMenu"
import { actions, clearActions, registerAction } from "@/ext/actions"
import { useTabsStore } from "@/stores/tabs"
import { CHOICE_EVENT, nativeMenuSupported, showNativeMenu } from "@/utils/nativeMenu"

const t = (key) => key

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  clearActions()
})

afterEach(() => {
  delete window.__TAURI_INTERNALS__
  clearActions()
})

const choose = (id) => window.dispatchEvent(new CustomEvent(CHOICE_EVENT, { detail: { id } }))

describe("tabMenuItems", () => {
  it("enables only the closing commands that have something to close", () => {
    const rows = (index, total, id = "t") =>
      Object.fromEntries(
        tabMenuItems({ tab: { id }, index, total, canPopOut: false, t })
          .filter((row) => row.id)
          .map((row) => [row.id, row.enabled !== false]),
      )
    expect(rows(0, 1)).toMatchObject({
      "close-left": false,
      "close-right": false,
      "close-others": false,
      close: true,
    })
    expect(rows(1, 3)).toMatchObject({ "close-left": true, "close-right": true })
    expect(rows(2, 3)["close-right"]).toBe(false)
    expect(rows(0, 2, "dashboard").close).toBe(false)
  })

  it("offers move-to-window only when asked and lists extension actions last", () => {
    const base = { tab: { id: "t", kind: "attach" }, index: 0, total: 1, t }
    expect(tabMenuItems({ ...base, canPopOut: false }).some((r) => r.id === "move")).toBe(false)
    expect(tabMenuItems({ ...base, canPopOut: true }).some((r) => r.id === "move")).toBe(true)

    registerAction({
      key: "pkg/act",
      pkg: "pkg",
      id: "act",
      label: "Pin",
      options: { targets: ["tab"] },
    })
    expect(actions.size).toBe(1)
    const rows = tabMenuItems({ ...base, canPopOut: false })
    expect(rows.at(-1)).toEqual({ id: "action:pkg/act", label: "Pin" })
    expect(rows.at(-2)).toEqual({ separator: true })
  })
})

describe("showNativeMenu", () => {
  it("is unavailable in a browser and runs the handler only for a chosen row", async () => {
    expect(nativeMenuSupported()).toBe(false)
    const picked = vi.fn()
    expect(await showNativeMenu([{ id: "a", label: "A" }], picked)).toBe(false)
    choose("a")
    expect(picked).not.toHaveBeenCalled()

    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    expect(nativeMenuSupported()).toBe(true)
    expect(await showNativeMenu([{ id: "a", label: "A" }], picked)).toBe(true)
    expect(invoke).toHaveBeenCalledWith("show_context_menu", {
      args: [{ items: [{ id: "a", label: "A" }] }],
    })
    choose("a")
    choose("a")
    expect(picked).toHaveBeenCalledTimes(1)
    expect(picked).toHaveBeenCalledWith("a")
  })

  it("lets a newer menu replace an older one that was dismissed", async () => {
    window.__TAURI_INTERNALS__ = { invoke: vi.fn(async () => true) }
    const first = vi.fn()
    const second = vi.fn()
    await showNativeMenu([{ id: "a", label: "A" }], first)
    await showNativeMenu([{ id: "b", label: "B" }], second)
    choose("b")
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledWith("b")
  })
})

describe("TabItem in the Tauri shell", () => {
  function mountTab(tab) {
    return mount(TabItem, {
      props: { tab },
      global: { stubs: { TabContextMenu: { template: '<div data-test="web-menu" />' } } },
    })
  }

  it("shows the system menu and runs the chosen command", async () => {
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    const tabs = useTabsStore()
    tabs.openTab({ kind: "settings", id: "settings" })
    tabs.openTab({ kind: "catalog", id: "catalog" })
    const wrapper = mountTab({ kind: "catalog", id: "catalog" })

    await wrapper.get(".group").trigger("contextmenu")
    await flushPromises()
    expect(wrapper.html()).not.toContain("web-menu")
    const [, payload] = invoke.mock.calls.find(([name]) => name === "show_context_menu")
    expect(payload.args[0].items.map((row) => row.id).filter(Boolean)).toContain("close-others")

    choose("close-others")
    expect(tabs.byId.settings).toBeUndefined()
    expect(tabs.byId.catalog).toBeTruthy()
  })

  it("keeps the in-page menu in a browser and when the shell shows nothing", async () => {
    const browser = mountTab({ kind: "catalog", id: "catalog" })
    await browser.get(".group").trigger("contextmenu")
    await flushPromises()
    expect(browser.html()).toContain("web-menu")

    window.__TAURI_INTERNALS__ = { invoke: vi.fn(async () => false) }
    const refused = mountTab({ kind: "catalog", id: "catalog" })
    await refused.get(".group").trigger("contextmenu")
    await flushPromises()
    expect(refused.html()).toContain("web-menu")
  })
})
