import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

const list = vi.hoisted(() => vi.fn())
vi.mock("@/utils/api", () => ({ extensionsAPI: { list } }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

import LibraryModules from "./LibraryModules.vue"

const MODULES = [
  {
    kind: "plugin",
    name: "checkpoint",
    package: "kt-biome",
    package_version: "3.0.0",
    module: "kt_biome.plugins.checkpoint",
    description: "Snapshot the workspace",
    editable: true,
  },
  {
    kind: "io",
    name: "discord_input",
    package: "kt-biome",
    package_version: "3.0.0",
    module: "kt_biome.io.discord",
    description: "Receive Discord messages",
  },
  { kind: "tool", name: "grep_plus", package: "kt-extra", description: "" },
]

beforeEach(() => {
  list.mockReset().mockResolvedValue(MODULES)
})

describe("LibraryModules", () => {
  it("lists every module, filters by the search and a kind chip, and reports the count", async () => {
    const w = mount(LibraryModules, { props: { query: "" } })
    await flushPromises()
    expect(w.emitted("count")).toEqual([[3]])
    expect(w.findAll("li")).toHaveLength(3)
    expect(w.find('[data-test="module-checkpoint"]').text()).toContain("kt-biome@3.0.0")
    await w.setProps({ query: "discord" })
    expect(w.findAll("li").map((li) => li.attributes("data-test"))).toEqual([
      "module-discord_input",
    ])
    await w.setProps({ query: "kt_biome.plugins" })
    expect(w.findAll("li").map((li) => li.attributes("data-test"))).toEqual(["module-checkpoint"])
    await w.setProps({ query: "" })
    await w.find('[data-test="module-kind-tool"]').trigger("click")
    expect(w.findAll("li").map((li) => li.attributes("data-test"))).toEqual(["module-grep_plus"])
    await w.setProps({ query: "nothing-like-this" })
    expect(w.find('[data-test="modules-empty"]').text()).toBe("lab.library.noModuleMatch")
  })

  it("shows a load failure, recovers on reload, and drops an older reply", async () => {
    list.mockRejectedValueOnce(new Error("packages unreadable"))
    const w = mount(LibraryModules)
    await flushPromises()
    expect(w.find('[role="alert"]').text()).toBe("packages unreadable")
    let resolveOld
    list
      .mockReturnValueOnce(new Promise((resolve) => (resolveOld = resolve)))
      .mockResolvedValueOnce([MODULES[2]])
    w.vm.load()
    await w.vm.load()
    resolveOld(MODULES)
    await flushPromises()
    expect(w.findAll("li").map((li) => li.attributes("data-test"))).toEqual(["module-grep_plus"])
  })
})
