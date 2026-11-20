// @vitest-environment node
import { existsSync, readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { resolve } from "node:path"
import { pathToFileURL } from "node:url"
import vm from "node:vm"
import { JSDOM } from "jsdom"
import { expect, it, vi } from "vitest"
import {
  fishInteraction,
  readView,
  manifestPath,
  VIEW_KEY,
} from "../../../../examples/packages/live2d_pet/ui/profiles.js"
import {
  PetInteraction,
  readSelection,
  STATE_KEY,
} from "../../../../examples/packages/live2d_pet/ui/interaction.js"
import { pngSize, webpSize } from "../../../../examples/packages/live2d_pet/scripts/textures.mjs"

import { setupComparison } from "../../../../examples/packages/live2d_pet/ui/comparison.js"
import { models } from "../../../../examples/packages/live2d_pet/ui/models/index.js"

const root = resolve("../../examples/packages/live2d_pet")
const dragonRoot = process.env.KT_LIVE2D_DRAGON_DIR || "/nonexistent-private-dragon"
const modelRoot = process.env.KT_LIVE2D_DEEPSEEK_DIR || "/nonexistent-private-deepseek"
const storage = () => new JSDOM("", { url: "http://localhost" }).window.sessionStorage
const bytes = (file) => {
  const b = readFileSync(file)
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)
}
const textureSize = (file) => {
  const content = readFileSync(file)
  return file.endsWith(".webp") ? webpSize(content) : pngSize(content)
}

it("isolates model selections, rejects unknown presets and drops unsupported sleep/expressions", () => {
  const store = storage()
  store.setItem(VIEW_KEY, JSON.stringify({ model: "deepseek", quality: "high" }))
  expect(readView(store)).toEqual({ model: "deepseek", quality: "high" })
  expect(readView(store, "?pet=dragon&quality=standard")).toEqual({
    model: "dragon",
    quality: "standard",
  })
  expect(readView(store, "?pet=../../other&quality=../bad")).toEqual({
    model: "dragon",
    quality: "high",
  })
  expect(() => manifestPath({ model: "deepseek", quality: "high" })).toThrow("导入")
  expect(manifestPath({ model: "deepseek", quality: "../bad" }, "/private/version/")).toBe(
    "/private/version/high.model3.json",
  )
  store.setItem(STATE_KEY, JSON.stringify({ version: 1, motion: "Sleep", expressions: ["抬手"] }))
  expect(readSelection(store, fishInteraction)).toBeNull()
  store.setItem(
    fishInteraction.key,
    JSON.stringify({ version: 1, motion: "Sleep", expressions: ["抬手", "脸红", "脸红"] }),
  )
  expect(readSelection(store, fishInteraction)).toEqual({ motion: "Idle", expressions: ["脸红"] })
  store.setItem(VIEW_KEY, "invalid")
  expect(readView(store)).toEqual({ model: "dragon", quality: "high" })
})

it.skipIf(!existsSync(`${modelRoot}/standard.model3.json`))(
  "uses identical model/UV inputs with correctly sized, local textures for both quality levels",
  () => {
    const manifests = ["standard", "high"].map((q) =>
      JSON.parse(readFileSync(`${modelRoot}/${q}.model3.json`)),
    )
    const totals = manifests.map((manifest) => {
      let total = 0
      for (const name of manifest.FileReferences.Textures) {
        const size = textureSize(`${modelRoot}/${name}`)
        expect(Math.max(size.width, size.height)).toBeLessThanOrEqual(
          manifest.Demo.quality === "high" ? 4096 : 2048,
        )
        total += size.width * size.height * 4
      }
      expect(total).toBe(manifest.Demo.textureBytes)
      return total / 1048576
    })
    expect(totals).toEqual([37, 148])
    expect(manifests[0].FileReferences.Moc).toBe(manifests[1].FileReferences.Moc)
    expect(manifests[0].FileReferences.Expressions).toEqual(manifests[1].FileReferences.Expressions)
  },
)

it.skipIf(!existsSync(`${modelRoot}/standard.model3.json`))(
  "runs the real fish model, keeps exclusive expressions correct, restores across frames and never invokes absent sleep",
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
    const manifest = JSON.parse(readFileSync(`${modelRoot}/standard.model3.json`))
    const model = new DragonModel("deepseek")
    try {
      model.loadModel(bytes(`${modelRoot}/${manifest.FileReferences.Moc}`), true)
      const m = model.getModel()
      // SDK construction references this browser global before any GL context is used.
      globalThis.WebGLBuffer = null
      model.createRenderer(1, 1)
      const masks = model.getRenderer()._drawableClippingManager
      const count = masks.getClippingMaskCount()
      const buffers = masks.getRenderTextureCount()
      expect(count).toBeLessThanOrEqual(buffers === 1 ? 36 : buffers * 32)
      const motionBytes = bytes(`${modelRoot}/${manifest.FileReferences.Motions.Idle[0].File}`)
      const motion = model.loadMotion(motionBytes, motionBytes.byteLength, "Idle")
      motion.setLoop(true)
      model.motions.set("Idle", motion)
      for (const { Name, File } of manifest.FileReferences.Expressions) {
        const b = bytes(`${modelRoot}/${File}`)
        model.expressions.set(Name, model.loadExpression(b, b.byteLength, Name))
      }
      const store = storage()
      let now = 0
      let interaction = new PetInteraction(model, {
        storage: store,
        mini: true,
        profile: fishInteraction,
        now: () => now,
      })
      interaction.expression("体型1")
      interaction.expression("体型2")
      expect([...model.activeExpressions]).toEqual(["体型2"])
      interaction.expression("圈圈眼")
      interaction.expression("爱心眼")
      expect(model.activeExpressions.has("圈圈眼")).toBe(false)
      interaction.expression("脸红")
      now = 120000
      interaction.tick()
      expect(model.currentMotion).toBe("Idle")
      expect(interaction.autoSleeping).toBe(false)
      interaction = new PetInteraction(model, { storage: store, profile: fishInteraction })
      expect([...model.activeExpressions]).toEqual(["体型2", "爱心眼", "脸红"])
      interaction.cycle()
      interaction.cycle()
      expect(model.activeExpressions.has("体型2")).toBe(true)
      expect(model.activeExpressions.has("脸红")).toBe(false)
      expect(model.activeExpressions.has("爱心眼")).toBe(false)
      for (let i = 0; i < 180; i++) {
        m.loadParameters()
        model._motionManager.updateMotion(m, 1 / 30)
        m.saveParameters()
        model._expressionManager.updateMotion(m, 1 / 30)
        m.update()
      }
      expect([...m.getModel().parameters.values].every(Number.isFinite)).toBe(true)
      const player = new DragonPlayer(null, () => {})
      player.model = model
      expect(Object.values(player.measure()).every(Number.isFinite)).toBe(true)
    } finally {
      model.release()
      delete globalThis.WebGLBuffer
      delete globalThis.Live2DCubismCore
    }
  },
)

it("requests host navigation when a quality button is clicked inside an old signed frame", () => {
  const host = new JSDOM(
    '<iframe src="/api/ext/demo/ui/EXPIRED/stage.html?pet=deepseek&quality=high"></iframe>',
    { url: "http://localhost/" },
  )
  const child = host.window.document.querySelector("iframe").contentWindow
  child.document.write(readFileSync(`${root}/ui/stage.html`, "utf8"))
  const send = vi.spyOn(host.window, "postMessage")
  vi.stubGlobal("window", child)
  vi.stubGlobal("document", child.document)
  vi.stubGlobal("location", child.location)
  models.custom = { ...models.dragon, id: "custom", name: "Custom model" }
  try {
    setupComparison(child.sessionStorage, false)
    child.document.querySelector('[data-quality="standard"]').click()
    expect(send).toHaveBeenCalledWith(
      { type: "kt-live2d-navigate", model: "deepseek", quality: "standard" },
      "http://localhost",
    )
    expect(child.location.href).toContain("/EXPIRED/")
    expect(JSON.parse(child.sessionStorage.getItem(VIEW_KEY))).toEqual({
      model: "deepseek",
      quality: "standard",
    })
    const custom = child.document.querySelector('[data-pet="custom"]')
    expect(custom).not.toBeNull()
    custom.click()
    expect(send).toHaveBeenLastCalledWith(
      { type: "kt-live2d-navigate", model: "custom", quality: "high" },
      "http://localhost",
    )
  } finally {
    delete models.custom
    vi.unstubAllGlobals()
    host.window.close()
  }
})

it("keeps standalone model and quality navigation consistent with saved preferences", () => {
  const dom = new JSDOM(readFileSync(`${root}/ui/stage.html`, "utf8"), {
    url: "http://localhost/stage.html",
  })
  const preferences = {
    version: 1,
    model: "deepseek",
    quality: "standard",
    models: { dragon: { scale: 1.2 }, deepseek: { expressions: ["体型1"] } },
  }
  const replace = vi.fn()
  vi.stubGlobal("window", dom.window)
  vi.stubGlobal("document", dom.window.document)
  vi.stubGlobal("location", {
    href: `http://localhost/stage.html?preferences=${encodeURIComponent(JSON.stringify(preferences))}`,
    replace,
  })
  try {
    setupComparison(dom.window.sessionStorage, false, preferences)
    dom.window.document.querySelector('[data-quality="high"]').click()
    const next = JSON.parse(new URL(replace.mock.lastCall[0]).searchParams.get("preferences"))
    expect(next.quality).toBe("high")
    expect(next.models.deepseek.expressions).toEqual(["体型1"])
    expect(next.models.dragon.scale).toBe(1.2)
  } finally {
    vi.unstubAllGlobals()
    dom.window.close()
  }
})

it.skipIf(!existsSync(`${dragonRoot}/standard.model3.json`))(
  "shares dragon geometry and preserves atlas proportions with quarter-sized pixel budgets",
  () => {
    const high = JSON.parse(readFileSync(`${dragonRoot}/high.model3.json`))
    const low = JSON.parse(readFileSync(`${dragonRoot}/standard.model3.json`))
    expect(high.FileReferences.Moc).toBe(low.FileReferences.Moc)
    expect(high.FileReferences.Motions).toEqual(low.FileReferences.Motions)
    expect(high.FileReferences.Expressions).toEqual(low.FileReferences.Expressions)
    expect(high.Demo.textureBytes).toBe(192 * 1048576)
    expect(low.Demo.textureBytes).toBe(high.Demo.textureBytes / 4)
    high.FileReferences.Textures.forEach((name, i) => {
      const h = textureSize(`${dragonRoot}/${name}`)
      const l = textureSize(`${dragonRoot}/${low.FileReferences.Textures[i]}`)
      expect(l.width * 2).toBe(h.width)
      expect(l.height * 2).toBe(h.height)
    })
  },
)
