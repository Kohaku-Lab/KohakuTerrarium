import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { defineComponent } from "vue"

import ModulesPanel from "./ModulesPanel.vue"
import { moduleAPI } from "@/utils/api"

vi.mock("@/utils/api", () => ({
  moduleAPI: { list: vi.fn(), setOptions: vi.fn(), toggle: vi.fn() },
}))
vi.mock("@/stores/chat", () => ({ useChatStore: () => ({ terrariumTarget: null }) }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

const stubs = {
  ElTooltip: defineComponent({
    props: ["content"],
    template: '<span :data-tip="content"><slot /></span>',
  }),
  ElInput: true,
}
const tools = () => [
  {
    type: "tool",
    name: "lab_query",
    schema: {},
    options: {},
    enabled: true,
    origin: { kind: "mcp", name: "sandbox-lab", label: "mcp:sandbox-lab" },
  },
  {
    type: "tool",
    name: "info",
    schema: {},
    options: {},
    enabled: false,
    origin: { kind: "builtin", name: "builtin", label: "builtin" },
  },
]
const instance = { id: "s", graph_id: "g", creatures: [{ name: "alice" }] }
let wrapper
const row = (key) => wrapper.find(`[data-module='${key}']`)

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  moduleAPI.list.mockImplementation(async () => tools())
})
afterEach(() => wrapper?.unmount())

async function mountPanel() {
  wrapper = mount(ModulesPanel, { props: { instance }, global: { stubs } })
  await flushPromises()
}

it("shows origin badges, the origin hint and dims a tool that is off", async () => {
  await mountPanel()
  expect(moduleAPI.list).toHaveBeenCalledWith("g", "alice")
  const badge = row("tool:lab_query").find("[data-test='origin-badge']")
  expect(badge.text()).toBe("mcp:sandbox-lab")
  expect(badge.attributes("data-origin-kind")).toBe("mcp")
  expect(wrapper.find("[data-test='origin-hint']").exists()).toBe(true)
  expect(row("tool:info").classes()).toContain("opacity-60")
  expect(row("tool:lab_query").classes()).not.toContain("opacity-60")
})

it("toggles a tool, shows its warning and reloads the list", async () => {
  await mountPanel()
  moduleAPI.toggle.mockResolvedValue({ name: "lab_query", enabled: false, warning: "Stops." })
  await row("tool:lab_query").find("button").trigger("click")
  await flushPromises()
  expect(moduleAPI.toggle).toHaveBeenCalledWith("g", "alice", "tool", "lab_query")
  expect(moduleAPI.list).toHaveBeenCalledTimes(2)
  expect(wrapper.find("[data-test='toggle-warning']").text()).toBe("settings.modules.toggleWarning")
})

it("reverts the optimistic flip and explains a 404", async () => {
  await mountPanel()
  moduleAPI.toggle.mockRejectedValue({ response: { status: 404, data: { detail: "x" } } })
  await row("tool:lab_query").find("button").trigger("click")
  await flushPromises()
  expect(wrapper.find("[role='alert']").text()).toBe("settings.modules.toggleMissing")
  expect(row("tool:lab_query").classes()).not.toContain("opacity-60")
  expect(row("tool:lab_query").find("button").text()).toBe("On")
})
