import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

import OverlayLayer from "@/ext/OverlayLayer.vue"
import {
  OVERLAY_MARGIN,
  beginDrag,
  hideOverlay,
  isOverlayVisible,
  loadPlacements,
  moveOverlay,
  overlayBounds,
  overlays,
  placements,
  resetOverlay,
  resizeOverlay,
  resolveBounds,
  showOverlay,
  toggleOverlay,
  viewport,
} from "@/ext/overlays"
import {
  provideContributionComponent,
  registerPackageContributions,
  unregisterAllContributions,
} from "@/ext/registry"
import { extState } from "@/ext/state"

const VP = { width: 1000, height: 600 }
const Pet = { template: '<div class="pet">pet</div>' }

function declare(overlay = {}) {
  const rec = {
    package: "pets",
    enabled: true,
    status: "idle",
    contributes: [{ kind: "overlay", id: "cat", label: "Cat", owner: "package:pets", overlay }],
  }
  extState.packages = [rec]
  registerPackageContributions(rec)
  return "ext:pets/cat"
}

describe("overlay geometry", () => {
  it("places each anchor inside the viewport with a margin", () => {
    const base = { width: 200, height: 100 }
    const at = (anchor) => resolveBounds({ ...base, anchor }, {}, VP)
    expect(at("top-left")).toMatchObject({ left: OVERLAY_MARGIN, top: OVERLAY_MARGIN })
    expect(at("bottom-right")).toMatchObject({
      left: VP.width - 200 - OVERLAY_MARGIN,
      top: VP.height - 100 - OVERLAY_MARGIN,
    })
    expect(at("center")).toMatchObject({ left: 400, top: 250 })
  })

  it("prefers the saved position and clamps it to the viewport", () => {
    const options = { width: 200, height: 100, anchor: "top-left" }
    expect(resolveBounds(options, { left: 50, top: 60 }, VP)).toMatchObject({ left: 50, top: 60 })
    expect(resolveBounds(options, { left: 5000, top: -40 }, VP)).toMatchObject({
      left: 800,
      top: 0,
    })
    expect(resolveBounds(options, { width: 9999, height: 1 }, VP)).toMatchObject({
      width: 1000,
      height: 48,
    })
  })
})

describe("overlay state", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    localStorage.clear()
    loadPlacements("test-host")
    viewport.width = VP.width
    viewport.height = VP.height
  })

  it("registers from the manifest, toggles, moves, resizes and persists per host", () => {
    const key = declare({ width: 200, height: 100, anchor: "top-left", resizable: true })
    expect(overlays.get(key).options.anchor).toBe("top-left")
    expect(isOverlayVisible(key)).toBe(true)

    hideOverlay(key)
    expect(isOverlayVisible(key)).toBe(false)
    toggleOverlay(key)
    expect(isOverlayVisible(key)).toBe(true)

    moveOverlay(key, 300, 200)
    resizeOverlay(key, 250, 150)
    expect(overlayBounds(key)).toEqual({ left: 300, top: 200, width: 250, height: 150 })

    loadPlacements("other-host")
    expect(overlayBounds(key)).toMatchObject({ left: OVERLAY_MARGIN })
    loadPlacements("test-host")
    expect(overlayBounds(key)).toEqual({ left: 300, top: 200, width: 250, height: 150 })

    resetOverlay(key)
    expect(overlayBounds(key)).toMatchObject({ left: OVERLAY_MARGIN, top: OVERLAY_MARGIN })
  })

  it("honours defaultVisible and keeps the user's choice over it", () => {
    const key = declare({ defaultVisible: false })
    expect(isOverlayVisible(key)).toBe(false)
    showOverlay(key)
    expect(isOverlayVisible(key)).toBe(true)
    resetOverlay(key)
    expect(isOverlayVisible(key)).toBe(true)
  })

  it("survives blocked storage", () => {
    const key = declare()
    const original = Storage.prototype.setItem
    Storage.prototype.setItem = () => {
      throw new Error("blocked")
    }
    try {
      hideOverlay(key)
      expect(isOverlayVisible(key)).toBe(false)
    } finally {
      Storage.prototype.setItem = original
    }
  })

  it("is removed with the rest of the contributions", () => {
    declare()
    expect(overlays.size).toBe(1)
    unregisterAllContributions()
    expect(overlays.size).toBe(0)
    expect(Object.keys(placements)).toEqual([])
  })

  it("lets code override manifest options and adds undeclared overlays", () => {
    const key = declare({ width: 200 })
    provideContributionComponent("pets", "overlay", "cat", Pet, { anchor: "center" })
    expect(overlays.get(key).options).toMatchObject({ width: 200, anchor: "center" })
    provideContributionComponent("pets", "overlay", "dog", Pet, { label: "Dog", width: 90 })
    expect(overlays.get("ext:pets/dog")).toMatchObject({ label: "Dog" })
    expect(overlays.get("ext:pets/dog").options.width).toBe(90)
    expect(() =>
      provideContributionComponent("pets", "overlay", "bad", Pet, { owner: "plugin:x" }),
    ).toThrow(/options.owner/)
  })

  it("drags only when draggable and resizes only when resizable", () => {
    const key = declare({ width: 200, height: 100, anchor: "top-left", draggable: false })
    beginDrag(key, { button: 0, clientX: 0, clientY: 0, preventDefault() {} })
    window.dispatchEvent(new MouseEvent("pointermove", { clientX: 90, clientY: 90 }))
    expect(overlayBounds(key)).toMatchObject({ left: OVERLAY_MARGIN })

    provideContributionComponent("pets", "overlay", "cat", Pet, { draggable: true })
    let prevented = false
    beginDrag(key, {
      button: 0,
      clientX: 10,
      clientY: 10,
      preventDefault: () => (prevented = true),
    })
    const move = new Event("pointermove")
    Object.assign(move, { clientX: 60, clientY: 40 })
    window.dispatchEvent(move)
    window.dispatchEvent(new Event("pointerup"))
    expect(prevented).toBe(true)
    expect(overlayBounds(key)).toMatchObject({
      left: OVERLAY_MARGIN + 50,
      top: OVERLAY_MARGIN + 30,
    })
  })
})

describe("OverlayLayer", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    unregisterAllContributions()
    localStorage.clear()
    loadPlacements("layer-host")
  })

  it("draws visible overlays with chrome, hides them on close and honours clickThrough", async () => {
    const key = declare({ width: 200, height: 100 })
    provideContributionComponent("pets", "overlay", "cat", Pet)
    const wrapper = mount(OverlayLayer, {
      global: { stubs: { ExtensionContribution: true } },
    })
    expect(wrapper.findAll('[data-test="overlay-frame"]')).toHaveLength(1)
    expect(wrapper.find('[data-test="overlay-bar"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="overlay-resize"]').exists()).toBe(false)

    await wrapper.find('[data-test="overlay-close"]').trigger("click")
    expect(wrapper.findAll('[data-test="overlay-frame"]')).toHaveLength(0)
    expect(isOverlayVisible(key)).toBe(false)

    showOverlay(key)
    provideContributionComponent("pets", "overlay", "cat", Pet, { clickThrough: true })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="overlay-bar"]').exists()).toBe(false)
    expect(wrapper.find(".kt-overlay--through").exists()).toBe(true)
    wrapper.unmount()
  })
})
