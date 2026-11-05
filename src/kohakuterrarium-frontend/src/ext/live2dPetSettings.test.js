import { afterEach, expect, it, vi } from "vitest"
import { mount, flushPromises } from "@vue/test-utils"
import { createMockHost } from "@/ext/sdk/mock"
import {
  normalizePreferences,
  updatePreferences,
  toggleAppearance,
  fittedSize,
  PREFERENCES_KEY,
} from "../../../../examples/packages/live2d_pet/ui/preferences.js"
import {
  createSettingsStore,
  settingsFor,
  initializeSettings,
} from "../../../../examples/packages/live2d_pet/ui/settings-state.js"
import register from "../../../../examples/packages/live2d_pet/ui/index.js"
import sets from "../../../../examples/packages/live2d_pet/ui/action-sets.json"

afterEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

it("migrates the old switch into three modes and persists a registered catalog per model", () => {
  expect(normalizePreferences({}).controlMode).toBe("pet")
  expect(normalizePreferences({}).talkingMouth).toBe(true)
  expect(normalizePreferences({ version: 1, talkingMouth: false }).talkingMouth).toBe(false)
  let prefs = normalizePreferences({ version: 1, agentControl: true })
  expect(prefs.controlMode).toBe("expression")
  prefs = updatePreferences(prefs, {
    controlMode: "agent",
    models: { dragon: { actionSet: sets.sets.dragon } },
  })
  expect(prefs.agentControl).toBe(true)
  expect(normalizePreferences(prefs).models.dragon.actionSet.actions.nod.playback).toEqual({
    type: "motion",
    name: "Nod",
  })
  expect(prefs.models.deepseek.actionSet).toBeNull()
  prefs = updatePreferences(prefs, { controlMode: "pet" })
  expect(prefs.agentControl).toBe(false)
  expect(
    updatePreferences(prefs, { models: { dragon: { actionSet: sets.sets.deepseek } } }).models
      .dragon.actionSet,
  ).toBeNull()
})

it("shares settings between distinct contribution facades of one package", () => {
  const host = createMockHost({ package: "live2d-pet-demo" })
  const facade = () => new Proxy({}, { get: (_, property) => host.kt[property] })
  const first = settingsFor(facade())
  const second = settingsFor(facade())
  first.update({ model: "deepseek", debug: true })
  expect(second.state.value.model).toBe("deepseek")
  expect(second.state.value.debug).toBe(true)
})

it("validates persisted choices, saves per model, migrates once, and restores after recreating the store", () => {
  const values = new Map()
  const storage = {
    get: (key) => values.get(key),
    set: (key, value) => values.set(key, JSON.parse(JSON.stringify(value))),
  }
  sessionStorage.setItem(
    "kt-live2d-dragon-interaction-v1",
    JSON.stringify({ version: 1, motion: "Idle", expressions: ["变"] }),
  )
  const first = createSettingsStore(storage, sessionStorage)
  expect(first.state.value.models.dragon.expressions).toEqual(["变"])
  first.update({ models: { dragon: { scale: 1.35, idle: "wings" } }, debug: true })
  first.update({
    model: "deepseek",
    models: { deepseek: { scale: 0.8, idle: "sleep", expressions: ["体型1", "体型2", "missing"] } },
  })
  sessionStorage.clear()
  const restored = createSettingsStore(storage, sessionStorage)
  expect(restored.state.value).toMatchObject({
    model: "deepseek",
    debug: true,
    models: {
      dragon: { scale: 1.35, idle: "wings", expressions: ["变"] },
      deepseek: { scale: 0.8, idle: "natural", expressions: ["体型2"] },
    },
  })
  restored.reset("dragon")
  expect(restored.state.value.models.dragon.expressions).toEqual([])
  expect(restored.state.value.models.deepseek.scale).toBe(0.8)
  expect(values.get(PREFERENCES_KEY)).toEqual(restored.state.value)
  const blocked = createSettingsStore({
    get() {
      throw Error("blocked")
    },
    set() {
      throw Error("blocked")
    },
  })
  blocked.update({ model: "deepseek" })
  expect(blocked.state.value.model).toBe("deepseek")
})

it("keeps exclusive appearances and safe proportional sizes", () => {
  let prefs = normalizePreferences({
    version: 1,
    model: "../../unknown",
    debug: "true",
    models: { dragon: { scale: Infinity } },
  })
  expect(prefs).toMatchObject({ model: "dragon", debug: false, models: { dragon: { scale: 1 } } })
  prefs = updatePreferences(prefs, { models: { dragon: { expressions: ["抬手2", "开扇", "变"] } } })
  expect(toggleAppearance(prefs, "dragon", "抬手2")).toEqual(["变"])
  expect(toggleAppearance(prefs, "dragon", "抬手")).toEqual(["开扇", "变", "抬手"])
  const [width, height] = fittedSize(
    updatePreferences(prefs, { models: { dragon: { scale: 9 } } }),
    500,
    500,
  )
  expect(height).toBeLessThanOrEqual(484)
  expect(width / height).toBeCloseTo(500 / 671, 2)
})

it("provides production settings without a renderer, synchronizes live changes, and gates debug preview", async () => {
  const host = createMockHost({
    package: "live2d-pet-demo",
    overlayId: "dragon",
    routes: { "GET /models": { deepseek: { version: "a".repeat(32) } } },
  })
  await register(host.kt)
  host.kt.hideOverlay("dragon")
  const preview = mount(host.registered.tab.get("preview").component)
  const settings = mount(host.registered.settings.get("preferences").component)
  const companion = mount(host.registered.overlay.get("dragon").component, {
    attachTo: document.body,
  })
  try {
    expect(preview.find("iframe").exists()).toBe(false)
    expect(companion.find("iframe").exists()).toBe(false)
    expect(settings.find('select[aria-label="交互模式"]').element.value).toBe("pet")
    const appearance = () =>
      settings.findAll("fieldset").find((f) => f.find("legend").text() === "形象状态")
    expect(appearance().text()).toContain("幼年态")
    expect(appearance().text()).not.toContain("持扇掩面")
    await settings.find('select[aria-label="交互模式"]').setValue("agent")
    expect(settings.text()).toContain("创建 / 打开龙娘对话")
    await settings.find('select[aria-label="交互模式"]').setValue("pet")
    const input = settings.find('input[aria-label="导入动作集"]')
    const imported = structuredClone(sets.sets.dragon)
    imported.label = "My gestures"
    Object.defineProperty(input.element, "files", {
      configurable: true,
      value: [{ size: 500, text: async () => JSON.stringify(imported) }],
    })
    await input.trigger("change")
    await flushPromises()
    expect(settingsFor(host.kt).state.value.models.dragon.actionSet.label).toBe("My gestures")
    expect(settingsFor(host.kt).state.value.controlMode).toBe("pet")
    Object.defineProperty(input.element, "files", {
      configurable: true,
      value: [{ size: 500, text: async () => '{"bad":true}' }],
    })
    await input.trigger("change")
    await flushPromises()
    expect(settingsFor(host.kt).state.value.models.dragon.actionSet.label).toBe("My gestures")
    await settings.find('select[aria-label="Live2D 角色"]').setValue("deepseek")
    expect(appearance().text()).toContain("体型1")
    expect(appearance().text()).toContain("裙子切换")
    expect(appearance().text()).not.toContain("脸红")
    await settings.find('input[aria-label="宠物大小"]').setValue("125")
    await settings
      .findAll("button")
      .find((button) => button.text() === "体型1")
      .trigger("click")
    await settings
      .findAll("button")
      .find((button) => button.text() === "显示宠物")
      .trigger("click")
    await flushPromises()
    const frame = companion.find("iframe").element
    const url = new URL(frame.src)
    expect(JSON.parse(url.searchParams.get("preferences")).models.dragon.actionSet).toBeNull()
    expect(JSON.parse(url.searchParams.get("preferences"))).toMatchObject({
      model: "deepseek",
      debug: false,
      models: { deepseek: { scale: 1.25, expressions: ["体型1"] } },
    })
    const post = vi.spyOn(frame.contentWindow, "postMessage")
    window.dispatchEvent(
      new MessageEvent("message", {
        source: frame.contentWindow,
        origin: url.origin,
        data: {
          type: "kt-live2d-ready",
          channel: url.searchParams.get("channel"),
          model: "deepseek",
        },
      }),
    )
    await settings.find('input[aria-label="宠物大小"]').setValue("90")
    await flushPromises()
    expect(frame.src).toBe(url.href)
    await settings.find('input[aria-label="说话口型"]').setValue(false)
    await flushPromises()
    expect(frame.src).toBe(url.href)
    expect(settingsFor(host.kt).state.value.talkingMouth).toBe(false)
    expect(
      post.mock.calls.filter(([data]) => data.type === "kt-live2d-settings").at(-1)[0].preferences
        .talkingMouth,
    ).toBe(false)
    expect(
      post.mock.calls.filter(([data]) => data.type === "kt-live2d-settings").at(-1)[0].preferences
        .models.deepseek.scale,
    ).toBe(0.9)
    const childPatch = {
      type: "kt-live2d-preferences",
      channel: url.searchParams.get("channel"),
      model: "deepseek",
      sequence: 1,
      patch: { models: { deepseek: { expressions: ["体型2"] } } },
    }
    const send = (data) =>
      window.dispatchEvent(
        new MessageEvent("message", { source: frame.contentWindow, origin: url.origin, data }),
      )
    send({ ...childPatch, channel: "old" })
    send({ ...childPatch, model: "dragon" })
    expect(settingsFor(host.kt).state.value.models.deepseek.expressions).toEqual(["体型1"])
    send(childPatch)
    await flushPromises()
    expect(
      settings
        .findAll("button")
        .find((button) => button.text() === "体型2")
        .attributes("aria-pressed"),
    ).toBe("true")
    send({ ...childPatch, patch: { debug: true } })
    expect(settingsFor(host.kt).state.value.debug).toBe(false)
    await settings.find('input[aria-label="Debug 调试功能"]').setValue(true)
    expect(preview.text()).toContain("Debug")
    expect(preview.find("iframe").exists()).toBe(false)
    host.kt.hideOverlay("dragon")
    await flushPromises()
    expect(companion.find("iframe").exists()).toBe(false)
    expect(preview.find("iframe").exists()).toBe(true)
    await settings.find('input[aria-label="Debug 调试功能"]').setValue(false)
    expect(preview.find("iframe").exists()).toBe(false)
  } finally {
    preview.unmount()
    companion.unmount()
    settings.unmount()
  }
})

it("loads durable choices before registering renderers and never saves defaults after a failed load", async () => {
  const host = createMockHost({ package: "live2d-pet-demo" })
  host.kt.preferences.set(
    PREFERENCES_KEY,
    normalizePreferences({
      version: 1,
      model: "deepseek",
      models: {
        dragon: { scale: 1.15, expressions: ["变"] },
        deepseek: { expressions: ["体型1"] },
      },
    }),
  )
  await register(host.kt)
  expect(settingsFor(host.kt).state.value.model).toBe("deepseek")
  expect(settingsFor(host.kt).state.value.models.dragon.scale).toBe(1.15)
  settingsFor(host.kt).update({ model: "dragon" })
  expect((await host.kt.preferences.get(PREFERENCES_KEY)).model).toBe("dragon")
  const offline = createMockHost({ package: "live2d-pet-demo" })
  vi.spyOn(offline.kt.preferences, "get").mockRejectedValue(new Error("offline"))
  const save = vi.spyOn(offline.kt.preferences, "set")
  const store = await initializeSettings(offline.kt)
  expect(save).not.toHaveBeenCalled()
  store.update({ debug: true })
  expect(save).toHaveBeenCalledOnce()
})

it("selects the dragon only when the host has granted the empty-pet startup fallback", async () => {
  const host = createMockHost({ package: "live2d-pet-demo" })
  host.kt.preferences.set(PREFERENCES_KEY, { version: 1, model: "deepseek", quality: "standard" })
  host.kt.consumeStartupFallback = () => true
  await register(host.kt)
  expect(settingsFor(host.kt).state.value).toMatchObject({ model: "dragon", quality: "standard" })
})
