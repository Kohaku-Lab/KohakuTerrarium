import { flushPromises, mount } from "@vue/test-utils"
import { computed, ref } from "vue"
import { describe, expect, it, vi } from "vitest"

import {
  createModelInventory,
  ModelSwitcher,
  MODEL_SWITCHER_CONTEXT,
} from "@kohakuterrarium/chat-ui"

const MODELS = [
  {
    provider: "codex",
    name: "gpt-6",
    model: "gpt-6",
    available: true,
    max_context: 272000,
    context_window: null,
    variation_groups: { reasoning: { low: {}, high: {} } },
  },
  {
    provider: "codex",
    name: "gpt-6-sol",
    model: "gpt-6-sol",
    available: true,
    max_context: 400000,
    context_window: 1000000,
  },
  {
    provider: "codex",
    name: "legacy",
    model: "legacy",
    available: true,
    max_context: 128000,
    variation_groups: { context: { "1m": { max_context: 1000000 } } },
  },
]

// The shared picker through its host contract, with the live context length
// and model window a host reports for the creature the pill tracks.
function makeContext({
  current = "codex/gpt-6@reasoning=high",
  maxContext = 300000,
  window = 400000,
} = {}) {
  const switchModel = vi.fn(async ({ selector }) => selector)
  const inventory = createModelInventory({
    getHostKey: () => `context-${Math.random()}`,
    fetchModels: async () => MODELS,
  })
  return {
    instance: ref({
      id: "session-1",
      graph_id: "session-1",
      type: "creature",
      creatures: [{ name: "agent" }],
    }),
    isTerrarium: computed(() => false),
    targetOptions: ref([]),
    selectedTarget: ref("agent"),
    currentModel: ref(current),
    currentMaxContext: ref(maxContext),
    currentContextWindow: ref(window),
    selectTarget: vi.fn(),
    switchModel,
    inventory,
    onHostChange: vi.fn(() => () => {}),
    onOpenRequest: vi.fn(() => () => {}),
  }
}

const ElInputStub = {
  props: ["modelValue", "placeholder", "size"],
  emits: ["update:modelValue"],
  template: `<input :value="modelValue" :placeholder="placeholder" @input="$emit('update:modelValue', $event.target.value)" />`,
}

async function openSwitcher(context) {
  const wrapper = mount(ModelSwitcher, {
    global: {
      provide: { [MODEL_SWITCHER_CONTEXT]: context },
      stubs: {
        ElDrawer: { template: '<div><slot v-if="$attrs.modelValue" /></div>' },
        ElInput: ElInputStub,
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
  await flushPromises()
  await wrapper.find(".model-pill").trigger("click")
  await flushPromises()
  return wrapper
}

const field = (w) => w.find('[data-test="model-context-input"]')
const selector = (w) => w.get("code").text()
const switchButton = (w) =>
  w.findAll("button").find((b) => ["Switch", "Save & switch"].includes(b.text().trim()))
const warning = (w) => w.find('[data-test="model-context-warning"]')
const presetRow = (w, name) => w.findAll(".model-row").find((row) => row.text().startsWith(name))

async function type(wrapper, value) {
  await field(wrapper).setValue(value)
  await flushPromises()
}

describe("model switcher context length", () => {
  it("prefills the effective length without sending it, so an untouched draft is no change", async () => {
    const wrapper = await openSwitcher(makeContext())
    expect(field(wrapper).element.value).toBe("300k")
    expect(selector(wrapper)).toBe("codex/gpt-6@reasoning=high")
    expect(switchButton(wrapper).attributes("disabled")).toBeDefined()
    expect(wrapper.get('[data-test="model-context-note"]').text()).toContain("Model window: 400k")
    expect(warning(wrapper).exists()).toBe(false)
  })

  it("adds context=<n> after other selections, warns above the window and still switches", async () => {
    const context = makeContext()
    const wrapper = await openSwitcher(context)
    await type(wrapper, "1.5m")
    expect(selector(wrapper)).toBe("codex/gpt-6@reasoning=high,context=1500000")
    expect(warning(wrapper).text()).toContain("400k")
    expect(switchButton(wrapper).attributes("disabled")).toBeUndefined()
    await switchButton(wrapper).trigger("click")
    await flushPromises()
    expect(context.switchModel).toHaveBeenCalledWith({
      target: "agent",
      selector: "codex/gpt-6@reasoning=high,context=1500000",
    })
  })

  it("builds context= alone when no variation is chosen, from a typed or a quick-picked length", async () => {
    const wrapper = await openSwitcher(makeContext({ current: "codex/gpt-6" }))
    await type(wrapper, "500k")
    expect(selector(wrapper)).toBe("codex/gpt-6@context=500000")
    await wrapper.get('[data-context-pick="200000"]').trigger("click")
    expect(field(wrapper).element.value).toBe("200k")
    expect(selector(wrapper)).toBe("codex/gpt-6@context=200000")
    expect(warning(wrapper).exists()).toBe(false)
  })

  it("starts from the current context= and drops it when the field is cleared", async () => {
    const context = makeContext({
      current: "codex/gpt-6@context=1m,reasoning=low",
      maxContext: 1000000,
    })
    const wrapper = await openSwitcher(context)
    expect(field(wrapper).element.value).toBe("1m")
    expect(selector(wrapper)).toBe("codex/gpt-6@reasoning=low,context=1000000")
    expect(switchButton(wrapper).attributes("disabled")).toBeDefined()
    expect(warning(wrapper).text()).toContain("400k")
    await type(wrapper, "")
    expect(selector(wrapper)).toBe("codex/gpt-6@reasoning=low")
    expect(warning(wrapper).exists()).toBe(false)
    await switchButton(wrapper).trigger("click")
    await flushPromises()
    expect(context.switchModel).toHaveBeenCalledWith({
      target: "agent",
      selector: "codex/gpt-6@reasoning=low",
    })
  })

  it("blocks a length it cannot read", async () => {
    const wrapper = await openSwitcher(makeContext())
    await type(wrapper, "lots")
    expect(selector(wrapper)).toBe("codex/gpt-6@reasoning=high")
    expect(wrapper.get('[role="alert"]').text()).toContain("500k")
    expect(switchButton(wrapper).attributes("disabled")).toBeDefined()
  })

  it("follows another preset's default and window while unpinned", async () => {
    const wrapper = await openSwitcher(makeContext())
    await presetRow(wrapper, "gpt-6-sol").trigger("click")
    await flushPromises()
    expect(field(wrapper).element.value).toBe("400k")
    expect(selector(wrapper)).toBe("codex/gpt-6-sol")
    expect(wrapper.get('[data-test="model-context-note"]').text()).toContain("Model window: 1M")
    expect(wrapper.find('[data-context-pick="1000000"]').text()).toBe("1M (model)")
    await type(wrapper, "2m")
    expect(warning(wrapper).text()).toContain("1M")
    await presetRow(wrapper, "gpt-6").trigger("click")
    await flushPromises()
    expect(field(wrapper).element.value).toBe("2m")
    expect(selector(wrapper)).toBe("codex/gpt-6@context=2000000")
  })

  it("leaves context= to a preset that has its own context group", async () => {
    const wrapper = await openSwitcher(makeContext({ current: "codex/legacy@context=1m" }))
    expect(field(wrapper).exists()).toBe(false)
    expect(selector(wrapper)).toBe("codex/legacy@context=1m")
    expect(wrapper.findAll(".variation-chip").some((chip) => chip.text() === "1m")).toBe(true)
  })
})
