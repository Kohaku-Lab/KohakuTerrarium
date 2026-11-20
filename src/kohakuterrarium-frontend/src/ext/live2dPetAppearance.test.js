// @vitest-environment node
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"
import vm from "node:vm"
import { expect, it } from "vitest"
import { PetInteraction } from "../../../../examples/packages/live2d_pet/ui/interaction.js"

const root = resolve("../../examples/packages/live2d_pet")
const dragonRoot = process.env.KT_LIVE2D_DRAGON_DIR || "/nonexistent-private-dragon"
const bytes = (path) => {
  const data = readFileSync(path)
  return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)
}

for (const [profile, folder, states, face] of [
  ["dragon", dragonRoot, ["变"], "抬手"],
  [
    "deepseek",
    process.env.KT_LIVE2D_DEEPSEEK_DIR || "/nonexistent-private-deepseek",
    ["体型2", "单片眼镜", "脱鞋", "尾巴开关"],
    "脸红",
  ],
]) {
  it.skipIf(!existsSync(`${folder}/high.model3.json`))(
    `preserves ${profile} appearance states across actions, speech and expression resets`,
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
      vm.runInNewContext(
        readFileSync(`${root}/ui/runtime/live2dcubismcore.min.js`, "utf8"),
        context,
      )
      globalThis.Live2DCubismCore = context.Live2DCubismCore
      const { DragonModel, initializeFramework } = await import(
        /* @vite-ignore */ pathToFileURL(`${root}/ui/runtime/player.js`).href
      )
      initializeFramework()
      const pet = new DragonModel(profile)
      try {
        const manifest = JSON.parse(readFileSync(`${folder}/high.model3.json`, "utf8"))
        pet.loadModel(bytes(`${folder}/${manifest.FileReferences.Moc}`), true)
        const physics = bytes(`${folder}/${manifest.FileReferences.Physics}`)
        pet.loadPhysics(physics, physics.byteLength)
        for (const expression of manifest.FileReferences.Expressions) {
          const data = bytes(`${folder}/${expression.File}`)
          pet.expressions.set(
            expression.Name,
            pet.loadExpression(data, data.byteLength, expression.Name),
          )
        }
        const idle = bytes(`${folder}/${manifest.FileReferences.Motions.Idle[0].File}`)
        const motion = pet.loadMotion(idle, idle.byteLength, "Idle")
        motion.setLoop(true)
        pet.motions.set("Idle", motion)
        for (const [name, file] of Object.entries(pet.adapter.workMotions)) {
          const data = bytes(`${folder}/${file}`)
          pet.workActions.load(pet, name, data)
        }
        pet.initializeEffects()
        const interaction = new PetInteraction(pet, { profile: pet.adapter.interaction })
        const core = pet.getModel()
        const ids = core.getModel().parameters.ids
        const frame = (action = { state: "idle" }) =>
          pet.update(1 / 60, { x: 0, y: 0 }, true, {}, action, 0.6)
        for (const state of states) interaction.expression(state)
        interaction.expression(face)
        for (let i = 0; i < 120; i++) frame()
        expect(pet.appearanceParameters.size).toBeGreaterThan(0)
        const selected = [...pet.appearanceParameters].map((i) => [
          i,
          core.getParameterValueByIndex(i),
        ])
        expect(selected.some(([i, v]) => v !== core.getParameterDefaultValue(i))).toBe(true)
        for (const state of ["running", "waiting", "resolving", "ready", "idle"]) {
          for (let i = 0; i < 120; i++) {
            frame({ state, progress: i / 120 })
            for (const [index, value] of selected)
              expect(core.getParameterValueByIndex(index)).toBeCloseTo(value)
          }
        }
        const conflicting = Object.fromEntries(
          selected.map(([i, value]) => [ids[i], value ? 0 : 1]),
        )
        for (const playback of [
          { type: "pose", parameters: conflicting },
          { type: "expression", name: states[0] },
        ]) {
          expect(() => pet.validateActionSet({ actions: { conflict: { playback } } })).toThrow(
            "conflict",
          )
          for (let i = 0; i < 120; i++) {
            frame({ playback })
            for (const [index, value] of selected)
              expect(core.getParameterValueByIndex(index)).toBeCloseTo(value)
          }
        }
        interaction.expression("")
        expect([...pet.activeExpressions]).toEqual(states)
        for (let i = 0; i < 120; i++) {
          frame()
          for (const [index, value] of selected)
            expect(core.getParameterValueByIndex(index)).toBeCloseTo(value)
        }
        for (const state of states) interaction.expression(state)
        for (let i = 0; i < 120; i++) frame()
        for (const index of pet.appearanceParameters)
          expect(core.getParameterValueByIndex(index)).toBeCloseTo(
            core.getParameterDefaultValue(index),
          )
        for (let i = 0; i < 120; i++) {
          frame({ playback: { type: "pose", parameters: conflicting } })
          for (const index of pet.appearanceParameters)
            expect(core.getParameterValueByIndex(index)).toBeCloseTo(
              core.getParameterDefaultValue(index),
            )
        }
      } finally {
        pet.release()
        delete globalThis.Live2DCubismCore
      }
    },
  )
}
