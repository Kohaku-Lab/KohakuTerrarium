import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, h, reactive, ref } from "vue"

vi.mock("@/composables/useModelInventory", async () => {
  const { ref } = await import("vue")
  const state = {
    models: ref([]),
    initialLoading: ref(false),
    refreshing: ref(false),
    ensureLoaded: vi.fn(),
    revalidateIfStale: vi.fn(),
    refresh: vi.fn(),
  }
  return { __inventory: state, useModelInventory: () => state }
})
vi.mock("@/composables/useDensity", () => ({ useDensity: () => ({ isCompact: { value: false } }) }))
vi.mock("@/stores/hosts", async () => {
  const { reactive } = await import("vue")
  const hosts = reactive({ activeHostId: null })
  return { useHostsStore: () => hosts }
})
vi.mock("@/stores/locale", () => ({ useLocaleStore: () => ({ locale: "en" }) }))
vi.mock("@/utils/api", () => {
  const switchCreatureModel = vi.fn()
  return { __api: { switchCreatureModel }, terrariumAPI: { switchCreatureModel } }
})
vi.mock("@/utils/layoutEvents", () => ({
  LAYOUT_EVENTS: { MODEL_CONFIG_OPEN: "model:config-open" },
  onLayoutEvent: () => () => {},
}))

import ComposerModelPicker from "./ComposerModelPicker.vue"
import { createSessionV2, provideSessionV2 } from "@/components/session-v2/model/sessionContext"
import { __inventory } from "@/composables/useModelInventory"
import { __api } from "@/utils/api"

const STUBS = {
  ElDrawer: { template: '<div><slot v-if="$attrs.modelValue" /></div>' },
  ElInput: { template: "<div />" },
  ElButton: { emits: ["click"], template: "<button @click=\"$emit('click')\"><slot /></button>" },
  ElOption: true,
  ElSelect: true,
  ElIcon: { template: "<span><slot /></span>" },
  ArrowDown: true,
}

function mountPicker(chat, instance) {
  const refresh = vi.fn()
  const Host = defineComponent({
    setup() {
      provideSessionV2(
        createSessionV2({ instance: ref(instance), instanceId: ref("g1"), chat, refresh }),
      )
      return () => h(ComposerModelPicker)
    },
  })
  return { wrapper: mount(Host, { global: { stubs: STUBS } }), refresh }
}

const INSTANCE = {
  id: "g1",
  graph_id: "g1",
  creatures: [
    { name: "boss", llm_name: "codex/current", model: "current" },
    { name: "worker", llm_name: "codex/other", model: "other" },
  ],
}

const pill = (wrapper) => wrapper.find(".model-pill")

beforeEach(() => {
  vi.resetAllMocks()
  __inventory.models.value = [
    { provider: "codex", name: "current", model: "current", available: true },
    { provider: "codex", name: "other", model: "other", available: true },
  ]
  __inventory.ensureLoaded.mockResolvedValue(__inventory.models.value)
  __inventory.revalidateIfStale.mockResolvedValue(__inventory.models.value)
  __api.switchCreatureModel.mockResolvedValue({ model: "codex/other" })
})

describe("ComposerModelPicker", () => {
  it("shows the open conversation's model and follows tab switches", async () => {
    const chat = reactive({ activeTab: "worker", _rootSourceName: null, modelByTab: {} })
    const { wrapper } = mountPicker(chat, INSTANCE)
    await flushPromises()
    expect(pill(wrapper).text()).toContain("codex/other")

    chat.activeTab = "boss"
    await flushPromises()
    expect(pill(wrapper).text()).toContain("codex/current")

    chat.modelByTab.boss = { llmName: "codex/live@reasoning=high" }
    await flushPromises()
    expect(pill(wrapper).text()).toContain("codex/live")
    expect(pill(wrapper).text()).toContain("reasoning=high")
  })

  it("is disabled in a channel conversation and never switches", async () => {
    const chat = reactive({ activeTab: "ch:general", _rootSourceName: null, modelByTab: {} })
    const { wrapper } = mountPicker(chat, INSTANCE)
    await flushPromises()
    expect(pill(wrapper).attributes("disabled")).toBeDefined()
    expect(wrapper.find("[data-test=v2-composer-model]").attributes("title")).toContain("channel")
  })

  it("switches the privileged node behind the root tab by its creature name", async () => {
    const chat = reactive({ activeTab: "root", _rootSourceName: "boss", modelByTab: {} })
    const { wrapper, refresh } = mountPicker(chat, INSTANCE)
    await flushPromises()
    expect(pill(wrapper).text()).toContain("codex/current")

    await pill(wrapper).trigger("click")
    await wrapper
      .findAll(".model-row")
      .find((row) => row.text().includes("other"))
      .trigger("click")
    await wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Switch")
      .trigger("click")
    await flushPromises()

    expect(__api.switchCreatureModel).toHaveBeenCalledWith("g1", "boss", "codex/other")
    expect(chat.modelByTab.root.llmName).toBe("codex/other")
    expect(chat.modelByTab.boss.llmName).toBe("codex/other")
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(pill(wrapper).text()).toContain("codex/other")
  })
})
