import { afterEach, expect, it, vi } from "vitest"
import {
  registerOverlay,
  clearOverlays,
  loadPlacements,
  hideOverlay,
  isOverlayVisible,
  placements,
} from "./overlays"
import {
  restoreStartupVisibility,
  applyStartupFallbacks,
  consumeStartupFallback,
  resetStartupChecks,
  visibilityKey,
} from "./overlayStartup"
import { _resetUIPrefsForTests } from "@/utils/uiPrefs"
const backend = vi.hoisted(() => ({ values: {} }))
vi.mock("@/utils/api", () => ({
  settingsAPI: {
    getUIPrefs: async () => ({ values: backend.values }),
    updateUIPrefs: async (values) => {
      Object.assign(backend.values, values)
      return { values: backend.values }
    },
  },
}))
afterEach(() => {
  clearOverlays()
  resetStartupChecks()
  _resetUIPrefsForTests()
  localStorage.clear()
  backend.values = {}
  vi.useRealTimers()
})
const add = (key, options) =>
  registerOverlay({ key, pkg: "p", id: key, component: { template: "<div />" }, options })
it("checks all registered pets once, restores hidden choices after restart, and never interrupts a later user choice", async () => {
  vi.useFakeTimers()
  loadPlacements("host")
  add("ext:sprite/pet", { startupGroup: "pet", defaultVisible: true })
  add("ext:l2d/pet", { startupGroup: "pet", startupFallback: true, defaultVisible: false })
  applyStartupFallbacks("host")
  expect(isOverlayVisible("ext:l2d/pet")).toBe(false)
  hideOverlay("ext:sprite/pet")
  applyStartupFallbacks("host")
  expect(isOverlayVisible("ext:l2d/pet")).toBe(false)
  await vi.advanceTimersByTimeAsync(1600)
  expect(backend.values[visibilityKey("host")]["ext:sprite/pet"]).toBe(false)
  resetStartupChecks()
  localStorage.clear()
  _resetUIPrefsForTests()
  loadPlacements("host")
  await restoreStartupVisibility("host", () => true)
  add("ext:agents/hud", { defaultVisible: true })
  applyStartupFallbacks("host")
  expect(isOverlayVisible("ext:sprite/pet")).toBe(false)
  expect(isOverlayVisible("ext:l2d/pet")).toBe(true)
  applyStartupFallbacks("other")
  expect(consumeStartupFallback("ext:l2d/pet", "other")).toBe(false)
  expect(consumeStartupFallback("ext:l2d/pet", "host")).toBe(true)
  expect(consumeStartupFallback("ext:l2d/pet", "host")).toBe(false)
  hideOverlay("ext:l2d/pet")
  applyStartupFallbacks("host")
  expect(isOverlayVisible("ext:l2d/pet")).toBe(false)
  resetStartupChecks()
  loadPlacements("other")
  await restoreStartupVisibility("host", () => false)
  expect(placements["ext:sprite/pet"]).toBeUndefined()
})
