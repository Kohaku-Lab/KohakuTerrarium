import { expect, it } from "vitest"

import { PetGaze } from "../../../../examples/packages/virtual_pet/ui/gaze.js"

const point = (angle = 0, distance = 90) => ({
  valid: true,
  pointerType: "mouse",
  dx: Math.sin((angle * Math.PI) / 180) * distance,
  dy: -Math.cos((angle * Math.PI) / 180) * distance,
  size: 100,
})

it("selects sixteen clockwise poses rather than cycling with time", () => {
  for (let i = 0; i < 16; i++) {
    const gaze = new PetGaze()
    expect(gaze.tick(0, point(i * 22.5))).toBeNull()
    expect(gaze.tick(79, point(i * 22.5))).toBeNull()
    const cell = { row: 9 + Math.floor(i / 8), column: i % 8 }
    expect(gaze.tick(80, point(i * 22.5))).toEqual(cell)
    expect(gaze.tick(9000, point(i * 22.5))).toEqual(cell)
  }
})

it("uses cancellable entry/exit dwell and separate enter/exit radii", () => {
  const gaze = new PetGaze()
  gaze.tick(0, point())
  gaze.tick(40, point(0, 101))
  gaze.tick(80, point())
  expect(gaze.tick(159, point())).toBeNull()
  expect(gaze.tick(160, point())).toEqual({ row: 9, column: 0 })
  expect(gaze.tick(200, point(0, 130))).toEqual({ row: 9, column: 0 })
  gaze.tick(250, point(0, 141))
  expect(gaze.tick(369, point(0, 141))).not.toBeNull()
  gaze.tick(370, point(0, 130))
  gaze.tick(400, point(0, 141))
  expect(gaze.tick(519, point(0, 141))).not.toBeNull()
  expect(gaze.tick(520, point(0, 141))).toBeNull()
  expect(gaze.tick(600, point())).toBeNull()
  expect(gaze.tick(680, point())).toEqual({ row: 9, column: 0 })
})

it("stabilizes angle boundaries including wraparound and the center dead zone", () => {
  const gaze = new PetGaze()
  gaze.tick(0, point())
  gaze.tick(80, point())
  expect(gaze.tick(90, point(13))).toEqual({ row: 9, column: 0 })
  expect(gaze.tick(100, point(15))).toEqual({ row: 9, column: 1 })
  expect(gaze.tick(110, point(10))).toEqual({ row: 9, column: 1 })
  expect(gaze.tick(120, point(7))).toEqual({ row: 9, column: 0 })
  expect(gaze.tick(130, point(347))).toEqual({ row: 9, column: 0 })
  expect(gaze.tick(140, point(345))).toEqual({ row: 10, column: 7 })
  expect(gaze.tick(150, point(270, 0))).toEqual({ row: 10, column: 7 })
  gaze.reset()
  gaze.tick(200, point(0, 0))
  expect(gaze.tick(300, point(0, 0))).toBeNull()
  expect(gaze.tick(310, point(90))).toEqual({ row: 9, column: 4 })
})

it("invalidates touch, lost input, invalid geometry and resized proximity", () => {
  const gaze = new PetGaze()
  for (const invalid of [
    null,
    { ...point(), valid: false },
    { ...point(), pointerType: "touch" },
    { ...point(), dx: NaN },
    { ...point(), size: 0 },
  ]) {
    gaze.tick(0, point())
    expect(gaze.tick(80, point())).not.toBeNull()
    expect(gaze.tick(90, invalid)).toBeNull()
    expect(gaze.tick(100, point())).toBeNull()
    gaze.reset()
  }
  gaze.tick(0, point())
  gaze.tick(80, point())
  gaze.tick(90, { ...point(), size: 50 })
  expect(gaze.tick(210, { ...point(), size: 50 })).toBeNull()
})
