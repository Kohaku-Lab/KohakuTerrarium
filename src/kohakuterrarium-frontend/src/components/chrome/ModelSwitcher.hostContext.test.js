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

function mountShared(context, options = {}) {
  return mount(ModelSwitcher, {
    ...options,
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
  it("keeps long catalogs in their own scroll area and lets the last model be selected", async () => {
    const context = makeContext({ hostKey: ref("long-catalog") })
    const rows = Array.from({ length: 65 }, (_, i) => {
      const id = `model-${i + 1}`
      return { id, name: `Model ${i + 1}`, preset: { name: id, model: id, provider: "codex" } }
    })
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: [{ name: "codex", available: true, model_discovery: true }],
      }),
      discoverModels: async () => ({ models: rows }),
    }
    const wrapper = mountShared(context)
    await openDrawer(wrapper)
    const list = wrapper.get('[role="tabpanel"][aria-label="Provider models"]')
    expect(list.findAll(".model-row")).toHaveLength(50)
    expect(list.element.contains(buttonByText(wrapper, "Saved presets").element)).toBe(false)
    await buttonByText(list, "Show more models (15)").trigger("click")
    const last = list.findAll(".model-row").at(-1)
    expect(last.text()).toContain("Model 65")
    await last.trigger("click")
    expect(wrapper.get("code").text()).toBe("codex/model-65")
    await buttonByText(wrapper, "Saved presets").trigger("click")
    expect(
      wrapper.get('[role="tabpanel"][aria-label="Saved presets"]').findAll(".model-row"),
    ).toHaveLength(2)
    wrapper.unmount()
  })

  it("lists provider model names first, keeps saved presets separate and creates only on switch", async () => {
    const context = makeContext({
      hostKey: ref("catalog-host"),
      models: [
        { provider: "deepseek", name: "flash-fast", model: "deepseek-v4-flash", available: true },
        { provider: "deepseek", name: "flash-think", model: "deepseek-v4-flash", available: true },
      ],
    })
    context.currentModel.value = "deepseek/flash-fast"
    const preset = {
      provider: "deepseek",
      name: "deepseek-flash",
      model: "deepseek-flash",
      max_context: 1048576,
    }
    const api = {
      getBackends: vi.fn(async () => ({
        backends: [{ name: "deepseek", available: true, model_discovery: true }],
      })),
      discoverModels: vi.fn(async () => ({
        models: [{ id: "deepseek-flash", name: "DeepSeek-V4.1-Flash", preset }],
      })),
      saveProfile: vi.fn(async () => ({})),
    }
    context.inventory.catalog = api
    const wrapper = mountShared(context)
    await flushPromises()
    expect(api.discoverModels).not.toHaveBeenCalled()
    await openDrawer(wrapper)
    expect(wrapper.findAll(".model-row").map((r) => r.text())).toEqual([
      "DeepSeek-V4.1-Flashdeepseek-flash",
    ])
    await buttonByText(wrapper, "Saved presets").trigger("click")
    expect(wrapper.findAll(".model-row").map((r) => r.text())).toEqual([
      "flash-fastdeepseek-v4-flash",
      "flash-thinkdeepseek-v4-flash",
    ])
    await buttonByText(wrapper, "Provider models").trigger("click")
    await flushPromises()
    await wrapper.find(".model-row").trigger("click")
    expect(api.saveProfile).not.toHaveBeenCalled()
    await buttonByText(wrapper, "Save & switch").trigger("click")
    await flushPromises()
    expect(api.saveProfile).toHaveBeenCalledWith({ ...preset, create_only: true })
    expect(context.switchModel).toHaveBeenCalledWith({
      target: "beta",
      selector: "deepseek/deepseek-flash",
    })
  })

  it("refreshes only the selected provider, drops stale results and keeps presets usable offline", async () => {
    const context = makeContext({ hostKey: ref("catalog-race") })
    const refreshPresets = vi.spyOn(context.inventory, "refresh")
    const providers = ["mimo", "qwen"].map((name) => ({
      name,
      model_discovery: true,
      available: true,
    }))
    let finishMimo
    const api = {
      getBackends: vi.fn(async () => ({ backends: providers })),
      discoverModels: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              finishMimo = resolve
            }),
        )
        .mockResolvedValueOnce({
          models: [
            {
              id: "v1",
              name: "Version one",
              preset: { name: "v1", model: "v1", provider: "qwen" },
            },
          ],
        })
        .mockRejectedValueOnce({ response: { data: { detail: "Provider is offline" } } }),
    }
    context.inventory.catalog = api
    const wrapper = mountShared(context)
    await flushPromises()
    await openDrawer(wrapper)
    await buttonByText(wrapper, "mimo").trigger("click")
    await buttonByText(wrapper, "qwen").trigger("click")
    await flushPromises()
    finishMimo({ models: [{ id: "stale", name: "Wrong provider" }] })
    await flushPromises()
    expect(wrapper.text()).toContain("Version one")
    expect(wrapper.text()).not.toContain("Wrong provider")
    await buttonByText(wrapper, "Refresh").trigger("click")
    await flushPromises()
    expect(api.discoverModels.mock.calls).toEqual([["mimo"], ["qwen"], ["qwen", { refresh: true }]])
    expect(api.getBackends).toHaveBeenCalledTimes(1)
    expect(refreshPresets).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain("Provider is offline")
    expect(wrapper.text()).toContain("Version one")
    await buttonByText(wrapper, "Saved presets").trigger("click")
    expect(wrapper.text()).toContain("No matching presets.")
  })

  it("does not switch a different creature after a pending preset save", async () => {
    const context = makeContext({ hostKey: ref("catalog-save-target") })
    let finishSave
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: [{ name: "codex", model_discovery: true, available: true }],
      }),
      discoverModels: async () => ({
        models: [
          {
            id: "new",
            name: "New model",
            preset: { name: "new", model: "new", provider: "codex" },
          },
        ],
      }),
      saveProfile: () =>
        new Promise((resolve) => {
          finishSave = resolve
        }),
    }
    const wrapper = mountShared(context)
    await flushPromises()
    await openDrawer(wrapper)
    await wrapper.find(".model-row").trigger("click")
    await buttonByText(wrapper, "Save & switch").trigger("click")
    context.selectedTarget.value = "root"
    finishSave({})
    await flushPromises()
    expect(context.switchModel).not.toHaveBeenCalled()
  })

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

  it("opens Provider models on the current model's row and variations, under real tablists", async () => {
    const groups = { effort: { low: {}, high: {} }, speed: { fast: {} } }
    const context = makeContext({
      hostKey: ref("catalog-current"),
      models: [
        { provider: "codex", name: "sol", model: "sol", available: true, variation_groups: groups },
      ],
    })
    context.currentModel.value = "codex/sol@effort=high"
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: [{ name: "codex", model_discovery: true, available: true }],
      }),
      discoverModels: async () => ({
        models: [
          {
            id: "sol",
            name: "Sol",
            preset: { name: "sol", model: "sol", provider: "codex", variation_groups: groups },
          },
          {
            id: "luna",
            name: "Luna",
            preset: {
              name: "luna",
              model: "luna",
              provider: "codex",
              variation_groups: { effort: { minimal: {}, low: {} } },
            },
          },
        ],
      }),
    }
    const wrapper = mountShared(context)
    await flushPromises()
    await openDrawer(wrapper)

    const source = wrapper.get('[data-test="model-source-tabs"]')
    expect(source.attributes("role")).toBe("tablist")
    const sourceTabs = source.findAll('[role="tab"]')
    expect(
      sourceTabs.map((tab) => [
        tab.text(),
        tab.attributes("aria-selected"),
        tab.attributes("tabindex"),
      ]),
    ).toEqual([
      ["Provider models", "true", "0"],
      ["Saved presets", "false", "-1"],
    ])
    const panel = wrapper.get('[role="tabpanel"][aria-label="Provider models"]')
    expect(sourceTabs[0].attributes("aria-controls")).toBe(panel.attributes("id"))
    const providerTab = wrapper.get(
      '[data-test="model-provider-tabs"] [role="tab"][aria-selected="true"]',
    )
    expect(providerTab.text()).toBe("codex")
    expect(providerTab.attributes("tabindex")).toBe("0")

    expect(wrapper.get(".model-row.is-active").text()).toContain("Sol")
    const chips = wrapper.get('[data-test="model-variations"]').findAll(".variation-chip")
    expect(
      chips.map((chip) => [chip.text(), chip.attributes("aria-pressed"), chip.attributes("title")]),
    ).toEqual([
      ["low", "false", "low"],
      ["high", "true", "high"],
      ["fast", "false", "fast"],
    ])
    expect(wrapper.get("code").text()).toBe("codex/sol@effort=high")
    await buttonByText(wrapper, "Switch").trigger("click")
    await flushPromises()
    expect(context.switchModel).not.toHaveBeenCalled()

    await wrapper
      .findAll(".model-row")
      .find((row) => row.text().includes("Luna"))
      .trigger("click")
    expect(wrapper.get("code").text()).toBe("codex/luna")
    await wrapper
      .findAll(".variation-chip")
      .find((chip) => chip.text() === "minimal")
      .trigger("click")
    expect(wrapper.get("code").text()).toBe("codex/luna@effort=minimal")

    await sourceTabs[1].trigger("click")
    expect(source.findAll('[role="tab"]').map((tab) => tab.attributes("aria-selected"))).toEqual([
      "false",
      "true",
    ])
    expect(wrapper.find('[role="tabpanel"][aria-label="Saved presets"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it("leaves Provider models unselected when the current preset only shares a model id", async () => {
    const context = makeContext({
      hostKey: ref("catalog-renamed"),
      models: [
        {
          provider: "codex",
          name: "my-sol",
          model: "sol",
          available: true,
          variation_groups: { effort: { high: {} } },
        },
      ],
    })
    context.currentModel.value = "codex/my-sol@effort=high"
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: [{ name: "codex", model_discovery: true, available: true }],
      }),
      discoverModels: async () => ({
        models: [
          { id: "sol", name: "Sol", preset: { name: "sol", model: "sol", provider: "codex" } },
        ],
      }),
    }
    const wrapper = mountShared(context)
    await flushPromises()
    await openDrawer(wrapper)
    expect(wrapper.findAll(".model-row")).toHaveLength(1)
    expect(wrapper.find(".model-row.is-active").exists()).toBe(false)
    expect(wrapper.find('[data-test="model-variations"]').exists()).toBe(false)
    expect(wrapper.get("code").text()).toBe("—")
    expect(buttonByText(wrapper, "Save & switch")).toBeUndefined()
    wrapper.unmount()
  })

  it("moves keyboard focus along the provider tabs without selecting them", async () => {
    const context = makeContext({
      hostKey: ref("provider-keys"),
      models: [
        { provider: "alpha", name: "a", model: "a", available: true },
        { provider: "codex", name: "current", model: "current", available: true },
        { provider: "zeta", name: "z", model: "z", available: true },
      ],
    })
    const wrapper = mountShared(context, { attachTo: document.body })
    await flushPromises()
    await openDrawer(wrapper)
    const tabs = () => wrapper.get('[data-test="model-provider-tabs"]').findAll('[role="tab"]')
    expect(tabs().map((tab) => [tab.attributes("title"), tab.attributes("tabindex")])).toEqual([
      ["alpha", "-1"],
      ["codex", "0"],
      ["zeta", "-1"],
    ])
    tabs()[1].element.focus()
    await tabs()[1].trigger("keydown", { key: "ArrowDown" })
    expect(document.activeElement.title).toBe("zeta")
    await tabs()[2].trigger("keydown", { key: "ArrowRight" })
    expect(document.activeElement.title).toBe("alpha")
    await tabs()[0].trigger("keydown", { key: "End" })
    expect(document.activeElement.title).toBe("zeta")
    expect(tabs()[1].attributes("aria-selected")).toBe("true")
    await tabs()[2].trigger("click")
    expect(tabs()[2].attributes("aria-selected")).toBe("true")
    expect(tabs()[2].attributes("tabindex")).toBe("0")
    wrapper.unmount()
  })

  it("restores the current choices and context when either tab shows the current preset again", async () => {
    const groups = { effort: { low: {}, high: {} } }
    const context = makeContext({
      hostKey: ref("tab-restore"),
      models: [
        { provider: "codex", name: "sol", model: "sol", available: true, variation_groups: groups },
        {
          provider: "codex",
          name: "luna",
          model: "luna",
          available: true,
          variation_groups: groups,
        },
      ],
    })
    context.currentModel.value = "codex/sol@effort=high,context=500000"
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: [{ name: "codex", model_discovery: true, available: true }],
      }),
      discoverModels: async () => ({
        models: [
          {
            id: "sol",
            name: "Sol",
            preset: { name: "sol", model: "sol", provider: "codex", variation_groups: groups },
          },
          {
            id: "luna",
            name: "Luna",
            preset: { name: "luna", model: "luna", provider: "codex", variation_groups: groups },
          },
        ],
      }),
    }
    const wrapper = mountShared(context)
    await flushPromises()
    await openDrawer(wrapper)
    const current = "codex/sol@effort=high,context=500000"
    const pressed = () =>
      wrapper.findAll('.variation-chip[aria-pressed="true"]').map((chip) => chip.text())
    const pickOtherContext = () =>
      wrapper
        .findAll("[data-context-pick]")
        .find((chip) => chip.attributes("data-context-pick") !== "500000")
        .trigger("click")
    expect(wrapper.get("code").text()).toBe(current)

    await wrapper
      .findAll(".variation-chip")
      .find((chip) => chip.text() === "low")
      .trigger("click")
    await pickOtherContext()
    expect(wrapper.get("code").text()).not.toBe(current)
    await buttonByText(wrapper, "Saved presets").trigger("click")
    expect(wrapper.get(".model-row.is-active").text()).toContain("sol")
    expect(pressed()).toEqual(["high"])
    expect(wrapper.get("code").text()).toBe(current)
    await buttonByText(wrapper, "Switch").trigger("click")
    await flushPromises()
    expect(context.switchModel).not.toHaveBeenCalled()

    await wrapper
      .findAll(".variation-chip")
      .find((chip) => chip.text() === "low")
      .trigger("click")
    await pickOtherContext()
    await buttonByText(wrapper, "Provider models").trigger("click")
    await flushPromises()
    expect(wrapper.get(".model-row.is-active").text()).toContain("Sol")
    expect(pressed()).toEqual(["high"])
    expect(wrapper.get("code").text()).toBe(current)

    await buttonByText(wrapper, "Saved presets").trigger("click")
    await wrapper
      .findAll(".model-row")
      .find((row) => row.text().startsWith("luna"))
      .trigger("click")
    expect(pressed()).toEqual([])
    await wrapper
      .findAll(".variation-chip")
      .find((chip) => chip.text() === "low")
      .trigger("click")
    expect(wrapper.get("code").text()).toMatch(/^codex\/luna@effort=low/)
    await buttonByText(wrapper, "Provider models").trigger("click")
    await flushPromises()
    await buttonByText(wrapper, "Saved presets").trigger("click")
    expect(wrapper.get(".model-row.is-active").text()).toContain("luna")
    expect(pressed()).toEqual([])
    expect(wrapper.get("code").text()).toMatch(/^codex\/luna(@context=\d+)?$/)
    wrapper.unmount()
  })

  it("restores choices only for the current preset, not a same-named preset of another model", async () => {
    const groups = { effort: { low: {}, high: {} } }
    const context = makeContext({
      hostKey: ref("tab-restore-model"),
      models: [
        {
          provider: "codex",
          name: "sol",
          model: "sol-2",
          available: true,
          variation_groups: groups,
        },
      ],
    })
    context.currentModel.value = "codex/sol@effort=high"
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: [{ name: "codex", model_discovery: true, available: true }],
      }),
      discoverModels: async () => ({
        models: [
          {
            id: "sol",
            name: "Sol",
            preset: { name: "sol", model: "sol", provider: "codex", variation_groups: groups },
          },
        ],
      }),
    }
    const wrapper = mountShared(context)
    await flushPromises()
    await openDrawer(wrapper)
    await wrapper.get(".model-row").trigger("click")
    expect(wrapper.findAll('.variation-chip[aria-pressed="true"]')).toHaveLength(0)
    expect(wrapper.get("code").text()).toBe("codex/sol")
    expect(buttonByText(wrapper, "Save & switch")).toBeDefined()
    await buttonByText(wrapper, "Saved presets").trigger("click")
    expect(
      wrapper.findAll('.variation-chip[aria-pressed="true"]').map((chip) => chip.text()),
    ).toEqual(["high"])
    expect(wrapper.get("code").text()).toBe("codex/sol@effort=high")
    wrapper.unmount()
  })
})
