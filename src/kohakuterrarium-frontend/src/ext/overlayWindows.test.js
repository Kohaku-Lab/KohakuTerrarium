import { parse } from "@vue/compiler-sfc"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { nextTick, provide, defineComponent, h } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import OverlayLayer from "@/ext/OverlayLayer.vue"
import { desktopContextConnected } from "@/ext/desktopContext"
import OverlayWindowApp from "@/ext/OverlayWindowApp.vue"
import overlayWindowSource from "@/ext/OverlayWindowApp.vue?raw"
import { desktopCapabilities } from "@/utils/hostBridge"
import { toggleHud } from "@/ext/hud"
import {
  hideOverlay,
  isOverlayVisible,
  loadPlacements,
  setWindowAllowed,
  resetOverlay,
  showOverlay,
  windowPreference,
} from "@/ext/overlays"
import {
  OVERLAY_PARAM,
  WINDOW_LABEL_PREFIX,
  overlayKeyFromSearch,
  startOverlayWindows,
  syncOverlayWindows,
  windowActive,
  windowLabel,
  refreshOverlayCapabilities,
  receiveOverlayReady,
  resetNativeOverlay,
  retryOverlayWindow,
  windowStates,
  windowFailure,
} from "@/ext/overlayWindows"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { useOverlay, useExtension } from "@/ext/sdk"
import { OVERLAY_INJECT_KEY, OVERLAY_MODE_INJECT_KEY } from "@/ext/sdk/keys"
import { extState, runtime } from "@/ext/state"

const providedLoader = async () => {}
const Pet = { template: '<div class="pet kt-overlay-hit">pet</div>' }
const KEY = "ext:pets/cat"

function declare(overlay = {}) {
  const rec = {
    package: "pets",
    enabled: true,
    status: "loaded",
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
  runtime.ensureLoaded = providedLoader
  registerPackageContributions(rec)
  provideContributionComponent("pets", "overlay", "cat", Pet)
}

function viewportContent(key) {
  let surface
  const Content = defineComponent({
    setup() {
      surface = useOverlay()
      return () => null
    },
  })
  const wrapper = mount(
    defineComponent({
      setup() {
        provide(OVERLAY_INJECT_KEY, key)
        return () => h(Content)
      },
    }),
  )
  return { surface, dispose: () => wrapper.unmount() }
}

function tauri() {
  const invoke = vi.fn(async (name) =>
    name === "get_desktop_capabilities"
      ? {
          surface: "desktop",
          protocol: 1,
          overlayWindows: true,
          overlayPointer: true,
          overlayPresentation: 1,
          overlayResize: true,
          overlayPlacementNative: true,
        }
      : true,
  )
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
    await desktopCapabilities()
    expect(windowActive(KEY)).toBe(false)
    setWindowAllowed(KEY, true)
    expect(windowActive(KEY)).toBe(true)

    delete window.__TAURI_INTERNALS__
    expect(windowActive(KEY)).toBe(false)
    expect(invoke.mock.calls.every(([name]) => name === "get_desktop_capabilities")).toBe(true)

    const layer = mount(OverlayLayer, { global: { stubs: { ExtensionContribution: true } } })
    expect(layer.findAll('[data-test="overlay-frame"]')).toHaveLength(1)
    layer.unmount()
  })

  it("keeps a package controller mounted when its renderer is native or hidden", async () => {
    declare()
    extState.packages[0].handle = { package: "pets" }
    const Controller = defineComponent({
      setup() {
        const kt = useExtension()
        return () => h("span", { "data-controller": "" }, kt.package)
      },
    })
    provideContributionComponent("pets", "overlay", "cat", Pet, { controller: Controller })
    tauri()
    await desktopCapabilities()
    setWindowAllowed(KEY, true)
    const layer = mount(OverlayLayer)
    try {
      expect(layer.find("[data-controller]").text()).toBe("pets")
      expect(layer.findAll('[data-test="overlay-frame"]')).toHaveLength(0)
      hideOverlay(KEY)
      await nextTick()
      expect(layer.find("[data-controller]").exists()).toBe(true)
    } finally {
      layer.unmount()
    }
  })

  it("loads a visible native overlay's package in the main window for its controller", async () => {
    declare()
    tauri()
    await desktopCapabilities()
    setWindowAllowed(KEY, true)
    const original = runtime.ensureLoaded
    runtime.ensureLoaded = vi.fn(async () => {})
    const layer = mount(OverlayLayer)
    try {
      await nextTick()
      expect(runtime.ensureLoaded).toHaveBeenCalledWith("pets")
    } finally {
      layer.unmount()
      runtime.ensureLoaded = original
    }
  })

  it("requires consent and renderer readiness for an unreviewed content-sized overlay", async () => {
    const invoke = tauri()
    declare({ windowReady: true, contentPlacement: true })
    const viewport = viewportContent(KEY)
    await refreshOverlayCapabilities()
    viewport.surface.ready()
    expect(invoke.mock.calls.some(([name]) => name === "open_overlay_window")).toBe(false)
    setWindowAllowed(KEY, true)
    syncOverlayWindows()
    await flushPromises()
    const request = invoke.mock.calls.find(([name]) => name === "open_overlay_window")[1].args[0]
    expect(request).toMatchObject({ contentPlacement: true, hostKey: extState.hostKey })
    expect(request.generation).toBeTruthy()
    expect(windowActive(KEY)).toBe(false)
    await receiveOverlayReady({ detail: { key: KEY, generation: "stale" } })
    expect(windowActive(KEY)).toBe(false)
    await receiveOverlayReady({ detail: { key: KEY, generation: request.generation } })
    expect(windowActive(KEY)).toBe(true)
    viewport.dispose()
    hideOverlay(KEY)
    syncOverlayWindows()
    await flushPromises()
  })

  it("opens the window once, closes it when hidden, and leaves the in-app layer empty", async () => {
    declare({ width: 120, height: 90, anchor: "top-left", alwaysOnTop: false })
    const invoke = tauri()
    await desktopCapabilities()
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
    await flushPromises()
    expect(invoke).toHaveBeenLastCalledWith("close_overlay_window", { args: [label] })
    invoke.mockClear()
    expect(resetNativeOverlay(KEY)).toBe(true)
    expect(invoke).not.toHaveBeenCalled()
    showOverlay(KEY)
    syncOverlayWindows()
    await flushPromises()
    expect(invoke.mock.calls.map(([name]) => name)).toEqual([
      "open_overlay_window",
      "reset_overlay_window",
    ])
    hideOverlay(KEY)
    syncOverlayWindows()
    await flushPromises()
  })

  it("opens the built-in HUD in a window without asking, and lists it in the app when there is no shell", async () => {
    expect(windowActive("hud")).toBe(false)

    const invoke = tauri()
    await desktopCapabilities()
    expect(windowActive("hud")).toBe(true)
    expect(isOverlayVisible("hud")).toBe(false)

    toggleHud()
    syncOverlayWindows()
    const opens = invoke.mock.calls.filter(([name]) => name === "open_overlay_window")
    expect(opens).toHaveLength(1)
    expect(opens[0][1].args[0]).toMatchObject({
      key: "hud",
      anchor: "top-right",
      alwaysOnTop: true,
    })

    toggleHud()
    syncOverlayWindows()
    await flushPromises()
    expect(invoke).toHaveBeenLastCalledWith("close_overlay_window", { args: [windowLabel("hud")] })
  })

  it("defaults only the host-verified pet to desktop and waits for matching readiness", async () => {
    const key = "ext:virtual-pet/pet"
    const record = {
      package: "virtual-pet",
      desktop_pet: true,
      contributes: [
        {
          kind: "overlay",
          id: "pet",
          owner: "package:virtual-pet",
          overlay: { surface: "window" },
        },
      ],
    }
    const invoke = tauri()
    invoke.mockImplementation(async (name) =>
      name === "get_desktop_capabilities"
        ? {
            surface: "desktop",
            protocol: 1,
            overlayWindows: true,
            overlayPointer: true,
            overlayPresentation: 1,
          }
        : true,
    )
    extState.hostKey = "realm-a"
    registerPackageContributions({ ...record, desktop_pet: false })
    provideContributionComponent("virtual-pet", "overlay", "pet", Pet, {
      desktopPet: true,
      firstParty: true,
    })
    await refreshOverlayCapabilities()
    expect(invoke.mock.calls.some(([name]) => name === "open_overlay_window")).toBe(false)
    registerPackageContributions(record)
    const viewport = viewportContent(key)
    let request
    vi.useFakeTimers()
    try {
      syncOverlayWindows()
      await vi.advanceTimersByTimeAsync(10001)
      expect(invoke.mock.calls.some(([name]) => name === "open_overlay_window")).toBe(false)
      expect(windowFailure(key)).toBe("")
      let finishOpen
      invoke.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishOpen = resolve
          }),
      )
      viewport.surface.ready()
      const cancelled = invoke.mock.calls.find(([name]) => name === "open_overlay_window")[1]
        .args[0]
      expect(windowStates.get(key).phase).toBe("opening")
      viewport.surface.ready(false)
      expect(windowStates.has(key)).toBe(false)
      await receiveOverlayReady({ detail: { key, generation: cancelled.generation } })
      await vi.advanceTimersByTimeAsync(10001)
      expect(windowFailure(key)).toBe("")
      expect(invoke.mock.calls.some(([name]) => name === "show_overlay_window")).toBe(false)
      expect(invoke.mock.calls.some(([name]) => name === "close_overlay_window")).toBe(false)
      viewport.surface.ready()
      expect(invoke.mock.calls.filter(([name]) => name === "open_overlay_window")).toHaveLength(1)
      let finishClose
      invoke.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishClose = resolve
          }),
      )
      finishOpen(true)
      await flushPromises()
      expect(invoke).toHaveBeenLastCalledWith("close_overlay_window", { args: [cancelled.label] })
      syncOverlayWindows()
      expect(invoke.mock.calls.filter(([name]) => name === "open_overlay_window")).toHaveLength(1)
      finishClose(true)
      await flushPromises()
      expect(invoke.mock.calls.filter(([name]) => name === "open_overlay_window")).toHaveLength(2)
      request = invoke.mock.calls.filter(([name]) => name === "open_overlay_window").at(-1)[1]
        .args[0]
      expect(request.generation).not.toBe(cancelled.generation)
      expect(windowActive(key)).toBe(false)
      await receiveOverlayReady({ detail: { key, generation: cancelled.generation } })
      expect(windowActive(key)).toBe(false)
      expect(invoke.mock.calls.some(([name]) => name === "show_overlay_window")).toBe(false)
      await receiveOverlayReady({ detail: { key, generation: request.generation } })
      await vi.advanceTimersByTimeAsync(10001)
      expect(windowActive(key)).toBe(true)
      expect(windowFailure(key)).toBe("")
      expect(invoke).toHaveBeenCalledWith("show_overlay_window", {
        args: [request.label, request.generation],
      })
    } finally {
      vi.useRealTimers()
    }
    retryOverlayWindow(key)
    await flushPromises()
    const again = invoke.mock.calls.filter(([name]) => name === "open_overlay_window").at(-1)[1]
      .args[0]
    let showed
    invoke.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          showed = resolve
        }),
    )
    const presenting = receiveOverlayReady({ detail: { key, generation: again.generation } })
    await nextTick()
    startOverlayWindows()
    window.dispatchEvent(
      new CustomEvent("kt:overlay-closed", { detail: { key, generation: again.generation } }),
    )
    showed(true)
    await presenting
    expect(windowActive(key)).toBe(false)
    expect(windowStates.get(key).phase).toBe("failed")
    setWindowAllowed(key, false)
    resetOverlay(key)
    expect(windowPreference(key)).toBe(false)
    syncOverlayWindows()
    await flushPromises()
    expect(windowActive(key)).toBe(false)
    expect(invoke).toHaveBeenCalledWith("close_overlay_window", { args: [request.label] })
    setWindowAllowed(key, true)
    syncOverlayWindows()
    await flushPromises()
    hideOverlay(key)
    syncOverlayWindows()
    const late = invoke.mock.calls.filter(([name]) => name === "open_overlay_window").at(-1)[1]
      .args[0]
    await receiveOverlayReady({ detail: { key, generation: late.generation } })
    expect(windowActive(key)).toBe(false)
    await flushPromises()
    resetNativeOverlay(key)
    expect(isOverlayVisible(key)).toBe(false)
    expect(windowPreference(key)).toBe(true)
    loadPlacements("win-host")
    showOverlay(key)
    syncOverlayWindows()
    await flushPromises()
    const next = invoke.mock.calls.filter(([name]) => name === "open_overlay_window").at(-1)[1]
      .args[0]
    const callStart = invoke.mock.calls.length
    let finishReset
    invoke.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishReset = resolve
        }),
    )
    const resetting = receiveOverlayReady({ detail: { key, generation: next.generation } })
    await flushPromises()
    loadPlacements("win-host")
    finishReset(true)
    await resetting
    expect(invoke.mock.calls.slice(callStart).map(([name]) => name)).toEqual([
      "reset_overlay_window",
      "show_overlay_window",
    ])
    expect(windowActive(key)).toBe(true)
    hideOverlay(key)
    syncOverlayWindows()
    await flushPromises()
    showOverlay(key)
    syncOverlayWindows()
    await flushPromises()
    const restored = invoke.mock.calls.filter(([name]) => name === "open_overlay_window").at(-1)[1]
      .args[0]
    const restoreStart = invoke.mock.calls.length
    const restoring = receiveOverlayReady({ detail: { key, generation: restored.generation } })
    expect(windowStates.get(key).phase).toBe("presenting")
    viewport.dispose()
    await restoring
    expect(invoke.mock.calls.slice(restoreStart).map(([name]) => name)).toEqual([
      "show_overlay_window",
    ])
    syncOverlayWindows()
    expect(windowActive(key)).toBe(true)
    hideOverlay(key)
    syncOverlayWindows()
    await flushPromises()
    const opened = invoke.mock.calls.filter(([name]) => name === "open_overlay_window").length
    showOverlay(key)
    syncOverlayWindows()
    await flushPromises()
    expect(invoke.mock.calls.filter(([name]) => name === "open_overlay_window")).toHaveLength(
      opened,
    )
    const empty = viewportContent(key)
    empty.surface.ready(false)
    syncOverlayWindows()
    expect(windowFailure(key)).toBe("")
    expect(invoke.mock.calls.filter(([name]) => name === "open_overlay_window")).toHaveLength(
      opened,
    )
    hideOverlay(key)
    empty.dispose()
  })

  it("falls back on timeout or open failure without endless retry and rejects late readiness", async () => {
    vi.useFakeTimers()
    const key = "ext:virtual-pet/pet"
    try {
      const invoke = tauri()
      invoke.mockImplementation(async (name) =>
        name === "get_desktop_capabilities"
          ? {
              surface: "desktop",
              protocol: 1,
              overlayWindows: true,
              overlayPointer: true,
              overlayPresentation: 1,
            }
          : true,
      )
      registerPackageContributions({
        package: "virtual-pet",
        desktop_pet: true,
        contributes: [
          {
            kind: "overlay",
            id: "pet",
            owner: "package:virtual-pet",
            overlay: { surface: "window" },
          },
        ],
      })
      const viewport = viewportContent(key)
      viewport.surface.ready()
      await refreshOverlayCapabilities()
      const request = invoke.mock.calls.find(([name]) => name === "open_overlay_window")[1].args[0]
      await vi.advanceTimersByTimeAsync(10001)
      expect(windowStates.get(key).phase).toBe("failed")
      expect(windowFailure(key)).toContain("ready")
      await receiveOverlayReady({ detail: { key, generation: request.generation } })
      expect(windowActive(key)).toBe(false)
      syncOverlayWindows()
      expect(invoke.mock.calls.filter(([name]) => name === "open_overlay_window")).toHaveLength(1)
      invoke.mockImplementation(async (name) => name !== "open_overlay_window")
      retryOverlayWindow(key)
      await vi.advanceTimersByTimeAsync(1)
      expect(windowFailure(key)).toContain("Could not open")
      viewport.surface.ready(false)
      expect(windowFailure(key)).toContain("Could not open")
      viewport.surface.ready()
      syncOverlayWindows()
      expect(windowFailure(key)).toContain("Could not open")
      viewport.dispose()
      hideOverlay(key)
      syncOverlayWindows()
      await vi.advanceTimersByTimeAsync(1)
      showOverlay(key)
      invoke.mockImplementation(async (name) =>
        name === "get_desktop_capabilities"
          ? { surface: "desktop", protocol: 1, overlayWindows: true }
          : true,
      )
      await refreshOverlayCapabilities()
      expect(windowActive(key)).toBe(false)
      expect(windowFailure(key)).toContain("unavailable")
      hideOverlay(key)
      syncOverlayWindows()
    } finally {
      vi.useRealTimers()
    }
  })

  it("follows visibility and consent changes while running", async () => {
    declare()
    const invoke = tauri()
    await desktopCapabilities()
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

  it("fills its zoom-adjusted parent rather than viewport units", () => {
    const style = document.createElement("style")
    style.textContent = parse(overlayWindowSource)
      .descriptor.styles.map((part) => part.content)
      .join("\n")
    let page
    try {
      document.head.append(style)
      declare({ clickThrough: true })
      tauri()
      page = mount(OverlayWindowApp, { props: { overlayKey: KEY }, attachTo: document.body })
      const rootStyle = getComputedStyle(page.element)
      expect(rootStyle.width).toBe("100%")
      expect(rootStyle.height).toBe("100%")
    } finally {
      page?.unmount()
      style.remove()
    }
  })

  it("acknowledges itself, draws the overlay with chrome and hides through the shell", async () => {
    declare()
    const invoke = tauri()
    await desktopCapabilities()
    const page = mount(OverlayWindowApp, { props: { overlayKey: KEY } })
    await flushPromises()
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
      vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
        left: 10,
        top: 20,
        width: 30,
        height: 40,
      })
      declare({ clickThrough: true })
      const invoke = tauri()
      await desktopCapabilities()
      const page = mount(OverlayWindowApp, { props: { overlayKey: KEY }, attachTo: document.body })
      expect(page.find('[data-test="overlay-window-bar"]').exists()).toBe(false)
      await vi.advanceTimersByTimeAsync(250)
      const call = invoke.mock.calls.find(([name]) => name === "overlay_set_hit_regions")
      expect(call[1].args[0]).toHaveLength(1)
      const before = invoke.mock.calls.length
      await vi.advanceTimersByTimeAsync(1000)
      expect(invoke.mock.calls.length).toBe(before)
      const hitCalls = () =>
        invoke.mock.calls.filter(([name]) => name === "overlay_set_hit_regions")
      window.dispatchEvent(new CustomEvent("kt-overlay-regions", { detail: [[50, 40, 100, 200]] }))
      expect(hitCalls().at(-1)[1].args[0]).toEqual([[50, 40, 100, 200]])
      window.dispatchEvent(new CustomEvent("kt-overlay-regions", { detail: [null] }))
      const explicitCount = hitCalls().length
      await vi.advanceTimersByTimeAsync(1000)
      expect(hitCalls().length).toBe(explicitCount)
      page.get(".pet").element.getBoundingClientRect = () => ({
        left: 15,
        top: 10,
        width: 250,
        height: 60,
      })
      window.dispatchEvent(new CustomEvent("kt-overlay-regions", { detail: null }))
      await flushPromises()
      expect(hitCalls().at(-1)[1].args[0]).toEqual([[15, 10, 250, 60]])
      page.unmount()
    } finally {
      vi.useRealTimers()
      vi.restoreAllMocks()
    }
  })

  it("retires its original window on host rebinding or authority loss without showing the new user's pet", async () => {
    const invoke = tauri()
    declare()
    desktopContextConnected.value = true
    extState.hostKey = "alice"
    window.history.replaceState(
      {},
      "",
      `/?kt_overlay=${encodeURIComponent(KEY)}&kt_overlay_label=overlay-original&kt_overlay_generation=test&kt_overlay_host=alice`,
    )
    const page = mount(OverlayWindowApp, { props: { overlayKey: KEY } })
    await nextTick()
    expect(page.find(".pet").exists()).toBe(true)
    extState.hostKey = "bob"
    await nextTick()
    expect(page.find(".pet").exists()).toBe(false)
    expect(invoke).toHaveBeenCalledWith("close_overlay_window", { args: ["overlay-original"] })
    desktopContextConnected.value = false
    await nextTick()
    expect(page.find(".pet").exists()).toBe(false)
    page.unmount()
    window.history.replaceState({}, "", "/")
  })

  it("says so when the overlay is not provided by any extension", async () => {
    tauri()
    await desktopCapabilities()
    const page = mount(OverlayWindowApp, { props: { overlayKey: "ext:none/x" } })
    expect(page.find('[data-test="overlay-window-missing"]').text()).toContain("not available")
    page.unmount()
  })

  it("gives the SDK a window-mode useOverlay", async () => {
    const invoke = tauri()
    await desktopCapabilities()
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
    expect(invoke).toHaveBeenLastCalledWith("overlay_resize", {
      args: [300, 200],
    })
    let prevented = false
    await control.startDrag({ preventDefault: () => (prevented = true) })
    expect(prevented).toBe(true)
    expect(invoke).toHaveBeenLastCalledWith("overlay_start_drag", {})
  })
})
