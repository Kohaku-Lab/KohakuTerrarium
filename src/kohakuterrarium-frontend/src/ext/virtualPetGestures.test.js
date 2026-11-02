import { afterEach, expect, it, vi } from "vitest"
import { PetGestures } from "../../../../examples/packages/virtual_pet/ui/gestures.js"
import { PetPlayer } from "../../../../examples/packages/virtual_pet/ui/player.js"

let gesture
const event = (
  type,
  x = 0,
  y = 0,
  id = 1,
  buttons = type === "pointermove" || type === "pointerdown" ? 1 : 0,
) => {
  const e = new MouseEvent(type, { cancelable: true, button: 0, buttons, clientX: x, clientY: y })
  Object.defineProperties(e, {
    pointerId: { value: id },
    isPrimary: { value: id === 1 },
    pointerType: { value: "touch" },
  })
  return e
}
afterEach(() => {
  gesture?.dispose()
  vi.restoreAllMocks()
})
it("owns one pointer through cancel/blur/dispose without leaving an independent movement loop", () => {
  const player = new PetPlayer({
    frames: { idle: 6, running: 6 },
    interactions: { "running-right": 8, waving: 4 },
  })
  const positions = []
  gesture = new PetGestures(
    () => player,
    () => {},
    () => {},
    (point) => positions.push(point),
  )
  gesture.down(event("pointerdown"))
  window.dispatchEvent(event("pointermove", 10, 0))
  expect(positions).toEqual([{ x: 10, y: 0 }])
  window.dispatchEvent(event("pointermove", 100, 100, 2))
  window.dispatchEvent(event("pointerup", 100, 100, 2))
  window.dispatchEvent(event("pointercancel", 100, 100, 2))
  window.dispatchEvent(event("pointermove", 20, 0))
  expect(positions.at(-1)).toEqual({ x: 20, y: 0 })
  expect(positions).toHaveLength(2)
  window.dispatchEvent(new Event("blur"))
  window.dispatchEvent(event("pointermove", 50, 0))
  expect(positions).toHaveLength(2)
  gesture.down(event("pointerdown"))
  window.dispatchEvent(event("pointermove", 10, 0))
  expect(positions).toHaveLength(3)
  gesture.dispose()
  window.dispatchEvent(event("pointermove", 50, 0))
  expect(positions).toHaveLength(3)
  gesture.down(event("pointerdown"))
  window.dispatchEvent(event("pointermove", 10, 0))
  window.dispatchEvent(event("pointercancel"))
  window.dispatchEvent(event("pointermove", 20, 0))
  expect(positions).toHaveLength(4)
  gesture.down(event("pointerdown"))
  window.dispatchEvent(event("pointermove", 0, 10))
  window.dispatchEvent(event("pointerup", 0, 10))
  expect(player.tick(performance.now()).action).toBe("idle")
  gesture.down(event("pointerdown"))
  window.dispatchEvent(event("pointermove", 10, 0))
  const beforeLostUp = positions.length
  window.dispatchEvent(event("pointermove", 20, 0, 1, 0))
  window.dispatchEvent(event("pointermove", 30, 0))
  expect(positions).toHaveLength(beforeLostUp)
})

it("confirms directional segments from extrema, independent of intermediate samples and clamped host movement", () => {
  let now = 0
  vi.spyOn(performance, "now").mockImplementation(() => now)
  const trace = (samples) => {
    gesture?.dispose()
    const player = new PetPlayer({
      frames: { idle: 6, running: 6 },
      interactions: { "running-right": 8, "running-left": 8 },
    })
    const transitions = []
    let last = null
    gesture = new PetGestures(
      () => player,
      () => {
        const cue = player.interaction
        if (cue && cue !== last) transitions.push([cue.action, cue.start])
        last = cue
      },
      () => {},
      () => {},
    )
    now = 0
    gesture.down(event("pointerdown"))
    for (const [time, x, y = 0] of samples) {
      now = time
      window.dispatchEvent(event("pointermove", x, y))
    }
    return { transitions, player }
  }
  const sparse = trace([
    [10, 10],
    [20, 24],
    [30, 18],
    [40, 10],
    [50, 16],
  ])
  expect(sparse.transitions).toEqual([
    ["running-right", 10],
    ["running-left", 30],
    ["running-right", 50],
  ])
  const dense = trace([
    [10, 10],
    [12, 15],
    [14, 20],
    [20, 24],
    [22, 23],
    [24, 21],
    [26, 19],
    [30, 18],
    [32, 16],
    [35, 13],
    [40, 10],
    [42, 11],
    [44, 13],
    [46, 15],
    [50, 16],
  ])
  expect(dense.transitions).toEqual(sparse.transitions)
  expect(dense.player.tick(3230).action).toBe("idle")
  now = 4000
  window.dispatchEvent(event("pointermove", 30))
  expect(dense.player.tick(now).action).toBe("idle")
  now = 4100
  window.dispatchEvent(event("pointermove", 29))
  now = 4200
  window.dispatchEvent(event("pointermove", 27))
  now = 4300
  window.dispatchEvent(event("pointermove", 24))
  expect(dense.player.tick(now)).toMatchObject({ action: "running-left", column: 0 })
  expect(dense.transitions.at(-1)).toEqual(["running-left", 4300])
  window.dispatchEvent(event("pointerup", 24))
  expect(dense.player.tick(now).action).toBe("idle")
  const vertical = trace([
    [10, 0, 10],
    [20, 5, 30],
    [30, 6, 40],
  ])
  expect(vertical.transitions).toEqual([["running-right", 30]])
  expect(vertical.player.tick(30)).toMatchObject({ action: "running-right", column: 0 })
  expect(vertical.player.tick(3209).action).toBe("running-right")
  expect(vertical.player.tick(3210).action).toBe("idle")
})
