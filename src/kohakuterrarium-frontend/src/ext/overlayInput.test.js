import { flushPromises, mount } from "@vue/test-utils"
import { defineComponent, h } from "vue"
import { afterEach, expect, it, vi } from "vitest"

import {
  beginOverlayMove,
  desktopPointer,
  installOverlayInput,
  overlayHitRegions,
  POINTER_EVENT,
  useOverlayPointer,
} from "@/ext/overlayInput"

const rect = { left: 10, top: 20, width: 40, height: 50 }
function event(type, values = {}) {
  const e = new Event(type, { bubbles: true })
  for (const [name, value] of Object.entries(values)) Object.defineProperty(e, name, { value })
  return e
}
afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  delete window.__TAURI_INTERNALS__
  document.body.replaceChildren()
})

it("excludes transparent or hidden controls and never enlarges hit regions for gaze", () => {
  document.body.innerHTML =
    '<div><button class="kt-overlay-hit">control</button></div><div class="kt-overlay-hit">pet</div>'
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(rect)
  expect(overlayHitRegions()).toEqual([
    [10, 20, 40, 50],
    [10, 20, 40, 50],
  ])
  const parent = document.body.firstElementChild
  for (const [property, value] of [
    ["opacity", "0"],
    ["visibility", "hidden"],
    ["display", "none"],
  ]) {
    parent.style[property] = value
    expect(overlayHitRegions()).toEqual([[10, 20, 40, 50]])
    parent.style[property] = ""
  }
  parent.hidden = true
  expect(overlayHitRegions()).toEqual([[10, 20, 40, 50]])
})

it("captures through release, serializes native reports and expires lost samples", async () => {
  vi.useFakeTimers()
  const invoke = vi.fn(async () => true)
  window.__TAURI_INTERNALS__ = { invoke }
  document.body.innerHTML = '<div class="kt-overlay-hit">pet</div>'
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(rect)
  const dispose = installOverlayInput(
    () => true,
    () => true,
  )
  await vi.advanceTimersByTimeAsync(1)
  const target = document.body.firstElementChild
  target.dispatchEvent(
    event("pointerdown", { button: 0, isPrimary: true, clientX: 22, clientY: 36 }),
  )
  expect(beginOverlayMove()).toBe(true)
  await vi.advanceTimersByTimeAsync(1)
  expect(invoke.mock.calls.at(-1)[1].args[1]).toMatchObject({
    captured: true,
    dragging: true,
    pointer: true,
    press: expect.objectContaining({ x: 22, y: 36 }),
  })
  window.dispatchEvent(
    new CustomEvent(POINTER_EVENT, {
      detail: {
        valid: true,
        pointerType: "mouse",
        x: -70,
        y: 200,
        captured: true,
        dragX: 15,
        dragY: 20,
      },
    }),
  )
  expect(desktopPointer.value.x).toBe(-70)
  await vi.advanceTimersByTimeAsync(1200)
  expect(desktopPointer.value).toBeNull()
  target.dispatchEvent(event("pointerup", { button: 0 }))
  await vi.advanceTimersByTimeAsync(1)
  expect(invoke.mock.calls.at(-1)[1].args[1].captured).toBe(false)
  expect(beginOverlayMove()).toBe(false)
  target.dispatchEvent(event("pointerdown", { button: 0, isPrimary: true, pointerId: 1 }))
  target.dispatchEvent(event("lostpointercapture", { pointerId: 1 }))
  await vi.advanceTimersByTimeAsync(1)
  expect(invoke.mock.calls.at(-1)[1].args[1].captured).toBe(false)
  const before = invoke.mock.calls.length
  await vi.advanceTimersByTimeAsync(1000)
  expect(invoke.mock.calls.length).toBe(before)
  dispose()
  expect(invoke.mock.calls.at(-1)[1].args).toEqual([
    [],
    { captured: false, dragging: false, pointer: false, enabled: false },
  ])
  expect(vi.getTimerCount()).toBe(0)
})

it("keeps separate capture identities across a coalesced release and repress", async () => {
  let complete
  const invoke = vi.fn(async () => true)
  window.__TAURI_INTERNALS__ = { invoke }
  document.body.innerHTML = '<button class="kt-overlay-hit">pet</button>'
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(rect)
  const dispose = installOverlayInput(
    () => true,
    () => true,
  )
  await flushPromises()
  invoke.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = resolve
      }),
  )
  const target = document.body.firstElementChild
  target.dispatchEvent(event("pointerdown", { button: 0, clientX: 22, clientY: 36 }))
  const first = invoke.mock.calls.at(-1)[1].args[1].press
  document.dispatchEvent(event("pointerup"))
  target.dispatchEvent(event("pointerdown", { button: 0, clientX: 30, clientY: 40 }))
  complete(true)
  await flushPromises()
  const second = invoke.mock.calls.at(-1)[1].args[1].press
  expect(second.id).not.toBe(first.id)
  window.dispatchEvent(
    new CustomEvent(POINTER_EVENT, {
      detail: { valid: true, captured: true, captureId: first.id, dragX: 80, dragY: 0 },
    }),
  )
  expect(desktopPointer.value.captured).toBe(false)
  window.dispatchEvent(
    new CustomEvent(POINTER_EVENT, {
      detail: { valid: true, captured: true, captureId: second.id, dragX: 0, dragY: 0 },
    }),
  )
  expect(desktopPointer.value.captured).toBe(true)
  document.dispatchEvent(event("pointerup"))
  await flushPromises()
  window.dispatchEvent(
    new CustomEvent(POINTER_EVENT, {
      detail: { valid: true, captured: true, captureId: second.id },
    }),
  )
  expect(desktopPointer.value.captured).toBe(false)
  dispose()
})

it("does not enable selective hit-testing in ordinary overlays on release or blur", async () => {
  const invoke = vi.fn(async () => true)
  window.__TAURI_INTERNALS__ = { invoke }
  const dispose = installOverlayInput(() => false)
  document.dispatchEvent(event("pointerup"))
  window.dispatchEvent(event("blur"))
  await flushPromises()
  dispose()
  expect(invoke).not.toHaveBeenCalled()
})

it("releases native input if the captured sprite is removed", async () => {
  vi.useFakeTimers()
  const invoke = vi.fn(async () => true)
  window.__TAURI_INTERNALS__ = { invoke }
  document.body.innerHTML = '<button class="kt-overlay-hit">pet</button>'
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(rect)
  const dispose = installOverlayInput(
    () => true,
    () => true,
  )
  document.body.firstElementChild.dispatchEvent(
    event("pointerdown", { button: 0, isPrimary: true }),
  )
  beginOverlayMove()
  await vi.advanceTimersByTimeAsync(1)
  document.body.replaceChildren()
  await vi.advanceTimersByTimeAsync(2100)
  expect(invoke.mock.calls.at(-1)[1].args[1]).toMatchObject({ captured: false, dragging: false })
  dispose()
})

it("browser pointer is mouse-only and invalidated on leave/blur with listener disposal", async () => {
  let point
  const wrapper = mount(
    defineComponent({
      setup() {
        point = useOverlayPointer("app")
        return () => h("div")
      },
    }),
  )
  window.dispatchEvent(event("pointermove", { pointerType: "mouse", clientX: 40, clientY: 80 }))
  expect(point.value).toEqual({ valid: true, pointerType: "mouse", x: 40, y: 80 })
  window.dispatchEvent(event("pointermove", { pointerType: "touch", clientX: 50, clientY: 70 }))
  expect(point.value).toBeNull()
  window.dispatchEvent(event("pointermove", { pointerType: "mouse", clientX: 20, clientY: 20 }))
  document.documentElement.dispatchEvent(event("pointerleave"))
  expect(point.value).toBeNull()
  window.dispatchEvent(event("pointermove", { pointerType: "mouse", clientX: 30, clientY: 30 }))
  window.dispatchEvent(event("blur"))
  expect(point.value).toBeNull()
  wrapper.unmount()
  window.dispatchEvent(event("pointermove", { pointerType: "mouse", clientX: 30, clientY: 30 }))
  await flushPromises()
  expect(point.value).toBeNull()
})
