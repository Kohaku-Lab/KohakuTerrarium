import { afterEach, expect, it, vi } from "vitest"
import { trackWindowPlacement } from "./windowPlacement"
import { resizeOverlayWindow } from "@/utils/hostBridge"
import { resetOverlay, showOverlay, hideOverlay, isOverlayVisible } from "./overlays"

it("consumes a reset requested while the native overlay was closed on its next open", async () => {
  vi.useFakeTimers()
  let native = { version: 1, x: 2200, y: 200, width: 600, height: 800 }
  const invoke = vi.fn(async (command, { args } = {}) => {
    if (command === "get_desktop_capabilities")
      return {
        surface: "desktop",
        protocol: 1,
        overlayPlacementV1: true,
        overlayPlacementNative: true,
      }
    if (command === "overlay_placement" && args[0] === "reset")
      native = { ...native, x: 400, y: 100 }
    if (command === "overlay_get_bounds") return [native.x, native.y, native.width, native.height]
    return native
  })
  window.__TAURI_INTERNALS__ = { invoke }
  hideOverlay("closed-pet")
  resetOverlay("closed-pet")
  expect(isOverlayVisible("closed-pet")).toBe(false)
  showOverlay("closed-pet")
  const stop = trackWindowPlacement("closed-pet")
  await vi.advanceTimersByTimeAsync(50)
  expect(native.x).toBe(400)
  expect(localStorage.getItem("kt.overlay.reset.closed-pet")).toBeNull()
  stop()
  native.x = 700
  const stopAgain = trackWindowPlacement("closed-pet")
  await vi.advanceTimersByTimeAsync(50)
  expect(native.x).toBe(700)
  expect(
    invoke.mock.calls.filter(
      ([command, payload]) => command === "overlay_placement" && payload.args[0] === "reset",
    ),
  ).toHaveLength(1)
  stopAgain()
})

afterEach(() => {
  vi.useRealTimers()
  delete window.__TAURI_INTERNALS__
  localStorage.clear()
})

it("uses native persistence and bounds events without frontend placement writes", async () => {
  vi.useFakeTimers()
  const placement = { version: 1, x: 2200, y: 200, width: 600, height: 800 }
  localStorage.setItem("kt.overlay.window.pet", JSON.stringify(placement))
  const invoke = vi.fn(async (command) =>
    command === "get_desktop_capabilities"
      ? { surface: "desktop", protocol: 1, overlayPlacementV1: true, overlayPlacementNative: true }
      : command === "overlay_get_bounds"
        ? [1100, 100, 300, 400]
        : placement,
  )
  window.__TAURI_INTERNALS__ = { invoke }
  const stop = trackWindowPlacement("pet")
  try {
    await vi.advanceTimersByTimeAsync(1000)
    expect(invoke).toHaveBeenCalledWith("overlay_placement", { args: ["validate", null] })
    expect(JSON.parse(localStorage.getItem("kt.overlay.window.pet"))).toEqual(placement)
    expect(invoke.mock.calls.some(([c]) => c === "overlay_set_bounds")).toBe(false)
  } finally {
    stop()
  }
})

it("retains the legacy shell resize command when native resize is unavailable", async () => {
  const invoke = vi.fn(async (command) => {
    if (command === "get_desktop_capabilities")
      return { surface: "desktop", protocol: 1, overlayWindows: true }
    if (command === "overlay_set_bounds") return true
    throw new Error("Unknown command")
  })
  window.__TAURI_INTERNALS__ = { invoke }
  expect(await resizeOverlayWindow(500, 800)).toBe(true)
  expect(invoke).toHaveBeenLastCalledWith("overlay_set_bounds", {
    args: [window.screenX, window.screenY, 500, 800],
  })
  expect(invoke.mock.calls.some(([c]) => c === "overlay_resize")).toBe(false)
})
