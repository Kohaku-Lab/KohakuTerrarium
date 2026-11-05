import { expect, it, vi } from "vitest"
import {
  normalizedRect,
  modelRectangle,
  validRegions,
  regionsClip,
  connectCrop,
} from "../../../../examples/packages/live2d_pet/ui/crop.js"

it("clamps crop rectangles to the frame and excludes empty or invalid bounds", () => {
  expect(normalizedRect({ left: -8, top: 20.2, right: 120, bottom: 70.1 }, 100, 100)).toEqual({
    x: 0,
    y: 0.2,
    width: 1,
    height: 0.51,
  })
  for (const rect of [
    null,
    { left: NaN, top: 0, right: 100, bottom: 100 },
    { left: 120, top: 0, right: 150, bottom: 100 },
  ]) {
    expect(normalizedRect(rect, 100, 100)).toBeNull()
  }
  expect(normalizedRect({ left: 0, top: 0, right: 100, bottom: 100 }, 0, 100)).toBeNull()
  expect(validRegions(Array(9).fill({ x: 0, y: 0, width: 1, height: 1 }))).toBe(false)
  expect(validRegions([{ x: 0, y: 0, width: 0, height: 1 }])).toBe(false)
})

it("keeps disjoint rectangles and overlapping controls in a nonzero polygon", () => {
  expect(regionsClip([])).toBe("inset(100%)")
  const clip = regionsClip([
    { x: 0.2, y: 0.1, width: 0.6, height: 0.5 },
    { x: 0.7, y: 0.5, width: 0.2, height: 0.3 },
  ])
  expect(clip).toMatch(/^polygon\(nonzero,/)
  const points = [...clip.matchAll(/([\d.]+)% ([\d.]+)%/g)].map((m) => [Number(m[1]), Number(m[2])])
  const inside = (x, y) => {
    let winding = 0
    for (let i = 0; i < points.length - 1; i++) {
      const [a, b] = [points[i], points[i + 1]]
      const side = (b[0] - a[0]) * (y - a[1]) - (x - a[0]) * (b[1] - a[1])
      if (a[1] <= y && b[1] > y && side > 0) winding++
      if (a[1] > y && b[1] <= y && side < 0) winding--
    }
    return winding !== 0
  }
  expect(inside(50, 30)).toBe(true)
  expect(inside(75, 55)).toBe(true)
  expect(inside(85, 75)).toBe(true)
  expect(inside(50, 75)).toBe(false)
  expect(inside(10, 50)).toBe(false)
})

it("throttles geometry checks, deduplicates messages, and releases resize listeners", () => {
  let now = 0
  const postMessage = vi.fn()
  const geometry = vi.fn(() => ({ left: 0, top: 0, width: 100, height: 100 }))
  const player = {
    bounds: { left: -1, right: 1, top: 1, bottom: -1 },
    zoom: 1,
    pan: { x: 0, y: 0 },
    canvas: { width: 100, height: 100, getBoundingClientRect: geometry },
    model: {
      getModel: () => ({ getDrawableCount: () => 0 }),
      workActions: { transitioning: false },
    },
  }
  const getPlayer = () => player
  history.replaceState(null, "", "/stage.html?channel=current")
  vi.spyOn(document, "referrer", "get").mockReturnValue("http://localhost/")
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false)
  vi.spyOn(performance, "now").mockImplementation(() => now)
  vi.stubGlobal("parent", { postMessage })
  const crop = connectCrop(getPlayer)
  try {
    expect(postMessage).toHaveBeenCalledTimes(1)
    now = 199
    crop.update()
    expect(geometry).toHaveBeenCalledTimes(1)
    now = 200
    crop.update()
    expect(geometry).toHaveBeenCalledTimes(2)
    expect(postMessage).toHaveBeenCalledTimes(1)
    hidden.mockReturnValue(true)
    now = 400
    crop.update()
    expect(geometry).toHaveBeenCalledTimes(2)
    hidden.mockReturnValue(false)
    window.dispatchEvent(new Event("resize"))
    expect(geometry).toHaveBeenCalledTimes(3)
    player.model.workActions.transitioning = true
    now = 416
    crop.update()
    expect(geometry).toHaveBeenCalledTimes(4)
    expect(postMessage).toHaveBeenCalledTimes(1)
    crop.dispose()
    window.dispatchEvent(new Event("resize"))
    expect(geometry).toHaveBeenCalledTimes(4)
  } finally {
    crop.dispose()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    history.replaceState(null, "", "/")
  }
})

it("places debug chrome around visible geometry without retaining the expanded motion envelope", () => {
  const rect = modelRectangle({
    bounds: { left: -1, right: 1, top: 1, bottom: -1 },
    zoom: 1,
    pan: { x: 0, y: 0 },
    canvas: {
      width: 200,
      height: 200,
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 200 }),
    },
    model: {
      getModel: () => ({
        getDrawableCount: () => 1,
        getDrawableOpacity: () => 1,
        getDrawableVertices: () => [-0.3, -0.4, 0.3, 0.4],
      }),
    },
  })
  expect(rect.top).toBeCloseTo(56)
  expect(rect.visible.top).toBeCloseTo(56)
  expect(rect.visible.bottom).toBeCloseTo(144)
})

it("keeps mesh scans throttled while speech and badge DOM updates stream", async () => {
  vi.useFakeTimers()
  history.replaceState(null, "", "/stage.html?channel=speech")
  vi.spyOn(document, "referrer", "get").mockReturnValue("http://localhost/")
  vi.spyOn(document, "hidden", "get").mockReturnValue(false)
  const parent = { postMessage: vi.fn() }
  vi.stubGlobal("parent", parent)
  document.body.innerHTML = '<span id="work-status"></span>'
  const scan = vi.fn(() => [-0.3, -0.4, 0.3, 0.4])
  const player = {
    bounds: { left: -1, right: 1, top: 1, bottom: -1 },
    zoom: 1,
    pan: { x: 0, y: 0 },
    canvas: {
      width: 100,
      height: 100,
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
    },
    model: {
      getModel: () => ({
        getDrawableCount: () => 1,
        getDrawableOpacity: () => 1,
        getDrawableVertices: scan,
      }),
    },
  }
  const { connectWork } = await import("../../../../examples/packages/live2d_pet/ui/work.js")
  const behavior = { setWork() {}, status: "running" }
  const disconnect = connectWork(behavior, { activity() {} }, "dragon", { followWork: false })
  const crop = connectCrop(() => player)
  try {
    for (let i = 1; i <= 50; i++) {
      window.dispatchEvent(
        new MessageEvent("message", {
          source: parent,
          origin: "http://localhost",
          data: {
            type: "kt-live2d-work",
            channel: "speech",
            sequence: i,
            work: {
              connected: true,
              status: "running",
              label: "Agent",
              speech: { text: String(i) },
            },
          },
        }),
      )
      document.querySelector("#work-status").dataset.test = String(i)
      await vi.advanceTimersByTimeAsync(10)
      crop.update()
    }
    expect(scan.mock.calls.length).toBeLessThanOrEqual(3)
  } finally {
    crop.dispose()
    disconnect()
    document.body.innerHTML = ""
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    history.replaceState(null, "", "/")
  }
})
