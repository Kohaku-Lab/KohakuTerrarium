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
import { mountInSession, PHONE_INSTANCE, phoneChat } from "./phoneHost"
import { __inventory } from "@/composables/useModelInventory"
import { useLocaleStore } from "@/stores/locale"
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
  {
    provider: "codex",
    name: "gpt-5",
    model: "gpt-5",
    available: true,
    max_context: 272000,
    context_window: 400000,
  },
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

  it("sets the agent's context length as a number, warning above the window but still switching", async () => {
    terrariumAPI.switchCreatureModel.mockImplementation(async (_sid, _name, model) => ({ model }))
    const instance = {
      ...PHONE_INSTANCE,
      creatures: PHONE_INSTANCE.creatures.map((c) =>
        c.name === "worker" ? { ...c, max_context: 300000, context_window: 400000 } : c,
      ),
    }
    const { wrapper, ctx } = mountInSession(PhoneModelSheet, {
      props: { initialAgent: "worker" },
      instance,
    })
    ctx.openSheet("model", { agent: "worker" })
    await flushPromises()
    const input = wrapper.find("[data-test='phone-model-context-input']")
    expect(input.element.value).toBe("300k")
    expect(selector(wrapper)).toBe("codex/gpt-5")
    expect(switchBtn(wrapper).attributes("disabled")).toBeDefined()

    await input.setValue("500k")
    expect(selector(wrapper)).toBe("codex/gpt-5@context=500000")
    expect(wrapper.find("[data-test='phone-model-context-warning']").text()).toContain("400k")
    expect(switchBtn(wrapper).attributes("disabled")).toBeUndefined()

    await wrapper.find("[data-test='phone-context-200000']").trigger("click")
    expect(selector(wrapper)).toBe("codex/gpt-5@context=200000")
    expect(wrapper.find("[data-test='phone-model-context-warning']").exists()).toBe(false)

    await wrapper.find("[data-test='phone-model-gpt-6']").trigger("click")
    await wrapper.find("[data-test='phone-var-reasoning-low']").trigger("click")
    expect(selector(wrapper)).toBe("codex/gpt-6@reasoning=low,context=200000")

    await input.setValue("1.5m")
    await switchBtn(wrapper).trigger("click")
    await flushPromises()
    expect(terrariumAPI.switchCreatureModel).toHaveBeenCalledWith(
      "g1",
      "worker",
      "codex/gpt-6@reasoning=low,context=1500000",
    )
  })

  it("starts from the current context= and clears it back to the preset default", async () => {
    const instance = {
      ...PHONE_INSTANCE,
      creatures: PHONE_INSTANCE.creatures.map((c) =>
        c.name === "worker" ? { ...c, llm_name: "codex/gpt-5@context=1m" } : c,
      ),
    }
    const { wrapper } = mountInSession(PhoneModelSheet, {
      props: { initialAgent: "worker" },
      instance,
    })
    await flushPromises()
    const input = wrapper.find("[data-test='phone-model-context-input']")
    expect(input.element.value).toBe("1m")
    expect(selector(wrapper)).toBe("codex/gpt-5@context=1000000")
    expect(switchBtn(wrapper).attributes("disabled")).toBeDefined()
    await input.setValue("")
    expect(selector(wrapper)).toBe("codex/gpt-5")
    expect(input.attributes("placeholder")).toContain("272k")
    expect(switchBtn(wrapper).attributes("disabled")).toBeUndefined()
    await input.setValue("lots")
    expect(wrapper.find("[role='alert']").exists()).toBe(true)
    expect(switchBtn(wrapper).attributes("disabled")).toBeDefined()
  })

  it("separates the context note's sentences the way each locale writes them", async () => {
    const instance = {
      ...PHONE_INSTANCE,
      creatures: PHONE_INSTANCE.creatures.map((c) =>
        c.name === "worker" ? { ...c, llm_name: "codex/gpt-5" } : c,
      ),
    }
    const { wrapper } = mountInSession(PhoneModelSheet, {
      props: { initialAgent: "worker" },
      instance,
    })
    await flushPromises()
    const note = () => wrapper.find("[data-test='phone-model-context'] p").text()
    expect(note()).toBe(
      "Model window: 400k. Preset default; type a length to set it for this session.",
    )
    useLocaleStore().setLocale("zh-TW")
    await flushPromises()
    expect(note()).toBe("模型視窗：400k。使用預設值；輸入長度可為此工作階段另行設定。")
    useLocaleStore().setLocale("zh-CN")
    await flushPromises()
    expect(note()).toBe("模型窗口：400k。使用预设默认值；输入长度可为此会话单独设置。")
    useLocaleStore().setLocale("en")
    await wrapper.find("[data-test='phone-model-context-input']").setValue("300k")
    expect(note()).toBe("Model window: 400k.")
  })
})
