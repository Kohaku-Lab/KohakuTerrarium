import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  workspaceAPI: { get: vi.fn() },
  starterAPI: { list: vi.fn(), preview: vi.fn() },
  creatureAPI: { scaffold: vi.fn(async () => ({})) },
  moduleAPI: { scaffold: vi.fn(async () => ({})) },
  catalogAPI: { models: vi.fn(async () => [{ name: "fast", model: "gpt-x" }]) },
}))
vi.mock("@/utils/studio/api", () => api)
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (k, p) => (p ? `${k}:${JSON.stringify(p)}` : k) }),
}))
vi.mock("@/components/session-v2/add/ConfigPicker.vue", () => ({
  default: {
    props: ["modelValue", "kind"],
    emits: ["update:modelValue"],
    template:
      "<button data-test='picker' @click=\"$emit('update:modelValue', '@kt-biome/creatures/swe')\" />",
  },
}))

import StudioCreate from "./StudioCreate.vue"
import { _resetStudioRouteForTests, useStudioRoute } from "../useStudioRoute"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"

const STARTERS = {
  creatures: [
    { id: "blank", label: "Just talk", summary: "" },
    { id: "coder", label: "Work in a folder", summary: "" },
  ],
  tools: [
    { id: "blank", label: "Return a value", summary: "" },
    { id: "web_api", label: "Call a web API", summary: "" },
  ],
}

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  localStorage.clear()
  _resetStudioRouteForTests()
  for (const group of Object.values(api)) for (const fn of Object.values(group)) fn.mockClear?.()
  api.starterAPI.list.mockImplementation(async (kind) => STARTERS[kind] || [])
  api.starterAPI.preview.mockImplementation(async (body) => ({
    files: [{ path: `modules/${body.kind}/${body.name}.py`, content: `# ${body.id}` }],
    entry: {
      name: body.name,
      type: "custom",
      module: `@/modules/${body.kind}/${body.name}.py`,
      class: "T",
    },
  }))
  const ws = useStudioWorkspaceStore()
  ws.root = "/proj"
  ws.summary = {
    root: "/proj",
    creatures: [{ name: "dev" }, { name: "my-creature" }],
    modules: { tools: [{ name: "my_tool", source: "workspace" }] },
  }
  ws.refresh = vi.fn(async () => ws.summary)
})
afterEach(() => vi.useRealTimers())

async function settle() {
  await flushPromises()
  vi.advanceTimersByTime(300)
  await flushPromises()
}

describe("StudioCreate", () => {
  it("asks what to make when not told, then offers that kind's starters", async () => {
    const w = mount(StudioCreate, { props: { kind: null } })
    await settle()
    expect(w.find("[data-test='create-kinds']").exists()).toBe(true)
    expect(w.find("[data-test='create-preview']").exists()).toBe(false)
    await w.find("[data-test='create-kind-tools']").trigger("click")
    await settle()
    expect(api.starterAPI.list).toHaveBeenLastCalledWith("tools")
    expect(w.find("[data-test='create-starter-blank']").attributes("aria-checked")).toBe("true")
    await w.find("[data-test='create-change-kind']").trigger("click")
    expect(w.find("[data-test='create-kinds']").exists()).toBe(true)
  })

  it("previews a module from its starter, plugs it in on create and opens its editor", async () => {
    const w = mount(StudioCreate, { props: { kind: "tools", starter: "web_api" } })
    await settle()
    expect(w.find("[data-test='create-name']").element.value).toBe("my_tool_2")
    expect(api.starterAPI.preview).toHaveBeenLastCalledWith({
      kind: "tools",
      name: "my_tool_2",
      id: "web_api",
    })
    expect(w.find("[data-test='create-file-content']").text()).toBe("# web_api")
    expect(w.find("[data-test='create-wiring']").text()).toContain(
      "module: @/modules/tools/my_tool_2.py",
    )
    await w.find("[data-test='create-plug-dev']").setValue(true)
    await w.find("[data-test='create-submit']").trigger("click")
    await settle()
    expect(api.moduleAPI.scaffold).toHaveBeenCalledWith("tools", {
      name: "my_tool_2",
      template: "web_api",
      plug_into: ["dev"],
    })
    expect(useStudioRoute().route.value).toEqual({
      view: "module",
      kind: "tools",
      name: "my_tool_2",
    })
  })

  it("refuses a taken or unusable name before asking the server", async () => {
    const w = mount(StudioCreate, { props: { kind: "tools" } })
    await settle()
    await w.find("[data-test='create-name']").setValue("my_tool")
    await settle()
    expect(w.find("[data-test='create-name-error']").text()).toContain("studioApp.create.nameTaken")
    expect(w.find("[data-test='create-submit']").attributes("disabled")).toBeDefined()
    await w.find("[data-test='create-name']").setValue("my tool")
    await settle()
    expect(w.find("[data-test='create-name-error']").text()).toBe("studioApp.create.nameModule")
    expect(w.find("[data-test='create-file-content']").exists()).toBe(false)
    await w.find("[data-test='create-submit']").trigger("click")
    expect(api.moduleAPI.scaffold).not.toHaveBeenCalled()
  })

  it("seeds a creature from a starter with its purpose and model", async () => {
    const w = mount(StudioCreate, { props: { kind: "creatures", starter: "coder" } })
    await settle()
    expect(w.find("[data-test='create-name']").element.value).toBe("my-creature-2")
    await w.find("[data-test='create-purpose']").setValue("Keeps the docs tidy.")
    await w.find("[data-test='create-model']").setValue("fast")
    await settle()
    expect(api.starterAPI.preview).toHaveBeenLastCalledWith({
      kind: "creatures",
      name: "my-creature-2",
      purpose: "Keeps the docs tidy.",
      description: "",
      model: "fast",
      id: "coder",
    })
    await w.find("[data-test='create-submit']").trigger("click")
    await settle()
    expect(api.creatureAPI.scaffold).toHaveBeenCalledWith({
      name: "my-creature-2",
      purpose: "Keeps the docs tidy.",
      description: "",
      model: "fast",
      starter: "coder",
    })
    expect(useStudioRoute().route.value).toEqual({ view: "creature", name: "my-creature-2" })
  })

  it("extends or copies a creature once one is picked", async () => {
    const w = mount(StudioCreate, { props: { kind: "creatures", mode: "extend" } })
    await settle()
    expect(w.find("[data-test='create-submit']").attributes("disabled")).toBeDefined()
    expect(w.find("[data-test='create-preview']").text()).toContain("studioApp.create.pickSource")
    await w.find("[data-test='picker']").trigger("click")
    await settle()
    expect(api.starterAPI.preview.mock.lastCall[0].base_config).toBe("@kt-biome/creatures/swe")
    await w.find("[data-test='create-mode-fork']").trigger("click")
    await settle()
    expect(w.find("[data-test='create-purpose']").exists()).toBe(false)
    expect(w.find("[data-test='create-preview']").text()).toContain("studioApp.create.forkNote")
    await w.find("[data-test='create-submit']").trigger("click")
    await settle()
    expect(api.creatureAPI.scaffold).toHaveBeenCalledWith({
      name: "my-creature-2",
      fork_from: "@kt-biome/creatures/swe",
    })
  })

  it("shows the server's refusal and stays", async () => {
    api.moduleAPI.scaffold.mockRejectedValueOnce(new Error("my_tool_2 already exists"))
    const w = mount(StudioCreate, { props: { kind: "tools" } })
    await settle()
    await w.find("[data-test='create-submit']").trigger("click")
    await settle()
    expect(w.find("[data-test='create-error']").text()).toContain("already exists")
    expect(useStudioRoute().route.value).toEqual({ view: "overview" })
  })
})
