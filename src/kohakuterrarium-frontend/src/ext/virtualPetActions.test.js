import { expect, it } from "vitest"

import { PetPlayer, cellFor } from "../../../../examples/packages/virtual_pet/ui/player.js"

const atlas = {
  frames: { idle: 6, running: 6 },
  interactions: { "running-right": 8, "running-left": 8, waving: 4, jumping: 5 },
}
const work = (started, status = "running") => ({
  hostId: null,
  sessionId: "a",
  started,
  status,
  busy: status === "running",
})

it("maps optional rows and three-cycle deadlines without queues, restarts or lost work facts", () => {
  for (const [action, row, period] of [
    ["waving", 3, 700],
    ["jumping", 4, 840],
    ["running-right", 1, 1060],
    ["running-left", 2, 1060],
  ]) {
    expect(cellFor(atlas, action, 0)).toEqual({ row, column: 0 })
    const player = new PetPlayer(atlas)
    expect(player.cue(action, 100)).toBe(true)
    expect(player.tick(100).action).toBe(action)
    expect(player.tick(100 + period * 3 - 0.01).action).toBe(action)
    expect(player.tick(100 + period * 3).action).toBe("idle")
  }
  const p = new PetPlayer(atlas)
  p.setWork(work(0), 0)
  p.setWork(work(1), 10)
  expect(p.tick(10).action).toBe("running")
  p.cue("jumping", 20)
  expect(p.tick(20).action).toBe("jumping")
  expect(p.cue("jumping", 200)).toBe(false)
  p.cue("waving", 300)
  expect(p.tick(300).action).toBe("waving")
  expect(p.cue("jumping", 400)).toBe(false)
  p.setWork(work(2), 500)
  expect(p.tick(2400).action).toBe("idle")
  p.press(2500)
  expect(p.tick(2500).action).toBe("idle")
  p.beginDrag(2600)
  p.direction("running-right", 2600)
  expect(p.tick(2600).action).toBe("running-right")
  p.direction("running-left", 3000)
  expect(p.tick(3000)).toMatchObject({ action: "running-left", column: 0 })
  expect(p.cue("waving", 3200)).toBe(false)
  expect(p.tick(5780).action).toBe("running-left")
  expect(p.tick(6180).action).toBe("idle")
  p.direction("running-left", 6200)
  expect(p.tick(6200).action).toBe("idle")
  p.direction("running-right", 6300)
  expect(p.tick(6300)).toMatchObject({ action: "running-right", column: 0 })
  p.release(6400, false)
  expect(p.tick(6400).action).toBe("idle")
  p.press(6500)
  p.release(6501, true)
  expect(p.tick(6501).action).toBe("waving")
  p.press(6600)
  p.release(6601, true)
  expect(p.tick(8601).action).toBe("idle")
})

it("degrades optional rows, consumes hidden/still cues, and keeps session/context changes from replaying gestures", () => {
  const p = new PetPlayer({ frames: { idle: 6, running: 6 } })
  expect(p.cue("waving", 0)).toBe(false)
  expect(p.tick(0).action).toBe("idle")
  const short = new PetPlayer({ ...atlas, interactions: { waving: 1, jumping: 2 } })
  short.cue("waving", 0)
  expect(short.tick(419).action).toBe("waving")
  expect(short.tick(420).action).toBe("idle")
  short.cue("jumping", 500)
  expect(short.tick(600, true).action).toBe("idle")
  expect(short.tick(700).action).toBe("idle")
  short.cue("jumping", 800)
  short.cancel()
  expect(short.tick(900).action).toBe("idle")
  short.cue("jumping", 1000)
  short.setWork(work(4), 1100)
  expect(short.tick(1100).action).toBe("jumping")
  short.setWork({ ...work(0), sessionId: "b" }, 1200)
  expect(short.tick(1200).action).toBe("jumping")
  expect(short.tick(1840).action).toBe("idle")
  const directions = new PetPlayer({
    ...atlas,
    interactions: { "running-right": 1, "running-left": 8 },
  })
  directions.press(0)
  directions.beginDrag(0)
  directions.direction("running-right", 0)
  directions.direction("running-left", 100)
  expect(directions.tick(100)).toMatchObject({ action: "running-left", column: 0 })
  expect(directions.tick(360).action).toBe("running-left")
  directions.direction("running-left", 500)
  expect(directions.tick(3279).action).toBe("running-left")
  expect(directions.tick(3280).action).toBe("idle")
  directions.direction("running-left", 4000)
  expect(directions.tick(4000).action).toBe("idle")
  directions.direction("running-right", 4100)
  expect(directions.tick(4100)).toMatchObject({ action: "running-right", column: 0 })
  directions.direction("running-right", 4200)
  expect(directions.tick(4459).action).toBe("running-right")
  expect(directions.tick(4460).action).toBe("idle")
  const missing = new PetPlayer({ ...atlas, interactions: { "running-right": 8 } })
  missing.press(0)
  missing.beginDrag(0)
  missing.direction("running-right", 0)
  missing.direction("running-left", 100)
  expect(missing.tick(100).action).toBe("idle")
  missing.direction("running-right", 200)
  expect(missing.tick(200)).toMatchObject({ action: "running-right", column: 0 })
  missing.release(201, false)
  missing.direction("running-left", 202)
  expect(missing.tick(202).action).toBe("idle")
  const still = new PetPlayer(atlas)
  still.setWork(work(0), 0)
  still.press(0)
  still.beginDrag(0)
  still.direction("running-right", 0)
  expect(still.tick(100, true).action).toBe("idle")
  expect(still.cue("waving", 200)).toBe(false)
  still.setWork(work(1), 200)
  still.direction("running-left", 200)
  expect(still.tick(200).action).toBe("idle")
  still.release(300, false)
  expect(still.tick(300).action).toBe("idle")
})
