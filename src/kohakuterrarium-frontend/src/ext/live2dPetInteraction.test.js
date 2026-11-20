// @vitest-environment node
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"
import vm from "node:vm"
import { JSDOM } from "jsdom"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { connectAgentActions } from "../../../../examples/packages/live2d_pet/ui/agent-actions.js"
import { PetBehavior } from "../../../../examples/packages/live2d_pet/ui/behavior.js"
import { connectWork } from "../../../../examples/packages/live2d_pet/ui/work.js"
import sets from "../../../../examples/packages/live2d_pet/ui/action-sets.json"
import {
  PetInteraction,
  readSelection,
  movedBeyondClick,
  STATE_KEY,
  SLEEP_AFTER_MS,
} from "../../../../examples/packages/live2d_pet/ui/interaction.js"

const document = new JSDOM("", { url: "http://localhost" })
const sessionStorage = document.window.sessionStorage
afterAll(() => document.window.close())

it("ignores malformed saved state and filters unknown expressions", () => {
  sessionStorage.setItem(STATE_KEY, "broken")
  expect(readSelection(sessionStorage)).toBeNull()
  sessionStorage.setItem(STATE_KEY, JSON.stringify({ version: 2, motion: "Sleep" }))
  expect(readSelection(sessionStorage)).toBeNull()
  sessionStorage.setItem(
    STATE_KEY,
    JSON.stringify({ version: 1, motion: "invalid", expressions: ["抬手", "抬手", "missing"] }),
  )
  expect(readSelection(sessionStorage)).toEqual({ motion: "Idle", expressions: ["抬手"] })
  sessionStorage.clear()
})

it("separates click jitter from a drag in either direction", () => {
  expect(movedBeyondClick({ x: 20, y: 20 }, { x: 22, y: 21 })).toBe(false)
  expect(movedBeyondClick({ x: 20, y: 20 }, { x: 13, y: 20 })).toBe(true)
  expect(movedBeyondClick({ x: 20, y: 20 }, { x: 25, y: 25 })).toBe(true)
})

const root = resolve("../../examples/packages/live2d_pet")
const dragonRoot = process.env.KT_LIVE2D_DRAGON_DIR || "/nonexistent-private-dragon"
const ready =
  ["ui/runtime/live2dcubismcore.min.js", "ui/runtime/player.js"].every((path) =>
    existsSync(`${root}/${path}`),
  ) && existsSync(`${dragonRoot}/kohaku.moc3`)
const bytes = (file) => {
  const data = readFileSync(file)
  return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)
}

describe.skipIf(!ready)("interaction with the original Cubism model", () => {
  let DragonModel, DragonPlayer, model, now
  beforeAll(async () => {
    const context = {
      __dirname: `${root}/ui/runtime`,
      require: createRequire(import.meta.url),
      process,
      console,
      setTimeout,
      clearTimeout,
      Buffer,
      performance,
    }
    vm.runInNewContext(readFileSync(`${root}/ui/runtime/live2dcubismcore.min.js`, "utf8"), context)
    globalThis.Live2DCubismCore = context.Live2DCubismCore
    const runtime = await import(
      /* @vite-ignore */ pathToFileURL(`${root}/ui/runtime/player.js`).href
    )
    runtime.initializeFramework()
    DragonModel = runtime.DragonModel
    DragonPlayer = runtime.DragonPlayer
  })
  beforeEach(() => {
    sessionStorage.clear()
    now = 0
    model = new DragonModel()
    model.loadModel(bytes(`${dragonRoot}/kohaku.moc3`), true)
    for (const [name, file] of [
      ["Idle", "idle"],
      ["Sleep", "sleep2"],
    ]) {
      const data = bytes(`${dragonRoot}/${file}.motion3.json`)
      const motion = model.loadMotion(data, data.byteLength, name)
      motion.setLoop(true)
      model.motions.set(name, motion)
    }
    for (const name of ["抬手", "抬手2", "开扇", "扇面", "变"]) {
      const data = bytes(`${dragonRoot}/${name}.exp3.json`)
      model.expressions.set(name, model.loadExpression(data, data.byteLength, name))
    }
  })
  afterEach(() => {
    model.release()
    sessionStorage.clear()
  })
  afterAll(() => {
    delete globalThis.Live2DCubismCore
  })
  const controller = (mini) =>
    new PetInteraction(model, { storage: sessionStorage, mini, now: () => now })

  it("cycles hand poses without removing body choices and preserves them across surfaces", () => {
    const preview = controller(false)
    preview.expression("变")
    preview.expression("抬手")
    preview.expression("扇面")
    const mini = controller(true)
    expect([...model.activeExpressions]).toEqual(["变", "抬手", "扇面"])
    mini.cycle()
    expect([...model.activeExpressions]).toEqual(["变", "扇面", "抬手2"])
    const returned = controller(false)
    expect(model.activeExpressions.has("抬手2")).toBe(true)
    returned.cycle()
    expect([...model.activeExpressions]).toEqual(["变"])
    returned.cycle()
    expect([...model.activeExpressions]).toEqual(["变", "抬手"])
  })

  it("auto-sleeps after 60 idle seconds and wakes for accepted gestures while preserving manual choices", () => {
    const mini = controller(true)
    mini.expression("抬手2")
    now = SLEEP_AFTER_MS - 1
    mini.tick()
    expect(model.currentMotion).toBe("Idle")
    now++
    mini.tick()
    expect(model.currentMotion).toBe("Sleep")
    expect([...model.activeExpressions]).toEqual([])
    expect(readSelection(sessionStorage).motion).toBe("Idle")
    mini.cycle()
    expect(model.currentMotion).toBe("Idle")
    expect([...model.activeExpressions]).toEqual(["抬手2"])
    now += SLEEP_AFTER_MS
    mini.tick()
    controller(false)
    expect(model.currentMotion).toBe("Idle")
    expect([...model.activeExpressions]).toEqual(["抬手2"])

    const page = new JSDOM('<div id="work-status"></div>', {
      url: "http://localhost/stage.html?channel=current",
      referrer: "http://localhost/",
    })
    for (const [name, value] of Object.entries({
      window: page.window,
      document: page.window.document,
      location: page.window.location,
      parent: page.window,
    }))
      vi.stubGlobal(name, value)
    const interaction = controller(true)
    const player = new DragonPlayer(null, () => {})
    player.model = model
    model.initializeEffects()
    const behavior = new PetBehavior(() => now)
    const disconnect = connectWork(behavior, interaction, "dragon", {
      followWork: false,
      talkingMouth: false,
    })
    const actions = connectAgentActions(player, behavior, interaction, sets.sets.dragon, "dragon")
    let sequence = 0
    const send = (command = {}, envelope = {}) =>
      page.window.dispatchEvent(
        new page.window.MessageEvent("message", {
          origin: "http://localhost",
          source: page.window,
          data: {
            type: "kt-live2d-action",
            channel: "current",
            command: {
              id: `gesture-${++sequence}`,
              operation: "perform",
              model: "dragon",
              action: "nod",
              expiresAt: Date.now() + 4000,
              ...command,
            },
          },
          ...envelope,
        }),
      )
    const asleep = () => {
      expect(model.currentMotion).toBe("Sleep")
      expect(interaction.autoSleeping).toBe(true)
      expect(actions.active).toBe(false)
    }
    try {
      now += SLEEP_AFTER_MS
      interaction.tick()
      asleep()
      send()
      asleep()
      actions.configure(true)
      for (const command of [
        { operation: "release" },
        { action: "missing" },
        { model: "deepseek" },
        { expiresAt: Date.now() - 1 },
      ]) {
        send(command)
        asleep()
      }
      send({}, { origin: "https://other.test" })
      send({}, { source: null })
      send({}, { data: { type: "kt-live2d-action", channel: "old" } })
      asleep()
      behavior.waitingForInput = true
      send()
      asleep()
      behavior.waitingForInput = false
      player.paused = true
      send()
      asleep()
      player.paused = false
      send()
      expect(model.currentMotion).toBe("Idle")
      expect(interaction.autoSleeping).toBe(false)
      expect(actions.active).toBe(true)
      expect([...model.activeExpressions]).toEqual(["抬手2"])
      expect(readSelection(sessionStorage)).toEqual({ motion: "Idle", expressions: ["抬手2"] })
      const core = model.getModel()
      const ids = core.getModel().parameters.ids
      const head = ids.indexOf("ParamAngleY1")
      const mouth = ids.indexOf("ParamMouthOpenY")
      const angles = []
      for (let frame = 0; frame < 120; frame++) {
        const time = actions.startedAt + (frame * 1000) / 60
        model.update(1 / 60, { x: 0, y: 0 }, false, {}, actions.action(time), 0.6)
        angles.push(core.getParameterValueByIndex(head))
        expect(core.getParameterValueByIndex(mouth)).toBeCloseTo(0.6)
      }
      expect(Math.min(...angles)).toBeLessThan(-8)
      expect(angles.at(-1)).toBeGreaterThan(-2)
      send({ operation: "release" })
      actions.current(performance.now())
      expect(actions.active).toBe(false)
      interaction.motion("Sleep")
      send()
      expect(model.currentMotion).toBe("Sleep")
      expect(interaction.autoSleeping).toBe(false)
      expect(actions.active).toBe(false)
      expect(readSelection(sessionStorage).motion).toBe("Sleep")
    } finally {
      actions.dispose()
      disconnect()
      vi.unstubAllGlobals()
      page.window.close()
    }
  })

  it("keeps manual sleep across surfaces and wakes on click rather than pointer movement", () => {
    const mini = controller(true)
    mini.motion("Sleep")
    now = 100_000
    mini.activity()
    expect(model.currentMotion).toBe("Sleep")
    const preview = controller(false)
    expect(model.currentMotion).toBe("Sleep")
    preview.cycle()
    expect(model.currentMotion).toBe("Idle")
    expect(readSelection(sessionStorage).motion).toBe("Idle")
  })

  it("keeps the chosen body form through every sleep and wake frame while resting hand poses", () => {
    model.initializeEffects()
    const mini = controller(true)
    mini.expression("变")
    mini.expression("抬手2")
    mini.expression("扇面")
    const core = model.getModel()
    const ids = core.getModel().parameters.ids
    const value = (name) => core.getParameterValueByIndex(ids.indexOf(name))
    const frames = (count, form) => {
      for (let i = 0; i < count; i++) {
        model.update(1 / 60, { x: 0, y: 0 }, false, {}, { state: "idle" })
        if (form != null) expect(value("Param31")).toBeCloseTo(form)
      }
    }
    frames(120)
    expect(value("Param31")).toBeCloseTo(30)
    const chosen = { motion: "Idle", expressions: ["变", "抬手2", "扇面"] }
    for (const wake of [() => mini.cycle(), () => mini.tick({ working: true })]) {
      now += SLEEP_AFTER_MS
      mini.tick()
      expect(mini.autoSleeping).toBe(true)
      expect([...model.activeExpressions]).toEqual(["变"])
      frames(180, 30)
      expect(value("Param78")).toBeCloseTo(0)
      expect(value("ParamEyeLOpen")).toBeLessThan(0.1)
      expect(readSelection(sessionStorage)).toEqual(chosen)
      wake()
      expect(mini.autoSleeping).toBe(false)
      expect(model.currentMotion).toBe("Idle")
      expect([...model.activeExpressions]).toEqual(chosen.expressions)
      frames(120, 30)
      expect(value("Param78")).toBeCloseTo(1)
    }
    mini.motion("Sleep")
    frames(180, 30)
    mini.cycle()
    frames(120, 30)
    mini.expression("变")
    frames(120)
    now += SLEEP_AFTER_MS
    mini.tick()
    frames(180, 0)
    mini.activity()
    frames(120, 0)
  })

  it("does not auto-sleep previews, paused frames, hidden documents, or open menus", () => {
    const preview = controller(false)
    preview.tick()
    now = 100_000
    preview.tick()
    expect(model.currentMotion).toBe("Idle")
    const mini = controller(true)
    for (const gate of ["paused", "hidden", "menuOpen", "working"]) {
      now += 100_000
      mini.tick({ [gate]: true })
      mini.tick()
      expect(model.currentMotion).toBe("Idle")
    }
    now += SLEEP_AFTER_MS
    mini.tick()
    expect(model.currentMotion).toBe("Sleep")
    mini.tick({ working: true })
    expect(model.currentMotion).toBe("Idle")
    expect(mini.autoSleeping).toBe(false)
  })
})
