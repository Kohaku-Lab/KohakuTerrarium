import { expect, it } from "vitest"
import { PetBehavior } from "../../../../examples/packages/live2d_pet/ui/behavior.js"

const snapshot = (status, started = 1) => ({
  hostId: null,
  sessionId: "a",
  label: "Agent",
  status,
  started,
  completed: status === "ready" ? started : Math.max(0, started - 1),
  connected: true,
  busy: status === "running",
})

it("animates live work edges once and does not replay old results across reload, reconnect, or rebinding", () => {
  let now = 0
  const pet = new PetBehavior(() => now)
  pet.setWork(snapshot("ready"))
  expect(pet.reaction).toBeNull()
  pet.setWork(snapshot("running", 2))
  expect(pet.reaction.kind).toBe("start")
  expect(pet.working).toBe(true)
  now = 2000
  expect(pet.sample().ParamAngleY).toBeLessThan(-5)
  pet.setWork({ ...snapshot("waiting", 2), busy: false })
  expect(pet.working).toBe(true)
  now += 1000
  expect(pet.sample().ParamAngleZ).toBeGreaterThan(8)
  pet.setWork(snapshot("ready", 2))
  expect(pet.reaction.kind).toBe("ready")
  now += 1950
  expect(pet.sample().ParamMouthForm).toBeGreaterThan(0.7)
  now += 1000
  pet.sample()
  pet.setWork(snapshot("ready", 2))
  expect(pet.reaction).toBeNull()
  pet.setWork(snapshot("running", 3))
  pet.setWork({ ...snapshot("running", 3), connected: false })
  expect(pet.working).toBe(false)
  pet.setWork(snapshot("ready", 3))
  expect(pet.reaction).toBeNull()
  pet.setWork({ ...snapshot("running", 8), sessionId: "b" })
  expect(pet.reaction).toBeNull()
  pet.setWork(null)
  expect(pet.status).toBe("idle")
  expect(pet.reaction).toBeNull()
  pet.setWork(snapshot("running", 0))
  pet.setWork(snapshot("ready", 0))
  expect(pet.reaction).toBeNull()
  pet.setWork(snapshot("running", 10))
  pet.setWork(snapshot("ready", 1))
  expect(pet.reaction).toBeNull()
})

it("bounds temporary touch feedback, rate-limits repeated taps, and returns to the work pose", () => {
  let now = 0
  const pet = new PetBehavior(() => now)
  pet.setWork(snapshot("waiting"))
  expect(pet.touch(null)).toBe(false)
  expect(pet.touch("head")).toBe(true)
  expect(pet.touch("body")).toBe(false)
  now = 900
  expect(pet.sample().ParamEyeLOpen).toBeCloseTo(-0.65)
  now = 2000
  expect(pet.sample().ParamEyeLOpen).toBeUndefined()
  expect(pet.sample().ParamAngleZ).toBeCloseTo(9, 1)
  expect(pet.touch("body")).toBe(true)
  now += 900
  expect(pet.sample().ParamMouthOpenY).toBeCloseTo(0.25)
  now += 1000
  expect(pet.sample().ParamMouthOpenY).toBeUndefined()
})

it("keeps the completion gesture independent from touch and cancels it on new work", () => {
  let now = 0
  const pet = new PetBehavior(() => now)
  pet.setWork(snapshot("running"))
  pet.setWork(snapshot("ready"))
  now = 2100
  expect(pet.action().state).toBe("ready")
  pet.touch("head")
  expect(pet.action().state).toBe("ready")
  pet.setWork(snapshot("ready"))
  expect(pet.action().progress).toBeCloseTo(2100 / 3400)
  now = 3500
  expect(pet.action().state).toBe("idle")
  pet.setWork(snapshot("running", 2))
  pet.setWork(snapshot("ready", 2))
  pet.setWork(snapshot("waiting", 2))
  expect(pet.action().state).toBe("waiting")
  pet.setWork({ ...snapshot("ready", 2), sessionId: "other" })
  expect(pet.action().state).toBe("idle")
})

it("does not restart or cancel gestures on identical work snapshots", () => {
  let now = 0
  const pet = new PetBehavior(() => now)
  pet.setWork(snapshot("idle", 0))
  pet.setWork(snapshot("running", 1))
  now = 100
  pet.setWork(snapshot("running", 1))
  expect(pet.reaction).toMatchObject({ kind: "start", at: 0 })
  pet.touch("head")
  pet.setWork(snapshot("running", 1))
  expect(pet.reaction.kind).toBe("pat")
})

it("holds consecutive questions and finishes the exit before a fast result without flashing busy", () => {
  let now = 0
  const pet = new PetBehavior(() => now)
  const work = (status, completed = 0) => ({ ...snapshot(status), completed })
  pet.setWork(work("waiting"))
  now = 1000
  pet.setWork(work("running"))
  now = 1100
  expect(pet.action().state).toBe("waiting")
  pet.setWork(work("waiting"))
  now = 2000
  expect(pet.action().state).toBe("waiting")
  expect(pet.releaseAt).toBeNull()
  pet.setWork(work("running"))
  now = 2050
  pet.setWork(work("ready", 1))
  now = 2250
  expect(pet.action()).toEqual({ state: "resolving", progress: 0 })
  now = 2650
  expect(pet.action()).toEqual({ state: "resolving", progress: 0.5 })
  expect(pet.sample().ParamMouthForm || 0).toBe(0)
  pet.setWork(work("ready", 1))
  now = 3050
  expect(pet.action()).toEqual({ state: "ready", progress: 0 })
  now = 6450
  expect(pet.action().state).toBe("idle")
})

it("completes entry before exit and does not celebrate a resolved question or replay on reconnect", () => {
  let now = 0
  const pet = new PetBehavior(() => now, "deepseek")
  const work = (status, completed = 1) => ({ ...snapshot(status), completed })
  pet.setWork(work("waiting"))
  now = 50
  pet.setWork(work("ready"))
  now = 600
  expect(pet.action().state).toBe("waiting")
  now = 950
  expect(pet.action()).toEqual({ state: "resolving", progress: 0.5 })
  now = 1250
  expect(pet.action().state).toBe("idle")
  expect(pet.completedAt).toBeNull()
  pet.setWork(work("waiting"))
  now = 2000
  pet.setWork(work("running"))
  now = 2400
  expect(pet.action().state).toBe("resolving")
  pet.setWork(work("waiting"))
  expect(pet.action().state).toBe("waiting")
  expect(pet.releaseAt).toBeNull()
  pet.setWork(work("ready", 2))
  pet.setWork({ ...work("ready", 2), connected: false })
  expect(pet.action().state).toBe("idle")
  pet.setWork(work("ready", 2))
  expect(pet.action().state).toBe("idle")
  expect(pet.completedAt).toBeNull()
})

it("drops queued results when newer work, another binding, or a counter reset takes over", () => {
  let now = 0
  const pet = new PetBehavior(() => now)
  const work = (status, completed = 0) => ({ ...snapshot(status), completed })
  pet.setWork(work("waiting"))
  now = 1000
  pet.setWork(work("ready", 1))
  now = 1100
  pet.setWork(work("running", 1))
  now = 1500
  expect(pet.action().state).toBe("resolving")
  now = 2100
  expect(pet.action().state).toBe("running")
  expect(pet.completedAt).toBeNull()
  pet.setWork(work("waiting", 1))
  now = 3000
  pet.setWork(work("ready", 2))
  pet.setWork({ ...work("ready", 2), sessionId: "other" })
  expect(pet.action().state).toBe("idle")
  pet.setWork(work("waiting", 5))
  now = 4000
  pet.setWork(work("ready", 0))
  expect(pet.action().state).toBe("idle")
  expect(pet.releaseAt).toBeNull()
})
