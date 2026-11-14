import { expect, it } from "vitest"
import { PetBehavior } from "../../../../examples/packages/live2d_pet/ui/behavior.js"
import { AgentActions } from "../../../../examples/packages/live2d_pet/ui/agent-actions.js"
import sets from "../../../../examples/packages/live2d_pet/ui/action-sets.json"
import { CropHistory } from "../../../../examples/packages/live2d_pet/ui/crop.js"
import { clampBox } from "./overlays"
import { validateActionSet } from "../../../../examples/packages/live2d_pet/ui/action-catalog.js"

it.each(["dragon", "deepseek"])(
  "keeps %s automatic until enabled and restores after a held pose",
  (model) => {
    let now = 0
    const automatic = new PetBehavior(() => now, model)
    const base = {
      connected: true,
      hostId: "host",
      sessionId: "session",
      started: 0,
      completed: 0,
      status: "running",
    }
    automatic.setWork(base)
    const actor = new AgentActions(automatic, model, sets.sets[model], () => now)
    const command = (id, action = "listen") => ({
      operation: "perform",
      id,
      model,
      action,
      expiresAt: Date.now() + 4000,
    })
    expect(actor.receive(command("disabled"))).toBe(false)
    actor.configure(true)
    expect(actor.receive(command("listen"))).toBe(true)
    expect(actor.action(now).state).toBe("waiting")
    expect(actor.receive(command("listen"))).toBe(false)
    now = 1000
    actor.configure(false)
    now = 1300
    expect(actor.action(now).state).toBe("resolving")
    now = 3000
    expect(actor.action(now).state).toBe("running")
    actor.configure(true)
    expect(actor.receive(command("again"))).toBe(true)
    automatic.setWork({ ...base, status: "waiting" })
    expect(actor.current(now)).toBe(automatic)
    expect(actor.receive(command("blocked", "celebrate"))).toBe(false)
    expect(actor.receive({ ...command("stale"), expiresAt: Date.now() - 1 })).toBe(false)
    expect(actor.receive({ ...command("wrong"), model: "other" })).toBe(false)
    automatic.setWork({ ...base, status: "running" })
    expect(actor.receive(command("prototype", "constructor"))).toBe(false)
    expect(actor.receive(command("manual"))).toBe(true)
    actor.touch("head")
    expect(actor.active).toBe(false)
    expect(actor.current(now)).toBe(automatic)
  },
)

it("expands immediately and releases old crop space after the short hold", () => {
  const history = new CropHistory()
  const head = { left: 40, right: 60, top: 20, bottom: 40 }
  const wings = { left: 10, right: 90, top: 40, bottom: 80 }
  const wide = { left: 10, right: 90, top: 20, bottom: 80, regions: [head, wings] }
  const narrow = {
    ...wide,
    left: 30,
    right: 70,
    regions: [head, { ...wings, left: 30, right: 70 }],
  }
  expect(history.update(wide, 0)).toEqual(wide)
  expect(history.update(narrow, 100)).toEqual(wide)
  expect(history.update(narrow, 200)).toEqual(narrow)
  expect(history.update(wide, 220)).toEqual(wide)
  const reordered = history.update({ ...wide, regions: [wings, head] }, 225)
  expect(
    reordered.regions.some((r) => 15 >= r.left && 15 <= r.right && 25 >= r.top && 25 <= r.bottom),
  ).toBe(false)
  expect(history.update(null, 230)).toBeNull()
})

it("uses registered gestures for automatic and explicit playback without changing work state", () => {
  let now = 0
  const automatic = new PetBehavior(() => now)
  const catalog = structuredClone(sets.sets.dragon)
  catalog.defaults.running = "nod"
  const actor = new AgentActions(automatic, "dragon", catalog, () => now)
  const work = {
    connected: true,
    hostId: "host",
    sessionId: "one",
    status: "running",
    started: 1,
    completed: 0,
  }
  automatic.setWork(work)
  expect(actor.action(now).playback).toEqual(catalog.actions.nod.playback)
  expect(automatic.status).toBe("running")
  now = 1000
  expect(actor.sample(now)).toEqual({})
  actor.configure(true)
  expect(
    actor.receive({
      operation: "perform",
      id: "pose",
      model: "dragon",
      action: "nod",
      expiresAt: Date.now() + 2000,
    }),
  ).toBe(true)
  expect(actor.action(now).playback).toEqual({ type: "motion", name: "Nod" })
  expect(automatic.work).toEqual(work)
  now = 3000
  expect(actor.current(now)).toBe(automatic)
  automatic.setWork({ ...work, status: "waiting" })
  expect(actor.action(now).state).toBe("waiting")
  catalog.defaults.waiting = "concern"
  actor.setCatalog(catalog)
  now = 5000
  expect(actor.sample(now).ParamMouthForm).toBeCloseTo(-0.4, 2)
  expect(actor.sample(now).ParamAngleZ || 0).toBeCloseTo(0, 2)
  actor.touch("head")
  now += 900
  expect(actor.sample(now).ParamEyeLSmile).toBeCloseTo(0.8, 2)
})

it("rejects malformed action sets and keeps imported catalogs detached", () => {
  const original = sets.sets.dragon
  const parsed = validateActionSet(original, "dragon")
  parsed.actions.nod.description = "Changed"
  expect(original.actions.nod.description).not.toBe("Changed")
  for (const patch of [
    { version: true },
    { model: "other" },
    { extra: 1 },
    { actions: {} },
    { defaults: { running: "missing" } },
  ])
    expect(() => validateActionSet({ ...original, ...patch }, "dragon")).toThrow()
  for (const playback of [
    { type: "pose", parameters: { ParamAngleX: NaN } },
    { type: "pose", parameters: { ParamAngleX: true } },
    { type: "pose", parameters: { ParamAngleX: 1001 } },
    { type: "motion", name: "Idle", url: "https://example.invalid" },
    { type: "preset", name: "missing" },
    { type: "pose", parameters: {} },
    { type: [], name: "Idle" },
  ]) {
    const broken = structuredClone(original)
    broken.actions.nod.playback = playback
    expect(() => validateActionSet(broken, "dragon")).toThrow()
  }
})

it("allows transparent canvas margins offscreen while keeping the visible model reachable", () => {
  const viewport = { width: 1000, height: 800 }
  const box = { width: 400, height: 600, left: -200, top: 400 }
  expect(clampBox(box, viewport, [[100, 50, 200, 500]])).toMatchObject({ left: -100, top: 250 })
  expect(clampBox({ ...box, left: 900 }, viewport, [[100, 50, 200, 500]])).toMatchObject({
    left: 700,
  })
  expect(clampBox(box, viewport)).toMatchObject({ left: 0, top: 200 })
})
