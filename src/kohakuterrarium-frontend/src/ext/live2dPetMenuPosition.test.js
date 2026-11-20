import { afterEach, expect, it, vi } from "vitest"
import {
  connectMenuPosition,
  intersectViewport,
  placeMenu,
} from "../../../../examples/packages/live2d_pet/ui/menu-position.js"

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  history.replaceState(null, "", "/")
  document.body.replaceChildren()
})

it("keeps the complete menu inside the visible part at every screen edge", () => {
  const menu = document.createElement("section")
  Object.defineProperties(menu, {
    offsetWidth: { get: () => Number.parseFloat(menu.style.width) },
    offsetHeight: { get: () => Math.min(520, Number.parseFloat(menu.style.maxHeight)) },
  })
  for (const viewport of [
    [0, 0, 380, 600],
    [240, 0, 360, 600],
    [0, 200, 380, 400],
    [180, 250, 210, 280],
    [0, 0, 600, 700],
  ]) {
    for (const [x, y] of [
      [0, 0],
      [599, 0],
      [0, 699],
      [599, 699],
    ]) {
      placeMenu(menu, x, y, viewport)
      const [left, top, width, height] = viewport
      expect(Number.parseFloat(menu.style.left)).toBeGreaterThanOrEqual(left + 6)
      expect(Number.parseFloat(menu.style.top)).toBeGreaterThanOrEqual(top + 6)
      expect(Number.parseFloat(menu.style.left) + menu.offsetWidth).toBeLessThanOrEqual(
        left + width - 6,
      )
      expect(Number.parseFloat(menu.style.top) + menu.offsetHeight).toBeLessThanOrEqual(
        top + height - 6,
      )
    }
  }
  expect(intersectViewport([-20, -10, 300, 400], 250, 350)).toEqual([0, 0, 250, 350])
  expect(intersectViewport(null, 600, 700)).toEqual([0, 0, 600, 700])
  expect(intersectViewport([NaN, 0, 10, 20], 600, 700)).toEqual([0, 0, 600, 700])
})

it("waits for its current host viewport, ignores stale replies, and cancels on close", async () => {
  vi.useFakeTimers()
  history.replaceState(null, "", "/stage.html?mini=1&channel=menu")
  vi.spyOn(document, "referrer", "get").mockReturnValue("http://localhost/")
  const host = { postMessage: vi.fn() }
  vi.stubGlobal("parent", host)
  const menu = document.createElement("section")
  menu.style.visibility = "hidden"
  const position = connectMenuPosition(menu)
  const reply = (request, source = host, channel = "menu") =>
    window.dispatchEvent(
      new MessageEvent("message", {
        source,
        origin: "http://localhost",
        data: {
          type: "kt-live2d-menu-viewport",
          channel,
          request,
          viewport: [200, 100, 320, 500],
        },
      }),
    )
  try {
    const first = position.position(300, 400)
    const old = host.postMessage.mock.lastCall[0].request
    const second = position.position(250, 150)
    const current = host.postMessage.mock.lastCall[0].request
    reply(old)
    reply(current, null)
    reply(current, host, "stale")
    await Promise.resolve()
    expect(menu.style.visibility).toBe("hidden")
    reply(current)
    await Promise.all([first, second])
    expect(menu.style.left).toBe("250px")
    expect(menu.style.top).toBe("150px")
    expect(menu.style.maxHeight).toBe("488px")
    expect(menu.style.visibility).toBe("")
    const cancelled = position.position(0, 0)
    position.cancel()
    menu.hidden = true
    reply(host.postMessage.mock.lastCall[0].request)
    await cancelled
    expect(menu.style.left).toBe("250px")
    menu.hidden = false
    const fallback = position.position(10, 20)
    await vi.advanceTimersByTimeAsync(500)
    await fallback
    expect(menu.style.left).toBe("10px")
    position.dispose()
    const count = host.postMessage.mock.calls.length
    window.dispatchEvent(new Event("resize"))
    expect(host.postMessage).toHaveBeenCalledTimes(count)
  } finally {
    position.dispose()
  }
})

it("places a normal preview menu immediately without waiting for an overlay host", async () => {
  vi.useFakeTimers()
  history.replaceState(null, "", "/stage.html?channel=preview")
  vi.spyOn(document, "referrer", "get").mockReturnValue("http://localhost/")
  const host = { postMessage: vi.fn() }
  vi.stubGlobal("parent", host)
  const menu = document.createElement("section")
  menu.style.visibility = "hidden"
  const position = connectMenuPosition(menu)
  try {
    const placed = position.position(25, 40)
    await Promise.resolve()
    expect(host.postMessage).not.toHaveBeenCalled()
    await placed
    expect(menu.style.visibility).toBe("")
    expect(menu.style.left).toBe("25px")
    expect(vi.getTimerCount()).toBe(0)
  } finally {
    position.dispose()
  }
})
