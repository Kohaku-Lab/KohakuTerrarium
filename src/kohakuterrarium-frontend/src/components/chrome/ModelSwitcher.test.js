import { createPinia, setActivePinia } from "pinia"
import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

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
  return {
    __modelInventoryTest: state,
    useModelInventory: () => state,
  }
})

vi.mock("@/components/chrome/instanceContext", async () => {
  const { ref } = await import("vue")
  const currentInstance = ref(null)
  return {
    __instanceContextTest: { currentInstance },
    useInstanceContext: () => ({ instance: currentInstance }),
  }
})

vi.mock("@/composables/useDensity", () => ({
  useDensity: () => ({ isCompact: { value: false } }),
}))

vi.mock("@/stores/chat", () => {
  const chat = {
    tabs: ["agent"],
    terrariumTarget: "agent",
    modelByTab: {},
    sessionInfo: { llmName: "codex/current", model: "codex/current" },
    setActiveTab: vi.fn(),
    openTab: vi.fn(),
  }
  return { __chatTest: chat, useChatStore: () => chat }
})

vi.mock("@/stores/hosts", async () => {
  const { reactive } = await import("vue")
  const hosts = reactive({ activeHostId: null })
  return { __hostsTest: hosts, useHostsStore: () => hosts }
})

vi.mock("@/stores/instances", () => {
  const instances = { current: null, list: [], fetchOne: vi.fn() }
  return { __instancesTest: instances, useInstancesStore: () => instances }
})

vi.mock("@/utils/api", () => {
  const switchCreatureModel = vi.fn()
  return {
    __apiTest: { switchCreatureModel },
    terrariumAPI: { switchCreatureModel },
  }
})

vi.mock("@/utils/layoutEvents", () => ({
  LAYOUT_EVENTS: { MODEL_CONFIG_OPEN: "model:config-open" },
  onLayoutEvent: () => () => {},
}))
vi.mock("vue-router", () => ({ useRoute: () => ({ params: {}, query: {} }) }))

import ModelSwitcher from "./ModelSwitcher.vue"
import { __instanceContextTest } from "@/components/chrome/instanceContext"
import { __modelInventoryTest } from "@/composables/useModelInventory"
import { __chatTest } from "@/stores/chat"
import { __hostsTest } from "@/stores/hosts"
import { __instancesTest } from "@/stores/instances"
import { __apiTest } from "@/utils/api"

function deferred() {
  let resolve
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function mountSwitcher() {
  return mount(ModelSwitcher, {
    props: { instanceId: "session-1" },
    global: {
      stubs: {
        ElDrawer: { template: '<div><slot v-if="$attrs.modelValue" /></div>' },
        ElInput: { template: "<div />" },
        ElButton: {
          emits: ["click"],
          template: "<button @click=\"$emit('click')\"><slot /></button>",
        },
        ElOption: true,
        ElSelect: true,
        ElIcon: { template: "<span><slot /></span>" },
        ArrowDown: true,
      },
    },
  })
}

function buttonByText(wrapper, text) {
  return wrapper.findAll("button").find((button) => button.text().trim() === text)
}

beforeEach(() => {
  vi.stubGlobal("useRoute", () => ({ params: {}, query: {} }))
  setActivePinia(createPinia())
  __modelInventoryTest.models.value = [
    { provider: "codex", name: "current", model: "current", available: true },
    { provider: "codex", name: "other", model: "other", available: true },
  ]
  __instanceContextTest.currentInstance.value = {
    id: "session-1",
    graph_id: "session-1",
    type: "creature",
    creatures: [{ name: "agent", llm_name: "codex/current", model: "current" }],
  }
  __chatTest.modelByTab = {}
  __chatTest.sessionInfo = { llmName: "codex/current", model: "codex/current" }
  __hostsTest.activeHostId = null
  vi.resetAllMocks()
  __modelInventoryTest.ensureLoaded.mockResolvedValue(__modelInventoryTest.models.value)
  __modelInventoryTest.revalidateIfStale.mockResolvedValue(__modelInventoryTest.models.value)
  __modelInventoryTest.refresh.mockResolvedValue(__modelInventoryTest.models.value)
  __instancesTest.fetchOne.mockResolvedValue(__instanceContextTest.currentInstance.value)
  __apiTest.switchCreatureModel.mockResolvedValue({ model: "codex/other" })
})

describe("ModelSwitcher inventory refresh", () => {
  it("preserves an in-progress valid selection when background revalidation finishes", async () => {
    const request = deferred()
    __modelInventoryTest.revalidateIfStale.mockReturnValueOnce(request.promise)
    const wrapper = mountSwitcher()
    await flushPromises()

    await wrapper.find(".model-pill").trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("other")
    await wrapper
      .findAll(".model-row")
      .find((button) => button.text().includes("other"))
      .trigger("click")
    request.resolve(__modelInventoryTest.models.value)
    await flushPromises()
    await buttonByText(wrapper, "Switch").trigger("click")
    await flushPromises()

    expect(__apiTest.switchCreatureModel).toHaveBeenCalledWith("session-1", "agent", "codex/other")
  })

  it("closes the open drawer when the active host changes", async () => {
    const wrapper = mountSwitcher()
    await flushPromises()

    await wrapper.find(".model-pill").trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("Refresh")
    __hostsTest.activeHostId = "host-a"
    await flushPromises()

    expect(wrapper.text()).not.toContain("Refresh")
  })

  it("falls back to the current model when explicit refresh removes the draft selection", async () => {
    __modelInventoryTest.refresh.mockImplementationOnce(async () => {
      __modelInventoryTest.models.value = [
        { provider: "codex", name: "current", model: "current", available: true },
      ]
      return __modelInventoryTest.models.value
    })
    const wrapper = mountSwitcher()
    await flushPromises()

    await wrapper.find(".model-pill").trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("other")
    await wrapper
      .findAll(".model-row")
      .find((button) => button.text().includes("other"))
      .trigger("click")
    await buttonByText(wrapper, "Refresh").trigger("click")
    await flushPromises()

    expect(buttonByText(wrapper, "Switch").attributes("disabled")).toBeDefined()
  })
})
