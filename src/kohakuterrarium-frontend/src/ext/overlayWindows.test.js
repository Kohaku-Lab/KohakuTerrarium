import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { nextTick, provide, defineComponent, h } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import OverlayLayer from "@/ext/OverlayLayer.vue"
import OverlayWindowApp from "@/ext/OverlayWindowApp.vue"
import { hideOverlay, isOverlayVisible, loadPlacements, setWindowAllowed } from "@/ext/overlays"
import {
  OVERLAY_PARAM,
  WINDOW_LABEL_PREFIX,
  overlayKeyFromSearch,
  startOverlayWindows,
  syncOverlayWindows,
  windowActive,
  windowLabel,
} from "@/ext/overlayWindows"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { useOverlay } from "@/ext/sdk"
import { OVERLAY_INJECT_KEY, OVERLAY_MODE_INJECT_KEY } from "@/ext/sdk/keys"
import { extState } from "@/ext/state"

const Pet = { template: '<div class="pet kt-overlay-hit">pet</div>' }
const KEY = "ext:pets/cat"

function declare(overlay = {}) {
  const rec = {
    package: "pets",
    enabled: true,
    status: "idle",
    contributes: [
      {
        kind: "overlay",
        id: "cat",
        label: "Cat",
        owner: "package:pets",
        overlay: { surface: "window", ...overlay },
      },
    ],
  }
  extState.packages = [rec]
  registerPackageContributions(rec)
  provideContributionComponent("pets", "overlay", "cat", Pet)
}

function tauri() {
  const invoke = vi.fn(async () => true)
  window.__TAURI_INTERNALS__ = { invoke }
  return invoke
}

describe("overlay window labels", () => {
  it("are readable, unique per key and accepted by the shell", () => {
    const label = windowLabel(KEY)
    expect(label.startsWith(WINDOW_LABEL_PREFIX)).toBe(true)
    expect(label).toMatch(/^overlay-[a-z0-9-]{1,48}$/)
    expect(label).toContain("ext-pets-cat")
    expect(windowLabel("ext:pets/Cat")).not.toBe(windowLabel(KEY))
    expect(windowLabel(KEY)).toBe(label)
    const long = windowLabel(`ext:${"x".repeat(200)}/y`)
    expect(long.length - WINDOW_LABEL_PREFIX.length).toBeLessThanOrEqual(48)
    expect(windowLabel("::")).toMatch(/^overlay-[a-z0-9]{8}$/)
  })

  it("reads the overlay key from the page address", () => {
    expect(overlayKeyFromSearch(`?${OVERLAY_PARAM}=ext%3Apets%2Fcat`)).toBe(KEY)
    expect(overlayKeyFromSearch("?kt_launch=x")).toBeNull()
    expect(overlayKeyFromSearch("")).toBeNull()
  })
})

describe("native overlay windows", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    localStorage.clear()
    loadPlacements("win-host")
  })

  afterEach(() => {
    delete window.__TAURI_INTERNALS__
  })

  it("stays in the app until the user allows a window and the shell can open one", async () => {
    declare()
    expect(windowActive(KEY)).toBe(false)

    const invoke = tauri()
    expect(windowActive(KEY)).toBe(false)
    setWindowAllowed(KEY, true)
    expect(windowActive(KEY)).toBe(true)

    delete window.__TAURI_INTERNALS__
    expect(windowActive(KEY)).toBe(false)
    expect(invoke).not.toHaveBeenCalled()

    const layer = mount(OverlayLayer, { global: { stubs: { ExtensionContribution: true } } })
    expect(layer.findAll('[data-test="overlay-frame"]')).toHaveLength(1)
    layer.unmount()
  })

  it("opens the window once, closes it when hidden, and leaves the in-app layer empty", async () => {
    declare({ width: 120, height: 90, anchor: "top-left", alwaysOnTop: false })
    const invoke = tauri()
    setWindowAllowed(KEY, true)

    syncOverlayWindows()
    syncOverlayWindows()
    const label = windowLabel(KEY)
    const opens = invoke.mock.calls.filter(([name]) => name === "open_overlay_window")
    expect(opens).toHaveLength(1)
    expect(opens[0][1].args[0]).toEqual({
      label,
      key: KEY,
      anchor: "top-left",
      width: 120,
      height: 90,
      alwaysOnTop: false,
    })

    const layer = mount(OverlayLayer, { global: { stubs: { ExtensionContribution: true } } })
    expect(layer.findAll('[data-test="overlay-frame"]')).toHaveLength(0)
    layer.unmount()

    hideOverlay(KEY)
    syncOverlayWindows()
    expect(invoke).toHaveBeenLastCalledWith("close_overlay_window", { args: [label] })
  })

  it("follows visibility and consent changes while running", async () => {
    declare()
    const invoke = tauri()
    startOverlayWindows()
    setWindowAllowed(KEY, true)
    await nextTick()
    expect(invoke.mock.calls.some(([name]) => name === "open_overlay_window")).toBe(true)
    setWindowAllowed(KEY, false)
    await nextTick()
    expect(invoke.mock.calls.some(([name]) => name === "close_overlay_window")).toBe(true)
  })
})

describe("OverlayWindowApp", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    localStorage.clear()
    loadPlacements("page-host")
    extState.loading = false
  })

  afterEach(() => {
    delete window.__TAURI_INTERNALS__
  })

  it("acknowledges itself, draws the overlay with chrome and hides through the shell", async () => {
    declare()
    const invoke = tauri()
    const page = mount(OverlayWindowApp, { props: { overlayKey: KEY } })
    await nextTick()
    expect(invoke).toHaveBeenCalledWith("overlay_ack", {})
    expect(page.find(".pet").exists()).toBe(true)

    await page.find('[data-test="overlay-window-bar"]').trigger("pointerdown")
    expect(invoke).toHaveBeenCalledWith("overlay_start_drag", {})

    await page.find('[data-test="overlay-window-close"]').trigger("click")
    expect(isOverlayVisible(KEY)).toBe(false)
    expect(invoke).toHaveBeenCalledWith("close_overlay_window", { args: [windowLabel(KEY)] })
    page.unmount()
  })

  it("draws no chrome for a click-through overlay and reports its hit regions", async () => {
    vi.useFakeTimers()
    try {
      declare({ clickThrough: true })
      const invoke = tauri()
      const page = mount(OverlayWindowApp, { props: { overlayKey: KEY }, attachTo: document.body })
      expect(page.find('[data-test="overlay-window-bar"]').exists()).toBe(false)
      vi.advanceTimersByTime(250)
      const call = invoke.mock.calls.find(([name]) => name === "overlay_set_hit_regions")
      expect(call[1].args[0]).toHaveLength(1)
      const before = invoke.mock.calls.length
      vi.advanceTimersByTime(1000)
      expect(invoke.mock.calls.length).toBe(before)
      page.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it("says so when the overlay is not provided by any extension", () => {
    tauri()
    const page = mount(OverlayWindowApp, { props: { overlayKey: "ext:none/x" } })
    expect(page.find('[data-test="overlay-window-missing"]').text()).toContain("not available")
    page.unmount()
  })

  it("gives the SDK a window-mode useOverlay", async () => {
    const invoke = tauri()
    let control
    const Probe = defineComponent({
      setup() {
        control = useOverlay()
        return () => h("div")
      },
    })
    const Host = defineComponent({
      setup() {
        provide(OVERLAY_INJECT_KEY, KEY)
        provide(OVERLAY_MODE_INJECT_KEY, "window")
        return () => h(Probe)
      },
    })
    mount(Host)
    expect(control.visible.value).toBe(true)
    await control.move(10, 20)
    expect(invoke).toHaveBeenLastCalledWith("overlay_set_bounds", {
      args: [10, 20, window.innerWidth, window.innerHeight],
    })
    await control.resize(300, 200)
    expect(invoke).toHaveBeenLastCalledWith("overlay_set_bounds", {
      args: [window.screenX, window.screenY, 300, 200],
    })
    let prevented = false
    await control.startDrag({ preventDefault: () => (prevented = true) })
    expect(prevented).toBe(true)
    expect(invoke).toHaveBeenLastCalledWith("overlay_start_drag", {})
  })
})
