// @vitest-environment node
import { existsSync, readFileSync } from "node:fs"
import vm from "node:vm"
import { createRequire } from "node:module"
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"
import { expect, it } from "vitest"
import { applyReaction, hitRegion } from "../../../../examples/packages/live2d_pet/src/touch.js"
import { PetBehavior } from "../../../../examples/packages/live2d_pet/ui/behavior.js"
import { AgentActions } from "../../../../examples/packages/live2d_pet/ui/agent-actions.js"
import { configureActionCatalog } from "../../../../examples/packages/live2d_pet/ui/action-catalog.js"
import catalogs from "../../../../examples/packages/live2d_pet/ui/action-sets.json"
import { WORK_MOTIONS } from "../../../../examples/packages/live2d_pet/ui/profiles.js"
import { modelRectangle, cropInterval } from "../../../../examples/packages/live2d_pet/ui/crop.js"

const root = resolve("../../examples/packages/live2d_pet")
const dragonRoot = process.env.KT_LIVE2D_DRAGON_DIR || "/nonexistent-private-dragon"
const fishRoot = process.env.KT_LIVE2D_DEEPSEEK_DIR || "/nonexistent-private-deepseek"
const ready =
  ["ui/runtime/live2dcubismcore.min.js", "ui/runtime/player.js"].every((path) =>
    existsSync(`${root}/${path}`),
  ) && existsSync(`${dragonRoot}/kohaku.moc3`)
const bytes = (file) => {
  const data = readFileSync(file)
  return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)
}

it.skipIf(!ready)(
  "runs original motions and expressions without accumulating additive parameters",
  async () => {
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
    const { DragonModel, DragonPlayer, initializeFramework } = await import(
      /* @vite-ignore */ pathToFileURL(`${root}/ui/runtime/player.js`).href
    )
    initializeFramework()
    const model = new DragonModel()
    try {
      model.loadModel(bytes(`${dragonRoot}/kohaku.moc3`), true)
      model.getModel().update()
      const player = new DragonPlayer(null, () => {})
      player.model = model
      const fitted = player.measure()
      const wing = model.getModel().getModel().parameters.ids.indexOf("Param76")
      model.getModel().setParameterValueByIndex(wing, 10)
      model.getModel().update()
      for (let i = 0; i < model.getModel().getDrawableCount(); i++) {
        if (model.getModel().getDrawableOpacity(i) <= 0) continue
        const vertices = model.getModel().getDrawableVertices(i)
        for (let j = 0; j < vertices.length; j += 2) {
          expect(vertices[j]).toBeGreaterThanOrEqual(fitted.left - 0.001)
          expect(vertices[j]).toBeLessThanOrEqual(fitted.right + 0.001)
        }
      }
      model.getModel().loadParameters()
      for (const [name, file] of [
        ["Idle", "idle"],
        ["Sleep", "sleep2"],
      ]) {
        const data = bytes(`${dragonRoot}/${file}.motion3.json`)
        const motion = model.loadMotion(data, data.byteLength, name)
        motion.setLoop(true)
        model.motions.set(name, motion)
        model.motion(name)
        for (let i = 0; i < 120; i++) model._motionManager.updateMotion(model.getModel(), 1 / 60)
        expect(
          Array.from(model.getModel().getModel().parameters.values).every(Number.isFinite),
        ).toBe(true)
      }
      expect(() => model.motion("missing")).toThrow("Unknown motion")
      expect(() => model.expression("missing")).toThrow("Unknown expression")
      model.motion("Idle")
      const data = bytes(`${dragonRoot}/抬手.exp3.json`)
      model.expressions.set("抬手", model.loadExpression(data, data.byteLength, "抬手"))
      const id = model.getModel().getModel().parameters.ids.indexOf("Param78")
      const baseline = model.getModel().getParameterValueByIndex(id)
      model.expression("抬手")
      for (let i = 0; i < 120; i++) {
        model.getModel().loadParameters()
        model._expressionManager.updateMotion(model.getModel(), 1 / 60)
      }
      expect(model.getModel().getParameterValueByIndex(id)).toBeCloseTo(baseline + 1)
      const fan = bytes(`${dragonRoot}/开扇.exp3.json`)
      model.expressions.set("开扇", model.loadExpression(fan, fan.byteLength, "开扇"))
      model.expression("开扇")
      for (let i = 0; i < 120; i++) {
        model.getModel().loadParameters()
        model._expressionManager.updateMotion(model.getModel(), 1 / 60)
      }
      expect(model.getModel().getParameterValueByIndex(id)).toBeCloseTo(baseline + 1)
      const pattern = bytes(`${dragonRoot}/扇面.exp3.json`)
      model.expressions.set("扇面", model.loadExpression(pattern, pattern.byteLength, "扇面"))
      expect(model.expression("扇面")).not.toContain("开扇")
      model.expression("")
      model.getModel().loadParameters()
      model._expressionManager.updateMotion(model.getModel(), 1 / 60)
      expect(model.getModel().getParameterValueByIndex(id)).toBeCloseTo(baseline)
      for (const [profile, folder, file] of [
        ["dragon", dragonRoot, "kohaku.moc3"],
        ...(existsSync(`${fishRoot}/high.model3.json`)
          ? [["deepseek", fishRoot, "大肥鱼.moc3"]]
          : []),
      ]) {
        const pet = new DragonModel(profile)
        try {
          pet.loadModel(bytes(`${folder}/${file}`), true)
          pet.initializeEffects()
          const core = pet.getModel()
          core.update()
          core.saveParameters()
          const original = [...core.getModel().parameters.values]
          const ids = core.getModel().parameters.ids
          const mouth = ids.indexOf("ParamMouthOpenY")
          expect(mouth).toBeGreaterThanOrEqual(0)
          const restingMouth = core.getParameterValueByIndex(mouth)
          const restingVertices = Array.from({ length: core.getDrawableCount() }, (_, i) => [
            ...core.getDrawableVertices(i),
          ])
          pet.applySpeech(0.65)
          core.update()
          expect(core.getParameterValueByIndex(mouth)).toBeCloseTo(0.65)
          expect(
            restingVertices.some((vertices, i) =>
              vertices.some((v, j) => Math.abs(v - core.getDrawableVertices(i)[j]) > 0.00001),
            ),
          ).toBe(true)
          pet.clearSpeech()
          expect(core.getParameterValueByIndex(mouth)).toBe(restingMouth)
          for (let i = 0; i < 120; i++) {
            core.loadParameters()
            pet.applySpeech(i % 2 ? 0.1 : 0.6)
          }
          core.loadParameters()
          pet.applySpeech(null)
          expect([...core.getModel().parameters.values]).toEqual(original)
          const axis = ids.indexOf("ParamAngleY")
          pet.validateActionSet({
            actions: { nod: { playback: { type: "pose", parameters: { ParamAngleY: -10 } } } },
          })
          expect(() =>
            pet.validateActionSet({
              actions: { invalid: { playback: { type: "pose", parameters: { MissingAxis: 1 } } } },
            }),
          ).toThrow("invalid")
          expect(() =>
            pet.validateActionSet({
              actions: { invalid: { playback: { type: "motion", name: "MissingMotion" } } },
            }),
          ).toThrow("invalid")
          pet.workActions.apply(core, 2, { state: "idle" })
          core.loadParameters()
          pet.actionPose.apply(
            core,
            1 / 60,
            { playback: { type: "pose", parameters: { ParamAngleY: 1000 } } },
            false,
          )
          expect(cropInterval({ model: pet })).toBe(0)
          for (let i = 0; i < 120; i++) {
            core.loadParameters()
            pet.actionPose.apply(
              core,
              1 / 60,
              { playback: { type: "pose", parameters: { ParamAngleY: 1000 } } },
              false,
            )
          }
          expect(core.getParameterValueByIndex(axis)).toBeCloseTo(
            core.getParameterMaximumValue(axis),
            2,
          )
          expect(cropInterval({ model: pet })).toBe(100)
          // Speaking owns only mouth opening, after the action/physics layers.
          const form = ids.indexOf("ParamMouthForm")
          core.setParameterValueByIndex(form, 0.35)
          const posed = [...core.getModel().parameters.values]
          pet.applySpeech(0.6)
          core.update()
          expect(core.getParameterValueByIndex(mouth)).toBeCloseTo(0.6)
          for (let i = 0; i < posed.length; i++) {
            if (i !== mouth) expect(core.getParameterValueByIndex(i)).toBe(posed[i])
          }
          pet.clearSpeech()
          expect([...core.getModel().parameters.values]).toEqual(posed)
          for (let i = 0; i < 120; i++) {
            core.loadParameters()
            pet.actionPose.apply(core, 1 / 60, {}, false)
          }
          expect([...core.getModel().parameters.values]).toEqual(original)
          const manifest = JSON.parse(readFileSync(`${folder}/high.model3.json`, "utf8"))
          const expression = manifest.FileReferences.Expressions.find(
            (e) => e.Name === (profile === "dragon" ? "抬手" : "脸红"),
          )
          const poseExpressionData = bytes(`${folder}/${expression.File}`)
          const loadedExpression = pet.loadExpression(
            poseExpressionData,
            poseExpressionData.byteLength,
            expression.Name,
          )
          pet.expressions.set(expression.Name, loadedExpression)
          pet.validateActionSet({
            actions: { face: { playback: { type: "expression", name: expression.Name } } },
          })
          const catalog = catalogs.sets[profile]
          const customCatalog = structuredClone(catalog)
          customCatalog.label = "Custom poses"
          customCatalog.actions.nod.playback = { type: "pose", parameters: { ParamAngleY: -5 } }
          const controller = new AgentActions(new PetBehavior(), profile, catalog)
          expect(configureActionCatalog(controller, pet, customCatalog, catalog, true)).toBe("")
          expect(controller.catalog.label).toBe("Custom poses")
          const invalidCatalog = structuredClone(customCatalog)
          invalidCatalog.actions.nod.playback.parameters.MissingAxis = 1
          expect(configureActionCatalog(controller, pet, invalidCatalog, catalog, true)).toContain(
            "nod",
          )
          expect(controller.catalog).toEqual(catalog)
          expect(controller.enabled).toBe(false)
          expect(configureActionCatalog(controller, pet, customCatalog, catalog, true)).toBe("")
          expect(controller.catalog.label).toBe("Custom poses")
          expect(controller.enabled).toBe(true)
          for (let i = 0; i < 120; i++) {
            core.loadParameters()
            pet.actionPose.apply(
              core,
              1 / 60,
              { playback: { type: "expression", name: expression.Name } },
              false,
            )
          }
          expect([...core.getModel().parameters.values]).not.toEqual(original)
          expect(pet.activeExpressions.size).toBe(0)
          core.loadParameters()
          pet.actionPose.apply(core, 1 / 60, {}, true)
          expect([...core.getModel().parameters.values]).toEqual(original)
          const behavior = new PetBehavior(() => 0)
          behavior.touch("head")
          const reaction = behavior.sample(900)
          for (let i = 0; i < 120; i++) {
            core.loadParameters()
            applyReaction(core, reaction)
          }
          const angle = core.getModel().parameters.ids.indexOf("ParamAngleZ")
          expect(core.getParameterValueByIndex(angle)).toBeCloseTo(original[angle] + 12)
          core.loadParameters()
          applyReaction(core, reaction, new Set(["ParamAngleZ"]))
          expect(core.getParameterValueByIndex(angle)).toBe(original[angle])
          core.loadParameters()
          applyReaction(core, behavior.sample(2000))
          expect([...core.getModel().parameters.values]).toEqual(original)
          pet.initializeEffects()
          pet.update(0, { x: 0, y: 0 }, false, {})
          const before = core.getModel().drawables.vertexPositions.map((v) => [...v])
          pet.update(0, { x: 0, y: 0 }, false, { ParamAngleZ: 18 })
          const movement = Math.max(
            ...core
              .getModel()
              .drawables.vertexPositions.flatMap((v, mesh) =>
                Array.from(v, (value, index) => Math.abs(value - before[mesh][index])),
              ),
          )
          expect(movement).toBeGreaterThan(0.01)
          const physicsData = bytes(
            `${folder}/${profile === "dragon" ? "kohaku.physics3.json" : "大肥鱼.physics3.json"}`,
          )
          pet.loadPhysics(physicsData, physicsData.byteLength)
          if (profile === "deepseek")
            for (const [name, file] of Object.entries(WORK_MOTIONS))
              pet.workActions.load(pet, name, bytes(`${folder}/${file}`))
          if (profile === "deepseek") {
            pet.validateActionSet({
              actions: { point: { playback: { type: "motion", name: "FingerIn" } } },
            })
            for (let i = 0; i < 120; i++) {
              core.loadParameters()
              pet.actionPose.apply(
                core,
                1 / 60,
                { playback: { type: "motion", name: "FingerIn" }, elapsed: 100 },
                false,
              )
            }
            expect(core.getParameterValueByIndex(ids.indexOf("Movement3"))).toBeGreaterThan(0.95)
            core.loadParameters()
            pet.actionPose.apply(core, 1 / 60, {}, true)
            expect([...core.getModel().parameters.values]).toEqual(original)
          }
          for (let i = 0; i < 20; i++) pet.update(1 / 60, { x: 0, y: 0 }, true, {})
          const waiting = new PetBehavior(() => 0)
          waiting.setWork({ status: "waiting", connected: true })
          for (let i = 0; i < 120; i++)
            pet.update(1 / 60, { x: 0, y: 0 }, true, waiting.sample((i * 1000) / 60))
          const renderedAngle = core
            .getModel()
            .parameters.ids.indexOf(profile === "dragon" ? "ParamAngleZ1" : "ParamAngleZ")
          expect(core.getParameterValueByIndex(renderedAngle)).toBeGreaterThan(5)
          core.loadParameters()
          core.update()
          const player = new DragonPlayer(
            {
              width: 800,
              height: 800,
              getBoundingClientRect: () => ({ left: 20, top: 30, width: 800, height: 800 }),
            },
            () => {},
          )
          player.model = pet
          // Diagnostics must read the selected model adapter, including imported models.
          pet.applySpeech(0.6)
          player.recordFrame(100, 2)
          expect(player.mouthRange).toEqual([expect.closeTo(0.6), expect.closeTo(0.6)])
          pet.clearSpeech()
          player.bounds = player.measure()
          expect(hitRegion(player, -100, -100)).toBeNull()
          const value = (name) =>
            core.getParameterValueByIndex(core.getModel().parameters.ids.indexOf(name))
          const check = (state, frames = 120) => {
            let rectangle = modelRectangle(player)
            let lastCrop = 0
            for (let i = 0; i < frames; i++) {
              pet.update(1 / 60, { x: 0, y: 0 }, true, {}, { state, progress: i / frames })
              if ((i * 1000) / 60 - lastCrop >= cropInterval(player)) {
                rectangle = modelRectangle(player)
                lastCrop = (i * 1000) / 60
              }
              const b = player.bounds
              const scale = Math.min(1.8 / (b.top - b.bottom), 1.8 / (b.right - b.left))
              for (let mesh = 0; mesh < core.getDrawableCount(); mesh++) {
                if (core.getDrawableOpacity(mesh) < 0.05) continue
                const vertices = core.getDrawableVertices(mesh)
                for (let j = 0; j < vertices.length; j += 2) {
                  const x = 20 + ((vertices[j] - (b.left + b.right) / 2) * scale + 1) * 400
                  const y = 30 + (1 - (vertices[j + 1] - (b.top + b.bottom) / 2) * scale) * 400
                  if (
                    !rectangle.regions.some(
                      (r) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom,
                    )
                  )
                    throw new Error(
                      `${profile} ${state} frame ${i}: mesh ${mesh} at ${x},${y} escaped the throttled crop ${JSON.stringify(rectangle.regions)}`,
                    )
                }
              }
            }
          }
          check("running")
          if (profile === "deepseek") expect(value("Movement3")).toBeGreaterThan(0.95)
          check("waiting")
          expect(value(profile === "dragon" ? "Param78" : "LeanInput")).toBeCloseTo(
            profile === "dragon" ? 1 : -30,
            1,
          )
          if (profile === "deepseek") expect(value("Movement3")).toBeLessThan(0.05)
          let clock = 0
          const sequence = new PetBehavior(() => clock, profile)
          const work = (status, completed = 0) => ({
            hostId: null,
            sessionId: "sequence",
            connected: true,
            status,
            busy: status === "running",
            started: 1,
            completed,
          })
          sequence.setWork(work("waiting"))
          clock = 1000
          sequence.setWork(work("running"))
          clock = 1050
          sequence.setWork(work("ready", 1))
          const states = new Set()
          for (let i = 0; i <= 80; i++) {
            clock = 1250 + (i * 1000) / 60
            const action = sequence.action()
            states.add(action.state)
            pet.update(1 / 60, { x: 0, y: 0 }, true, sequence.sample(), action)
            if (i === 21 && profile === "dragon") {
              expect(value("Param36")).toBeCloseTo(10, 2)
              expect(value("Param78")).toBeGreaterThan(0.99)
            }
            if (i === 36 && profile === "deepseek")
              expect(Math.abs(value("LeanInput"))).toBeLessThan(0.2)
            expect(cropInterval(player)).toBe(0)
            const crop = modelRectangle(player)
            expect([crop.left, crop.right, crop.top, crop.bottom].every(Number.isFinite)).toBe(true)
            expect(Object.values(crop.visible).every(Number.isFinite)).toBe(true)
          }
          expect([...states]).toEqual(["resolving", "ready"])
          check("idle")
          check("waiting")
          check("resolving", profile === "dragon" ? 48 : 36)
          if (profile === "dragon") {
            expect(value("Param36")).toBeGreaterThan(9.9)
            expect(value("Param78")).toBeLessThan(0.05)
          } else expect(value("LeanInput")).toBeGreaterThan(-0.1)
          check("running", 24)
          check("waiting", 24)
          check("ready", 204)
          check("idle")
          expect(value(profile === "dragon" ? "Param78" : "LeanInput")).toBeCloseTo(0, 1)
          if (profile === "deepseek") expect(value("Movement2")).toBeLessThan(0.01)
          pet.workActions.refreshBounds()
          pet.currentMotion = "Sleep"
          pet.update(1 / 60, { x: 0, y: 0 }, true, {}, { state: "waiting" })
          expect(cropInterval(player)).toBe(100)
          pet.currentMotion = "Idle"
          check("idle", 30)
          expect([...core.getModel().parameters.values].every(Number.isFinite)).toBe(true)
          const baselineExpression = profile === "dragon" ? "抬手" : "前倾"
          const expressionData = bytes(
            `${folder}/${profile === "dragon" ? "" : "Exp3/"}${baselineExpression}.exp3.json`,
          )
          pet.expressions.set(
            baselineExpression,
            pet.loadExpression(expressionData, expressionData.byteLength, baselineExpression),
          )
          pet.expression(baselineExpression)
          check("waiting")
          pet.workActions.suspend()
          check("waiting", 126)
          expect(cropInterval(player)).toBe(0)
          check("waiting", 84)
          check("idle")
          expect(value(profile === "dragon" ? "Param78" : "LeanInput")).toBeCloseTo(
            profile === "dragon" ? 1 : -30,
            1,
          )
          expect([...pet.activeExpressions]).toEqual([baselineExpression])
          pet.expression("")
          check("idle")
          core.loadParameters()
          core.update()

          const raw = core.getModel()
          const part = raw.parts.ids.indexOf(profile === "dragon" ? "Part22" : "Part15")
          const mesh = [...raw.drawables.parentPartIndices].findIndex(
            (parent, index) =>
              parent === part &&
              core.getDrawableOpacity(index) > 0 &&
              core.getDrawableVertexIndices(index).length > 0,
          )
          expect(mesh).toBeGreaterThanOrEqual(0)
          const indices = core.getDrawableVertexIndices(mesh),
            vertices = core.getDrawableVertices(mesh)
          const x =
            (vertices[indices[0] * 2] + vertices[indices[1] * 2] + vertices[indices[2] * 2]) / 3
          const y =
            (vertices[indices[0] * 2 + 1] +
              vertices[indices[1] * 2 + 1] +
              vertices[indices[2] * 2 + 1]) /
            3
          for (const zoom of [1, 3]) {
            player.zoom = zoom
            player.pan = { x: 0.2, y: -0.4 }
            const b = player.bounds
            const scale = Math.min(1.8 / (b.top - b.bottom), 1.8 / (b.right - b.left)) * zoom
            const screenX = 20 + ((x - (b.left + b.right) / 2) * scale + player.pan.x + 1) * 400
            const screenY = 30 + (1 - ((y - (b.top + b.bottom) / 2) * scale + player.pan.y)) * 400
            expect(hitRegion(player, screenX, screenY)).toBe("head")
            const crop = modelRectangle(player)
            expect(screenX).toBeGreaterThanOrEqual(crop.left)
            expect(screenX).toBeLessThanOrEqual(crop.right)
            expect(screenY).toBeGreaterThanOrEqual(crop.top)
            expect(screenY).toBeLessThanOrEqual(crop.bottom)
            for (let i = 0; i < core.getDrawableCount(); i++) {
              if (core.getDrawableOpacity(i) < 0.05) continue
              const vertices = core.getDrawableVertices(i)
              for (let j = 0; j < vertices.length; j += 2) {
                const vx =
                  20 + ((vertices[j] - (b.left + b.right) / 2) * scale + player.pan.x + 1) * 400
                const vy =
                  30 + (1 - (vertices[j + 1] - (b.top + b.bottom) / 2) * scale - player.pan.y) * 400
                expect(
                  crop.regions.some(
                    (r) => vx >= r.left && vx <= r.right && vy >= r.top && vy <= r.bottom,
                  ),
                ).toBe(true)
              }
            }
          }
          for (const physics of [false, true]) {
            let now = 0
            const automatic = new PetBehavior(() => now, profile)
            const nod = new AgentActions(automatic, profile, catalogs.sets[profile], () => now)
            nod.configure(true)
            expect(
              nod.receive({
                id: `nod-${physics}`,
                operation: "perform",
                model: profile,
                action: "nod",
                expiresAt: Date.now() + 4000,
              }),
            ).toBe(true)
            const renderedY = ids.indexOf(profile === "dragon" ? "ParamAngleY1" : "ParamAngleY")
            const angles = []
            for (let frame = 0; frame < 120; frame++) {
              now = (frame * 1000) / 60
              pet.update(1 / 60, { x: 0, y: 0 }, physics, {}, nod.action(now), 0.6)
              angles.push(core.getParameterValueByIndex(renderedY))
              expect(core.getParameterValueByIndex(mouth)).toBeCloseTo(0.6)
            }
            expect(Math.min(...angles)).toBeLessThan(-8)
            expect(angles[56]).toBeGreaterThan(angles[36] + 5)
            expect(angles[76]).toBeLessThan(angles[56] - 3)
            expect(angles.at(-1)).toBeGreaterThan(-2)
            expect(nod.active).toBe(true)
            nod.release()
            for (let frame = 0; frame < 120; frame++) {
              now += 1000 / 60
              pet.update(1 / 60, { x: 0, y: 0 }, physics, {}, nod.action(now))
            }
            expect(pet.actionPose.values.size).toBe(0)
            expect(core.getParameterValueByIndex(mouth)).toBe(restingMouth)
            expect(
              nod.receive({
                id: `interrupt-${physics}`,
                operation: "perform",
                model: profile,
                action: "nod",
                expiresAt: Date.now() + 4000,
              }),
            ).toBe(true)
            for (let frame = 0; frame < 35; frame++) {
              now += 1000 / 60
              pet.update(1 / 60, { x: 0, y: 0 }, physics, {}, nod.action(now), 0.5)
            }
            expect(core.getParameterValueByIndex(renderedY)).toBeLessThan(-8)
            nod.configure(false)
            for (let frame = 0; frame < 120; frame++) {
              now += 1000 / 60
              pet.update(1 / 60, { x: 0, y: 0 }, physics, {}, nod.action(now))
            }
            expect(pet.actionPose.values.size).toBe(0)
            expect(Math.abs(core.getParameterValueByIndex(renderedY))).toBeLessThan(0.01)
            expect(core.getParameterValueByIndex(mouth)).toBe(restingMouth)
          }
        } finally {
          pet.release()
        }
      }
    } finally {
      model.release()
      delete globalThis.Live2DCubismCore
    }
  },
  30000,
)
