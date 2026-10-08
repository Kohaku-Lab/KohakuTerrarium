import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  workspaceAPI: { get: vi.fn() },
  starterAPI: {
    list: vi.fn(async () => [
      { id: "blank", label: "Just talk", summary: "No tools." },
      { id: "coder", label: "Work in a folder", summary: "Edits files." },
    ]),
  },
}))
vi.mock("@/utils/studio/api", () => api)
vi.mock("element-plus", () => ({ ElMessage: { success: vi.fn(), error: vi.fn() } }))
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (k, p) => (p ? `${k}:${JSON.stringify(p)}` : k) }),
}))

vi.mock("@/components/shell/newSession/NewSessionDialog.vue", () => ({
  default: {
    props: ["mode", "initialConfig"],
    template: "<div data-test='run-dialog' :data-mode='mode' :data-config='initialConfig' />",
  },
}))

import StudioOverview from "./StudioOverview.vue"
import { MODULE_KINDS, wiringSnippet, workspaceLabel } from "./studioKinds"
import { _resetStudioRouteForTests, useStudioRoute } from "./useStudioRoute"
import { useStudioWorkspaceStore } from "@/stores/studio/workspace"

const SUMMARY = {
  root: "/home/u/.kohakuterrarium/project",
  ref_prefix: "@",
  is_project: true,
  creatures: [
    { name: "dev", description: "Writes code", ref: "@/creatures/dev", base_config: null },
    {
      name: "kid",
      description: "",
      ref: "@/creatures/kid",
      base_config: "@kt-biome/creatures/general",
    },
  ],
  modules: {
    tools: [
      { name: "echo", source: "workspace", users: ["dev"] },
      { name: "read", source: "builtin" },
    ],
    plugins: [{ name: "guard", source: "workspace", users: [] }],
  },
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  _resetStudioRouteForTests()
})

async function mountOverview(summary = SUMMARY) {
  const ws = useStudioWorkspaceStore()
  ws.summary = summary
  ws.root = summary.root
  const w = mount(StudioOverview)
  await flushPromises()
  return w
}

describe("StudioOverview", () => {
  it("names the workspace and its reference", async () => {
    const w = await mountOverview()
    expect(w.find("h1").text()).toBe("studioApp.ws.project")
    expect(w.find("[data-test='studio-ref']").text()).toBe("@")
  })

  it("offers every way to start something, each opening the create flow", async () => {
    const w = await mountOverview()
    const { route } = useStudioRoute()
    await w.find("[data-test='studio-starter-coder']").trigger("click")
    expect(route.value).toEqual({ view: "new", kind: "creatures", starter: "coder" })
    await w.find("[data-test='studio-fork']").trigger("click")
    expect(route.value).toEqual({ view: "new", kind: "creatures", mode: "fork" })
    await w.find("[data-test='studio-extend']").trigger("click")
    expect(route.value.mode).toBe("extend")
    for (const { kind } of MODULE_KINDS) {
      await w.find(`[data-test='studio-make-${kind}']`).trigger("click")
      expect(route.value).toEqual({ view: "new", kind })
    }
  })

  it("lists the workspace's creatures and only its own modules, with how each is wired", async () => {
    const w = await mountOverview()
    expect(w.find("[data-test='studio-creature-kid']").text()).toContain(
      "@kt-biome/creatures/general",
    )
    expect(w.find("[data-test='studio-module-tools-read']").exists()).toBe(false)
    expect(w.find("[data-test='studio-module-tools-echo']").text()).toContain(
      'studioApp.overview.usedBy:{"names":"dev"}',
    )
    expect(w.find("[data-test='studio-module-plugins-guard']").text()).toContain(
      "studioApp.overview.unused",
    )
    const { route } = useStudioRoute()
    await w.find("[data-test='studio-module-plugins-guard']").trigger("click")
    expect(route.value).toEqual({ view: "module", kind: "plugins", name: "guard" })
    await w.find("[data-test='studio-creature-dev']").trigger("click")
    expect(route.value).toEqual({ view: "creature", name: "dev" })
  })

  it("lists a package's manifest modules and runs its terrariums", async () => {
    const w = await mountOverview({
      root: "/pkgs/kt-biome",
      ref_prefix: "@kt-biome",
      is_project: false,
      creatures: [],
      terrariums: [
        {
          name: "swe_team",
          ref: "@kt-biome/terrariums/swe_team",
          description: "Review",
          creatures: 2,
        },
      ],
      modules: {
        plugins: [
          {
            name: "otel",
            source: "workspace-manifest",
            editable: true,
            path: "kt_biome/plugins/otel.py",
            users: ["general"],
          },
          { name: "foreign", source: "package:other", editable: false },
        ],
      },
    })
    expect(w.find("[data-test='studio-module-plugins-otel']").text()).toContain("general")
    expect(w.find("[data-test='studio-module-plugins-foreign']").exists()).toBe(false)
    expect(w.find("[data-test='studio-terrarium-swe_team']").text()).toContain("Review")
    expect(w.find("[data-test='run-dialog']").exists()).toBe(false)
    await w.find("[data-test='studio-run-terrarium-swe_team']").trigger("click")
    const dialog = w.find("[data-test='run-dialog']")
    expect([dialog.attributes("data-mode"), dialog.attributes("data-config")]).toEqual([
      "terrarium",
      "@kt-biome/terrariums/swe_team",
    ])
  })

  it("says what goes where when the workspace is empty", async () => {
    const w = await mountOverview({
      root: "/x/plain",
      ref_prefix: null,
      is_project: false,
      creatures: [],
      modules: {},
    })
    expect(w.find("h1").text()).toBe("plain")
    expect(w.find("[data-test='studio-ref']").exists()).toBe(false)
    expect(w.find("[data-test='studio-no-creatures']").exists()).toBe(true)
    expect(w.find("[data-test='studio-no-modules']").exists()).toBe(true)
  })
})

describe("studioKinds", () => {
  it("renders the config snippet each kind is plugged in with", () => {
    const entry = { type: "custom", module: "@/modules/x.py", class: "X" }
    expect(wiringSnippet("tools", "x", { name: "x", ...entry })).toBe(
      "tools:\n  - name: x\n    type: custom\n    module: @/modules/x.py\n    class: X",
    )
    expect(wiringSnippet("inputs", "x", entry)).toBe(
      "input:\n  type: custom\n  module: @/modules/x.py\n  class: X",
    )
    expect(wiringSnippet("outputs", "log", entry)).toBe(
      "output:\n  named_outputs:\n    log:\n      type: custom\n      module: @/modules/x.py\n      class: X",
    )
    expect(wiringSnippet("tools", "x", null)).toBe("")
    expect(wiringSnippet("tools", "x", { module: "C:\\a b\\x.py" })).toBe(
      'tools:\n  - module: "C:\\\\a b\\\\x.py"',
    )
  })

  it("labels a package workspace by its reference", () => {
    expect(workspaceLabel({ ref_prefix: "@kt-biome", root: "/p" }, (k) => k)).toBe("@kt-biome")
    expect(workspaceLabel(null, (k) => k)).toBe("")
  })
})
