import { flushPromises } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/composables/useDensity", async () => {
  const { ref } = await import("vue")
  return { useDensity: () => ({ isCompact: ref(false), isExpansive: ref(false) }) }
})
vi.mock("@/utils/api", () => ({
  moduleAPI: { list: vi.fn(async () => []) },
  moduleDefaultsAPI: { list: vi.fn(async () => []) },
  configAPI: { getModels: vi.fn(async () => [{ provider: "codex", name: "gpt-5" }]) },
  terrariumAPI: {
    switchCreatureModel: vi.fn(),
    listTriggers: vi.fn(async () => [
      { trigger_id: "t-1", trigger_type: "timer", running: true, created_at: 0 },
    ]),
  },
  registryAPI: {
    listLocal: vi.fn(async () => ({ tool: [{ name: "grep", description: "Search files" }] })),
  },
}))
vi.mock("@/components/session-v2/settings/EnvSection.vue", () => ({
  default: { template: "<div data-test='env-section' />" },
}))
vi.mock("@/components/session-v2/settings/CostSection.vue", () => ({
  default: { template: "<div />" },
}))
vi.mock("@/components/session-v2/settings/WorkspaceSection.vue", () => ({
  default: { template: "<div />" },
}))
import { mountInSession } from "../phone/phoneHost"
import SettingsTab from "@/components/session-v2/settings/SettingsTab.vue"

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe("desktop Settings tab", () => {
  it("lists the sections in groups like the global Settings page and remembers the chosen one", async () => {
    const { wrapper } = mountInSession(SettingsTab, { tab: "settings" })
    await flushPromises()

    expect(wrapper.find("[data-settings-layout]").exists()).toBe(true)
    expect(wrapper.find("[data-settings-search]").exists()).toBe(true)
    const ids = wrapper
      .findAll("[data-settings-nav] [data-section]")
      .map((b) => b.attributes("data-section"))
    expect(ids).toEqual(["model", "modules", "workspace", "env", "triggers", "cost", "extensions"])
    expect(wrapper.find("[data-section='model']").attributes("aria-current")).toBe("page")

    await wrapper.find("[data-section='env']").trigger("click")
    await flushPromises()
    expect(wrapper.find("[data-test='env-section']").exists()).toBe(true)
    expect(wrapper.find("[data-section='env']").attributes("aria-current")).toBe("page")
    expect(localStorage.getItem("kt.v2.settings.section")).toBe("env")
  })

  it("opens on the section chosen last time", async () => {
    localStorage.setItem("kt.v2.settings.section", "env")
    const { wrapper } = mountInSession(SettingsTab, { tab: "settings" })
    await flushPromises()
    expect(wrapper.find("[data-test='env-section']").exists()).toBe(true)
  })

  it("shows each creature's model as a settings row", async () => {
    const { wrapper } = mountInSession(SettingsTab, { tab: "settings" })
    await flushPromises()
    const rows = wrapper.find("[data-test='v2-settings-models']")
    expect(rows.exists()).toBe(true)
    expect(rows.text()).toContain("worker")
    expect(rows.text()).toContain("lead")
    expect(
      wrapper.find("[data-test='v2-settings-models-apply']").attributes("disabled"),
    ).toBeDefined()
  })

  it("shows triggers and packages as settings rows", async () => {
    const { wrapper } = mountInSession(SettingsTab, { tab: "settings" })
    await flushPromises()
    await wrapper.find("[data-section='triggers']").trigger("click")
    await flushPromises()
    expect(wrapper.find("[data-test='v2-settings-triggers']").text()).toContain("timer")
    await wrapper.find("[data-section='extensions']").trigger("click")
    await flushPromises()
    const packages = wrapper.find("[data-test='v2-settings-extensions']")
    expect(packages.text()).toContain("grep")
    expect(packages.text()).toContain("Search files")
  })
})
