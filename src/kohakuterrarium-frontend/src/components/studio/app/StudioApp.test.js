import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { inject } from "vue"

const api = vi.hoisted(() => ({
  workspaceAPI: { get: vi.fn(), open: vi.fn(), close: vi.fn() },
  starterAPI: { list: vi.fn(async () => []), preview: vi.fn() },
  packagesAPI: { list: vi.fn(async () => []) },
}))
vi.mock("@/utils/studio/api", () => api)
vi.mock("element-plus", () => ({ ElMessage: { error: vi.fn(), success: vi.fn() } }))
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (k, p) => (p ? `${k}:${JSON.stringify(p)}` : k) }),
}))
vi.mock("@/composables/useStudioNav", () => ({ STUDIO_NAV_INJECT_KEY: "kt.studioNav" }))
vi.mock("@/components/studio/pages/StudioCreaturePage.vue", () => ({
  default: {
    props: ["creatureNameProp"],
    setup() {
      return { nav: inject("kt.studioNav") }
    },
    template:
      "<button data-test='creature-page' :data-name='creatureNameProp' @click=\"nav.openModule('tools', 'echo')\" />",
  },
}))
vi.mock("@/components/studio/pages/StudioModulePage.vue", () => ({
  default: {
    props: ["moduleKindProp", "moduleNameProp"],
    setup() {
      return { nav: inject("kt.studioNav") }
    },
    template:
      "<button data-test='module-page' :data-kind='moduleKindProp' :data-name='moduleNameProp' @click='nav.openHome()' />",
  },
}))
vi.mock("@/components/studio/app/create/StudioCreate.vue", () => ({
  default: {
    props: ["kind", "starter", "mode"],
    template: "<div data-test='create' :data-kind='kind' :data-mode='mode' />",
  },
}))

import StudioApp from "./StudioApp.vue"
import { _resetStudioRouteForTests, goStudio, useStudioRoute } from "./useStudioRoute"

const SUMMARY = (root, extra = {}) => ({
  root,
  ref_prefix: null,
  is_project: false,
  creatures: [],
  modules: {},
  ...extra,
})
const conflict = () => Object.assign(new Error("no workspace"), { status: 409 })

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  _resetStudioRouteForTests()
  for (const fn of Object.values(api.workspaceAPI)) fn.mockReset()
})

describe("StudioApp", () => {
  it("opens the local project when nothing is open or remembered, then shows the overview", async () => {
    api.workspaceAPI.get.mockRejectedValue(conflict())
    api.workspaceAPI.open.mockResolvedValue(
      SUMMARY("/home/u/.kohakuterrarium/project", { ref_prefix: "@", is_project: true }),
    )
    const w = mount(StudioApp)
    expect(w.find("[data-test='studio-opening']").exists()).toBe(true)
    await flushPromises()
    expect(api.workspaceAPI.open).toHaveBeenCalledWith("@")
    expect(w.find("[data-test='studio-overview']").exists()).toBe(true)
  })

  it("keeps the workspace the server already has open", async () => {
    api.workspaceAPI.get.mockResolvedValue(SUMMARY("/work"))
    const w = mount(StudioApp)
    await flushPromises()
    expect(api.workspaceAPI.open).not.toHaveBeenCalled()
    expect(w.find("[data-test='studio-overview']").text()).toContain("/work")
  })

  it("falls back to the local project when the remembered folder is gone, and forgets it", async () => {
    localStorage.setItem("kt.studio.workspace", JSON.stringify("/gone"))
    localStorage.setItem("kt.studio.route", JSON.stringify({ view: "creature", name: "old" }))
    _resetStudioRouteForTests()
    api.workspaceAPI.get.mockRejectedValue(conflict())
    api.workspaceAPI.open.mockImplementation(async (p) => {
      if (p === "/gone") throw new Error("not found")
      return SUMMARY("/proj", { ref_prefix: "@", is_project: true })
    })
    const w = mount(StudioApp)
    await flushPromises()
    expect(api.workspaceAPI.open.mock.calls.map((c) => c[0])).toEqual(["/gone", "@"])
    expect(JSON.parse(localStorage.getItem("kt.studio.workspace"))).toBe("@")
    expect(w.find("[data-test='studio-overview']").exists()).toBe(true)
  })

  it("says why when even the local project cannot open, and retries", async () => {
    api.workspaceAPI.get.mockRejectedValue(conflict())
    api.workspaceAPI.open.mockRejectedValueOnce(new Error("disk full"))
    const w = mount(StudioApp)
    await flushPromises()
    expect(w.text()).toContain("disk full")
    api.workspaceAPI.open.mockResolvedValue(SUMMARY("/proj", { is_project: true, ref_prefix: "@" }))
    await w.find("[data-test='studio-retry']").trigger("click")
    await flushPromises()
    expect(w.find("[data-test='studio-overview']").exists()).toBe(true)
  })

  it("routes between the overview, editors and the create flow, and editors navigate through it", async () => {
    api.workspaceAPI.get.mockResolvedValue(SUMMARY("/work"))
    const w = mount(StudioApp)
    await flushPromises()
    goStudio({ view: "creature", name: "alpha" })
    await flushPromises()
    expect(w.find("[data-test='creature-page']").attributes("data-name")).toBe("alpha")
    await w.find("[data-test='creature-page']").trigger("click")
    expect(useStudioRoute().route.value).toEqual({ view: "module", kind: "tools", name: "echo" })
    expect(w.find("[data-test='module-page']").attributes("data-kind")).toBe("tools")
    await w.find("[data-test='module-page']").trigger("click")
    expect(w.find("[data-test='studio-overview']").exists()).toBe(true)
    goStudio({ view: "new", kind: "creatures", mode: "fork" })
    await flushPromises()
    expect(w.find("[data-test='create']").attributes("data-mode")).toBe("fork")
    expect(JSON.parse(localStorage.getItem("kt.studio.route"))).toEqual({
      view: "new",
      kind: "creatures",
      mode: "fork",
    })
  })

  it("normalizes routes that name nothing back to the overview", () => {
    goStudio({ view: "creature" })
    expect(useStudioRoute().route.value).toEqual({ view: "overview" })
    goStudio({ view: "module", kind: "tools" })
    expect(useStudioRoute().route.value).toEqual({ view: "overview" })
    goStudio({ view: "elsewhere" })
    expect(useStudioRoute().route.value).toEqual({ view: "overview" })
  })
})
