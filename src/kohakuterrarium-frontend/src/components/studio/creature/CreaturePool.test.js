import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  workspaceAPI: { get: vi.fn() },
  moduleAPI: { plug: vi.fn(async () => ({})), unplug: vi.fn(async () => ({})) },
  catalogAPI: {
    tools: vi.fn(async () => []),
    subagents: vi.fn(async () => []),
    triggers: vi.fn(async () => []),
    plugins: vi.fn(async () => []),
    inputs: vi.fn(async () => []),
    outputs: vi.fn(async () => []),
    models: vi.fn(async () => []),
    pluginHooks: vi.fn(async () => []),
  },
}))
const messages = vi.hoisted(() => ({ warning: vi.fn(), error: vi.fn() }))
vi.mock("@/utils/studio/api", () => api)
vi.mock("element-plus", () => ({ ElMessage: messages }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (k) => k }) }))

import CreaturePool from "./CreaturePool.vue"
import { useStudioCreatureStore } from "@/stores/studio/creature"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"

let creature
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  const ws = useStudioWorkspaceStore()
  ws.root = "/proj"
  ws.summary = {
    creatures: [{ name: "dev" }],
    modules: {
      outputs: [{ name: "log", source: "workspace", users: ["dev"] }],
      tools: [{ name: "echo", source: "workspace", users: [] }],
    },
  }
  ws.refresh = vi.fn(async () => ws.summary)
  creature = useStudioCreatureStore()
  creature.name = "dev"
  creature.saved = { config: { name: "dev" }, prompts: {} }
  creature.draft = { config: { name: "dev" }, prompts: {} }
  creature.load = vi.fn(async () => {})
})

describe("CreaturePool — this workspace", () => {
  it("lists the workspace's own modules of every kind with whether this creature loads them", async () => {
    const w = mount(CreaturePool)
    await flushPromises()
    const dot = (sel) => w.find(`${sel} span`).classes()
    expect(dot("[data-test='pool-own-outputs-log']")).toContain("bg-iolite")
    expect(dot("[data-test='pool-own-tools-echo']")).not.toContain("bg-iolite")
  })

  it("plugs or unplugs on disk and reloads the creature", async () => {
    const w = mount(CreaturePool)
    await flushPromises()
    await w.find("[data-test='pool-own-tools-echo'] button").trigger("click")
    await flushPromises()
    expect(api.moduleAPI.plug).toHaveBeenCalledWith("tools", "echo", ["dev"])
    expect(creature.load).toHaveBeenCalledWith("dev")
    await w.find("[data-test='pool-own-outputs-log'] button").trigger("click")
    await flushPromises()
    expect(api.moduleAPI.unplug).toHaveBeenCalledWith("outputs", "log", ["dev"])
  })

  it("asks to save first instead of writing over unsaved edits", async () => {
    creature.draft.config.description = "changed"
    const w = mount(CreaturePool)
    await flushPromises()
    await w.find("[data-test='pool-own-tools-echo'] button").trigger("click")
    await flushPromises()
    expect(messages.warning).toHaveBeenCalledWith("studioApp.pool.saveFirst")
    expect(api.moduleAPI.plug).not.toHaveBeenCalled()
  })
})
