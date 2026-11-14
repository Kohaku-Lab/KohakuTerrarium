import { expect, it } from "vitest"
import { FrameStats } from "../../../../examples/packages/live2d_pet/src/performance.js"
import {
  fittedSize,
  normalizePreferences,
} from "../../../../examples/packages/live2d_pet/ui/preferences.js"
import { bindGestures } from "../../../../examples/packages/live2d_pet/ui/gestures.js"
it("bounds diagnostic history and counts real long frame gaps", () => {
  const stats = new FrameStats()
  for (let i = 0; i < 1200; i++) stats.add((i * 1000) / 60, 2)
  expect(stats.summary().fps).toBeCloseTo(60)
  expect(stats.samples.length).toBeLessThanOrEqual(601)
  stats.add(20500, 20)
  expect(stats.summary().stalls).toBe(1)
  expect(stats.summary().cpuP95).toBe(2)
  stats.reset()
  expect(stats.summary().frames).toBe(0)
})
it("supports 50–300 percent, compacts the envelope, and reserves only debug chrome", () => {
  const p = normalizePreferences({ version: 1, models: { dragon: { scale: 3 } } })
  expect(p.quality).toBe("high")
  const slim = normalizePreferences({ version: 1, model: "deepseek" })
  expect(fittedSize(slim, 1280, 800, 0.4)).toEqual([206, 516])
  expect(fittedSize(p, 4000, 4000, 1.4)).toEqual([1500, 1071])
  expect(fittedSize({ ...p, debug: true }, 4000, 4000, 1.4)).toEqual([1500, 1151])
  expect(fittedSize(p, 400, 300, 1.4)[1]).toBeLessThanOrEqual(284)
  expect(
    normalizePreferences({ version: 1, quality: "standard", models: { dragon: { scale: 0.1 } } }),
  ).toMatchObject({ quality: "standard", models: { dragon: { scale: 0.5 } } })
})
it("moves a companion through screen deltas without treating a drag as a tap", () => {
  const canvas = document.createElement("canvas")
  canvas.setPointerCapture = () => {}
  canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 300, height: 300 })
  const moves = [],
    touches = []
  const dispose = bindGestures(
    canvas,
    {
      dead: false,
      paused: false,
      pan: { x: 0, y: 0 },
      hitRegion: () => "head",
      model: { currentMotion: "Idle" },
    },
    { activity: () => false },
    { touch: (x) => touches.push(x) },
    { mini: true, closeMenu: () => {}, isMenuOpen: () => false, movePet: (x) => moves.push(x) },
  )
  const send = (name, x, y) =>
    canvas.dispatchEvent(
      new MouseEvent(name, { button: 0, clientX: x, clientY: y, screenX: x, screenY: y }),
    )
  send("pointerdown", 10, 10)
  send("pointermove", 30, 40)
  send("pointerup", 30, 40)
  expect(moves.map((x) => x.phase)).toEqual(["start", "move", "end"])
  expect(moves[1]).toMatchObject({ dx: 20, dy: 30 })
  expect(touches).toEqual([])
  dispose()
  send("pointerdown", 10, 10)
  expect(moves).toHaveLength(3)
})
