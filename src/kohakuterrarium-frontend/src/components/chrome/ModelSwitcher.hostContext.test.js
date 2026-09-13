import { flushPromises, mount } from "@vue/test-utils"
import { computed, ref } from "vue"
import { describe, expect, it, vi } from "vitest"

import {
  createModelInventory,
  ModelSwitcher,
  MODEL_SWITCHER_CONTEXT,
} from "@kohakuterrarium/chat-ui"

const MODELS = [
  { provider: "codex", name: "current", model: "current", available: true },
  {
    provider: "codex",
    name: "other",
    model: "other",
    available: true,
    variation_groups: { effort: { high: {}, low: {} } },
  },
]

// The shared picker consumed through its host contract only: no router, no
// pinia, no store or instance-context mocks. This pins the production component
// both hosts render and the narrow seam the Extension binds.
function makeContext(overrides = {}) {
  const instance = ref({
    id: "session-1",
    graph_id: "session-1",
    type: "terrarium",
    has_root: true,
    creatures: [{ name: "root", is_root: true }, { name: "beta" }],
  })
  const inventory = createModelInventory({
    getHostKey: () => overrides.hostKey?.value || "host-a",
    fetchModels: async () => overrides.models || MODELS,
  })
  return {
    instance,
    isTerrarium: computed(() => instance.value?.type === "terrarium"),
    targetOptions: ref([
      { value: "root", label: "root" },
      { value: "beta", label: "beta" },
    ]),
    selectedTarget: ref("beta"),
    currentModel: ref("codex/current"),
    selectTarget: vi.fn(),
    switchModel: overrides.switchModel || vi.fn(async () => "codex/other"),
    inventory,
    onHostChange: vi.fn(() => () => {}),
    onOpenRequest: vi.fn(() => () => {}),
  }
}

const ElSelectStub = {
  name: "ElSelect",
  props: ["modelValue"],
  emits: ["change"],
  template: "<div><slot /></div>",
}

function mountShared(context) {
  return mount(ModelSwitcher, {
    global: {
      provide: { [MODEL_SWITCHER_CONTEXT]: context },
      stubs: {
        ElDrawer: { template: '<div><slot v-if="$attrs.modelValue" /></div>' },
        ElInput: true,
        ElButton: {
          emits: ["click"],
          template: "<button @click=\"$emit('click')\"><slot /></button>",
        },
        ElOption: true,
        ElSelect: ElSelectStub,
        ElIcon: { template: "<span><slot /></span>" },
        ArrowDown: true,
      },
    },
  })
}

function buttonByText(wrapper, text) {
  return wrapper.findAll("button").find((button) => button.text().trim() === text)
}

async function openDrawer(wrapper) {
  await wrapper.find(".model-pill").trigger("click")
  await flushPromises()
}

describe("shared ModelSwitcher host context", () => {
  it("renders the pill, loads the host-keyed inventory and switches through the context", async () => {
    const context = makeContext()
    const wrapper = mountShared(context)
    await flushPromises()
    await openDrawer(wrapper)
    expect(wrapper.text()).toContain("other")

    await wrapper
      .findAll(".model-row")
      .find((button) => button.text().includes("other"))
      .trigger("click")
    await buttonByText(wrapper, "Switch").trigger("click")
    await flushPromises()

    expect(context.switchModel).toHaveBeenCalledWith({ target: "beta", selector: "codex/other" })
  })

  it("exports the canonical variation selector to the host switch action", async () => {
    const context = makeContext()
    const wrapper = mountShared(context)
    await flushPromises()
    await openDrawer(wrapper)
    await wrapper
      .findAll(".model-row")
      .find((button) => button.text().includes("other"))
      .trigger("click")
    await wrapper
      .findAll(".variation-chip")
      .find((chip) => chip.text() === "high")
      .trigger("click")
    await buttonByText(wrapper, "Switch").trigger("click")
    await flushPromises()

    expect(context.switchModel).toHaveBeenCalledWith({
      target: "beta",
      selector: "codex/other@effort=high",
    })
  })

  it("routes target selection through the context instead of a local tab change", async () => {
    const context = makeContext()
    const wrapper = mountShared(context)
    await wrapper.vm.$nextTick()
    await wrapper.findComponent(ElSelectStub).vm.$emit("change", "root")
    expect(context.selectTarget).toHaveBeenCalledWith("root")
  })

  it("surfaces a refused switch as an error with no false success", async () => {
    const context = makeContext({
      switchModel: vi.fn(async () => {
        throw Error("selected creature changed")
      }),
    })
    const wrapper = mountShared(context)
    await flushPromises()
    await openDrawer(wrapper)
    await wrapper
      .findAll(".model-row")
      .find((button) => button.text().includes("other"))
      .trigger("click")
    await buttonByText(wrapper, "Switch").trigger("click")
    await flushPromises()

    // The drawer stays open (no success path closed it) and the failure is shown.
    expect(wrapper.text()).toContain("Refresh")
    expect(wrapper.text()).not.toMatch(/Switched/)
  })
})
