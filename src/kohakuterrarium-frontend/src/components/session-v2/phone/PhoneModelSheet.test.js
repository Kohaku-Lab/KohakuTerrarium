import { flushPromises } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/composables/useModelInventory", async () => {
  const { ref } = await import("vue")
  const state = {
    models: ref([]),
    initialLoading: ref(false),
    refreshing: ref(false),
    ensureLoaded: vi.fn(async () => {}),
    revalidateIfStale: vi.fn(async () => {}),
    refresh: vi.fn(async () => {}),
  }
  return { __inventory: state, useModelInventory: () => state }
})
vi.mock("@/utils/api", () => ({ terrariumAPI: { switchCreatureModel: vi.fn() } }))
vi.mock("element-plus", () => ({ ElMessage: { success: vi.fn(), error: vi.fn() } }))

import PhoneModelSheet from "./PhoneModelSheet.vue"
import { mountInSession, phoneChat } from "./phoneHost"
import { __inventory } from "@/composables/useModelInventory"
import { terrariumAPI } from "@/utils/api"

const MODELS = [
  {
    provider: "codex",
    name: "gpt-6",
    model: "gpt-6",
    available: true,
    is_default: true,
    variation_groups: { reasoning: { low: {}, high: {} } },
  },
  { provider: "codex", name: "gpt-5", model: "gpt-5", available: true },
  { provider: "openai", name: "mini", model: "gpt-mini", available: true },
]

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.clearAllMocks()
  __inventory.models.value = MODELS
})

const agentSelect = (w) => w.find("[data-test='phone-model-agent']")
const switchBtn = (w) => w.find("[data-test='phone-model-switch']")
const selector = (w) => w.find("footer code").text()

describe("PhoneModelSheet", () => {
  it("starts on the given agent and its current model, variation included", async () => {
    const { wrapper } = mountInSession(PhoneModelSheet, { props: { initialAgent: "lead" } })
    await flushPromises()
    expect(agentSelect(wrapper).element.value).toBe("lead")
    expect(wrapper.text()).toContain("Model for lead")
    expect(wrapper.find("[data-test='phone-model-gpt-6'] .i-carbon-checkmark").exists()).toBe(true)
    expect(wrapper.find("[data-test='phone-var-reasoning-high']").classes()).toContain(
      "font-medium",
    )
    expect(selector(wrapper)).toBe("codex/gpt-6@reasoning=high")
    expect(switchBtn(wrapper).attributes("disabled")).toBeDefined()
  })

  it("switches the chosen agent to the picked model and records it under both chat keys", async () => {
    terrariumAPI.switchCreatureModel.mockResolvedValue({ model: "openai/mini" })
    const chat = phoneChat()
    const { wrapper, ctx } = mountInSession(PhoneModelSheet, {
      props: { initialAgent: "lead" },
      chat,
    })
    ctx.openSheet("model", { agent: "lead" })
    await flushPromises()
    await wrapper.find("[data-test='phone-provider-openai']").trigger("click")
    expect(selector(wrapper)).toBe("openai/mini")
    await switchBtn(wrapper).trigger("click")
    await flushPromises()
    expect(terrariumAPI.switchCreatureModel).toHaveBeenCalledWith("g1", "lead", "openai/mini")
    expect(chat.modelByTab.root.llmName).toBe("openai/mini")
    expect(chat.modelByTab.lead.llmName).toBe("openai/mini")
    expect(ctx.sheet.value).toBe(null)
  })

  it("asks for an agent in a channel, and restarts the draft from the picked agent's model", async () => {
    const { wrapper } = mountInSession(PhoneModelSheet, {
      props: { initialAgent: "" },
      chat: phoneChat({ activeTab: "ch:tasks" }),
    })
    await flushPromises()
    expect(agentSelect(wrapper).element.value).toBe("")
    await wrapper.find("[data-test='phone-model-gpt-6']").trigger("click")
    expect(switchBtn(wrapper).attributes("disabled")).toBeDefined()
    await agentSelect(wrapper).setValue("worker")
    await flushPromises()
    expect(selector(wrapper)).toBe("codex/gpt-5")
    await wrapper.find("[data-test='phone-model-gpt-6']").trigger("click")
    await wrapper.find("[data-test='phone-var-reasoning-low']").trigger("click")
    expect(selector(wrapper)).toBe("codex/gpt-6@reasoning=low")
    await wrapper.find("[data-test='phone-var-reasoning-low']").trigger("click")
    expect(selector(wrapper)).toBe("codex/gpt-6")
    expect(switchBtn(wrapper).attributes("disabled")).toBeUndefined()
  })

  it("keeps the sheet open with the error when the switch fails", async () => {
    terrariumAPI.switchCreatureModel.mockRejectedValue({
      response: { data: { detail: "no such model" } },
    })
    const { wrapper, ctx } = mountInSession(PhoneModelSheet, { props: { initialAgent: "worker" } })
    ctx.openSheet("model", { agent: "worker" })
    await flushPromises()
    await wrapper.find("[data-test='phone-model-gpt-6']").trigger("click")
    await switchBtn(wrapper).trigger("click")
    await flushPromises()
    expect(wrapper.find("[role='alert']").text()).toContain("no such model")
    expect(ctx.sheet.value.kind).toBe("model")
  })

  it("filters providers and models by the search, moving off a provider the search hides", async () => {
    const { wrapper } = mountInSession(PhoneModelSheet, { props: { initialAgent: "lead" } })
    await flushPromises()
    await wrapper.find("[data-test='phone-model-search']").setValue("mini")
    await flushPromises()
    expect(
      wrapper.findAll("[data-test^='phone-provider-']").map((b) => b.attributes("data-test")),
    ).toEqual(["phone-provider-openai"])
    expect(selector(wrapper)).toBe("openai/mini")
  })
})
