import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  workspaceAPI: { get: vi.fn(), open: vi.fn() },
  packagesAPI: {
    list: vi.fn(async () => [
      { name: "kt-biome", editable: true },
      { name: "frozen", editable: false },
      { name: "", editable: true, local: true },
    ]),
  },
}))
vi.mock("@/utils/studio/api", () => api)
vi.mock("element-plus", () => ({ ElMessage: { error: vi.fn() } }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (k) => k }) }))
vi.mock("@/components/studio/common/FolderPickerDialog.vue", () => ({
  default: {
    props: ["modelValue"],
    emits: ["pick"],
    template: "<button v-if='modelValue' data-test='picker' @click=\"$emit('pick', '/picked')\" />",
  },
}))

vi.mock("@/components/shell/newSession/NewSessionDialog.vue", () => ({
  default: {
    props: ["initialConfig"],
    template: "<div data-test='run-dialog' :data-config='initialConfig' />",
  },
}))

import StudioRailNav from "./StudioRailNav.vue"
import { _resetStudioRouteForTests, useStudioRoute } from "../useStudioRoute"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"

const SUMMARY = {
  root: "/proj",
  ref_prefix: "@",
  is_project: true,
  creatures: [{ name: "dev" }],
  modules: {
    tools: [{ name: "echo", source: "workspace", users: [] }],
    plugins: [{ name: "x", source: "package" }],
  },
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  _resetStudioRouteForTests()
  api.workspaceAPI.open.mockReset()
  const ws = useStudioWorkspaceStore()
  ws.summary = SUMMARY
  ws.root = SUMMARY.root
  ws.recent = ["/proj", "/old/place"]
})

describe("StudioRailNav", () => {
  it("lists the workspace's creatures and own modules and moves Studio to them", async () => {
    const w = mount(StudioRailNav, { attachTo: document.body })
    const { route } = useStudioRoute()
    expect(w.find("[data-test='studio-rail-module-plugins-x']").exists()).toBe(false)
    await w.find("[data-test='studio-rail-creature-dev']").trigger("click")
    expect(route.value).toEqual({ view: "creature", name: "dev" })
    expect(w.find("[data-test='studio-rail-creature-dev']").classes()).toContain("font-medium")
    await w.find("[data-test='studio-rail-module-tools-echo']").trigger("click")
    expect(route.value).toEqual({ view: "module", kind: "tools", name: "echo" })
    await w.find("[data-test='studio-rail-new']").trigger("click")
    expect(route.value).toEqual({ view: "new" })
    await w.find("[data-test='studio-rail-new-creature']").trigger("click")
    expect(route.value).toEqual({ view: "new", kind: "creatures" })
    await w.find("[data-test='studio-rail-overview']").trigger("click")
    expect(route.value).toEqual({ view: "overview" })
    w.unmount()
  })

  it("switches to the local project, an editable package, a recent folder or a picked one", async () => {
    api.workspaceAPI.open.mockImplementation(async (p) => ({
      root: p,
      ref_prefix: null,
      is_project: p === "@",
      creatures: [],
      modules: {},
    }))
    const w = mount(StudioRailNav, { attachTo: document.body })
    await w.find("[data-test='studio-ws-switch']").trigger("click")
    await flushPromises()
    const menu = w.find("[data-test='studio-ws-menu']")
    expect(menu.find("[data-test='studio-ws-pkg-kt-biome']").exists()).toBe(true)
    expect(menu.find("[data-test='studio-ws-pkg-frozen']").exists()).toBe(false)
    expect(menu.text()).toContain("/old/place")
    expect(menu.text()).not.toContain("/proj")
    await menu.find("[data-test='studio-ws-pkg-kt-biome']").trigger("click")
    await flushPromises()
    expect(api.workspaceAPI.open).toHaveBeenLastCalledWith("@kt-biome")
    expect(w.find("[data-test='studio-ws-menu']").exists()).toBe(false)

    await w.find("[data-test='studio-ws-switch']").trigger("click")
    await w.find("[data-test='studio-ws-project']").trigger("click")
    await flushPromises()
    expect(api.workspaceAPI.open).toHaveBeenLastCalledWith("@")
    expect(JSON.parse(localStorage.getItem("kt.studio.workspace"))).toBe("@")

    await w.find("[data-test='studio-ws-switch']").trigger("click")
    await w.find("[data-test='studio-ws-folder']").trigger("click")
    await w.find("[data-test='picker']").trigger("click")
    await flushPromises()
    expect(api.workspaceAPI.open).toHaveBeenLastCalledWith("/picked")
    expect(JSON.parse(localStorage.getItem("kt.studio.workspace"))).toBe("/picked")
    w.unmount()
  })

  it("lists terrariums and offers to run one", async () => {
    useStudioWorkspaceStore().summary = {
      ...SUMMARY,
      terrariums: [{ name: "team", ref: "@/terrariums/team" }],
    }
    const w = mount(StudioRailNav, { attachTo: document.body })
    await w.find("[data-test='studio-rail-terrarium-team']").trigger("click")
    expect(w.find("[data-test='run-dialog']").attributes("data-config")).toBe("@/terrariums/team")
    w.unmount()
  })

  it("closes the switcher on an outside click", async () => {
    const w = mount(StudioRailNav, { attachTo: document.body })
    await w.find("[data-test='studio-ws-switch']").trigger("click")
    expect(w.find("[data-test='studio-ws-menu']").exists()).toBe(true)
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }))
    await flushPromises()
    expect(w.find("[data-test='studio-ws-menu']").exists()).toBe(false)
    w.unmount()
  })
})
