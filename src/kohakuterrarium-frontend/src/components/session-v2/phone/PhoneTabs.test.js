import { flushPromises } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/composables/useDensity", () => ({
  useDensity: () => ({ isCompact: { value: true }, isExpansive: { value: false } }),
}))
vi.mock("@/utils/api", () => ({
  configAPI: { getModels: vi.fn(async () => []) },
  terrariumAPI: { startCreature: vi.fn(), stopCreature: vi.fn(), switchCreatureModel: vi.fn() },
}))
vi.mock("@/components/session-v2/settings/EnvSection.vue", () => ({
  default: { template: "<div data-test='env-section' />" },
}))
vi.mock("@/components/session-v2/settings/CostSection.vue", () => ({
  default: { template: "<div />" },
}))
vi.mock("@/components/session-v2/settings/TriggersSection.vue", () => ({
  default: { template: "<div />" },
}))
vi.mock("@/components/session-v2/settings/WorkspaceSection.vue", () => ({
  default: { template: "<div />" },
}))
vi.mock("@/components/session-v2/settings/ExtensionsSection.vue", () => ({
  default: { template: "<div />" },
}))
import { mountInSession } from "./phoneHost"
import TabSwitcher from "@/components/session-v2/bar/TabSwitcher.vue"
import AgentsTable from "@/components/session-v2/status/AgentsTable.vue"
import SettingsTab from "@/components/session-v2/settings/SettingsTab.vue"

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe("phone Settings", () => {
  it("lists the sections and opens one as a page; the model page opens a creature's model sheet", async () => {
    const { wrapper, ctx } = mountInSession(SettingsTab, { tab: "settings" })
    expect(wrapper.findAll("[data-test^='v2-settings-nav-']")).toHaveLength(6)
    await wrapper.find("[data-test='v2-settings-nav-env']").trigger("click")
    expect(wrapper.find("[data-test='env-section']").exists()).toBe(true)
    await wrapper.find("[data-test='phone-page-back']").trigger("click")
    await wrapper.find("[data-test='v2-settings-nav-model']").trigger("click")
    await flushPromises()
    const row = wrapper.find("[data-test='v2-settings-model-worker']")
    expect(row.text()).toContain("codex/gpt-5")
    expect(wrapper.find("[data-test='v2-settings-models-apply']").exists()).toBe(false)
    await row.trigger("click")
    expect(ctx.sheet.value).toEqual({ kind: "model", payload: { agent: "worker" } })
  })
})

describe("phone tab bar", () => {
  it("has every session tab but Workspace", () => {
    const { wrapper } = mountInSession(TabSwitcher, { props: { bottom: true } })
    expect(wrapper.findAll("[role='tab']").map((b) => b.attributes("data-test"))).toEqual([
      "v2-tab-chat",
      "v2-tab-graph",
      "v2-tab-status",
      "v2-tab-debug",
      "v2-tab-settings",
    ])
  })
})

describe("phone Status agents", () => {
  it("lists each agent as a row that opens its agent sheet", async () => {
    const { wrapper, ctx } = mountInSession(AgentsTable, { tab: "status" })
    expect(wrapper.find("table").exists()).toBe(false)
    const row = wrapper.find("[data-test='status-agent-lead']")
    expect(row.text()).toContain("codex/gpt-6@reasoning=high")
    await row.trigger("click")
    expect(ctx.sheet.value).toEqual({ kind: "agent", payload: { name: "lead" } })
  })
})
