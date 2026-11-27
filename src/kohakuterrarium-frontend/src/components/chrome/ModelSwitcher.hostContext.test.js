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
    expect(api.saveProfile).toHaveBeenCalledWith({
      ...preset,
      create_only: true,
      catalog_model_id: preset.model,
    })
    expect(context.switchModel).toHaveBeenCalledWith({
      target: "beta",
      selector: "deepseek/deepseek-flash",
    })
  })

  it("switches to the first user preset after saving from the default provider catalog", async () => {
    const options = { hostKey: ref("first-saved-preset"), models: [] }
    const context = makeContext(options)
    const preset = { provider: "codex", name: "first", model: "first" }
    const saveProfile = vi.fn(async () => {
      options.models = [{ ...preset, available: true, source: "user" }]
    })
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: [{ name: "codex", available: true, model_discovery: true }],
      }),
      discoverModels: async () => ({ models: [{ id: "first", preset }] }),
      saveProfile,
    }
    const wrapper = mountShared(context)
    await openDrawer(wrapper)
    await wrapper.get('[aria-label="Provider models"] .model-row').trigger("click")
    await buttonByText(wrapper, "Save & switch").trigger("click")
    await flushPromises()
    expect(saveProfile).toHaveBeenCalledExactlyOnceWith({
      ...preset,
      create_only: true,
      catalog_model_id: preset.model,
    })
    expect(context.inventory.models.value).toEqual(options.models)
    expect(context.switchModel).toHaveBeenCalledExactlyOnceWith({
      target: "beta",
      selector: "codex/first",
    })
    await openDrawer(wrapper)
    expect(wrapper.get('[aria-label="Saved presets"] .model-row').text()).toContain("first")
    wrapper.unmount()
  })

  it("refreshes both lists after a concurrent save, then waits for an explicit switch", async () => {
    const state = { hostKey: ref("catalog-conflict"), models: [] }
    const context = makeContext(state)
    const preset = { provider: "codex", name: "new", model: "new" }
    const existing = {
      ...preset,
      name: "my-new",
      max_context: 10000,
      source: "user",
      available: true,
    }
    const discoverModels = vi.fn(async () => ({
      models: [{ id: "new", preset: state.models[0] || preset, has_preset: !!state.models.length }],
    }))
    const saveProfile = vi.fn(async () => {
      state.models = [existing]
      throw { response: { status: 409, data: { detail: "Preset changed" } } }
    })
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: [{ name: "codex", available: true, model_discovery: true }],
      }),
      discoverModels,
      saveProfile,
    }
    const wrapper = mountShared(context)
    await openDrawer(wrapper)
    await wrapper.get(".model-row").trigger("click")
    await buttonByText(wrapper, "Save & switch").trigger("click")
    await flushPromises()
    expect(saveProfile).toHaveBeenCalledExactlyOnceWith({
      ...preset,
      create_only: true,
      catalog_model_id: "new",
    })
    expect(discoverModels.mock.calls).toEqual([["codex"], ["codex", { refresh: true }]])
    expect(context.inventory.models.value).toEqual([existing])
    expect(context.switchModel).not.toHaveBeenCalled()
    expect(wrapper.get("code").text()).toBe("—")
    await wrapper.get(".model-row").trigger("click")
    expect(wrapper.get("code").text()).toBe("codex/my-new")
    await buttonByText(wrapper, "Switch").trigger("click")
    await flushPromises()
    expect(context.switchModel).toHaveBeenCalledExactlyOnceWith({
      target: "beta",
      selector: "codex/my-new",
    })
    expect(saveProfile).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it("refreshes a cached deleted alias before offering to save its model again", async () => {
    const state = { hostKey: ref("deleted-catalog-alias"), models: [] }
    const context = makeContext(state)
    const fresh = { provider: "codex", name: "model", model: "model" }
    const stale = { ...fresh, name: "deleted", max_context: 10000 }
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: [{ name: "codex", available: true, model_discovery: true }],
      }),
      discoverModels: vi
        .fn()
        .mockResolvedValueOnce({ models: [{ id: "model", preset: stale, has_preset: true }] })
        .mockResolvedValueOnce({ models: [{ id: "model", preset: fresh, has_preset: false }] }),
      saveProfile: vi.fn(),
    }
    const wrapper = mountShared(context)
    await openDrawer(wrapper)
    expect(wrapper.get(".model-row").text()).toContain("Refresh saved preset")
    await wrapper.get(".model-row").trigger("click")
    await flushPromises()
    expect(context.inventory.catalog.discoverModels.mock.calls).toEqual([
      ["codex"],
      ["codex", { refresh: true }],
    ])
    expect(wrapper.get("code").text()).toBe("—")
    await wrapper.get(".model-row").trigger("click")
    expect(wrapper.get("code").text()).toBe("codex/model")
    expect(buttonByText(wrapper, "Save & switch")).toBeDefined()
    expect(context.inventory.catalog.saveProfile).not.toHaveBeenCalled()
    expect(context.switchModel).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it("opens on manual presets and preserves an explicit source across shared-gateway providers", async () => {
    const id = "fly/slurm/inkling2-finetuned"
    const models = [
      { provider: "inkling", name: "inkling2-fly", model: id, source: "user", available: true },
      { provider: "kimi", name: "kimi-local", model: "k3", source: "user", available: true },
    ]
    const context = makeContext({ hostKey: ref("manual-default"), models })
    context.currentModel.value = "inkling/inkling2-fly"
    const discoverModels = vi.fn(async () => {
      throw new Error("offline")
    })
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: models.map((p) => ({ name: p.provider, available: true, model_discovery: true })),
      }),
      discoverModels,
    }
    const w = mountShared(context)
    await flushPromises()
    await openDrawer(w)
    expect(w.get('[aria-label="Saved presets"]').text()).toContain("inkling2-fly")
    expect(discoverModels).not.toHaveBeenCalled()
    await buttonByText(w, "Provider models").trigger("click")
    await buttonByText(w, "kimi").trigger("click")
    await flushPromises()
    expect(w.get('[aria-label="Provider models"]').text()).toContain("Could not load")
    await buttonByText(w, "Saved presets").trigger("click")
    expect(w.get(".model-row").text()).toContain("kimi-local")
    await buttonByText(w, "Switch").trigger("click")
    await flushPromises()
    expect(context.switchModel).toHaveBeenCalledWith({
      target: "beta",
      selector: "kimi/kimi-local",
    })
    w.unmount()
  })

  it("uses the user's same-name override and explicitly selects between shared-model aliases", async () => {
    const manual = (name, model) => ({
      provider: "p",
      source: "user",
      available: true,
      name,
      model,
      max_context: 262144,
    })
    const context = makeContext({
      hostKey: ref("manual-conflict"),
      models: [manual("sol", "local/model"), manual("A", "shared"), manual("B", "shared")],
    })
    context.currentModel.value = "p/sol"
    const saveProfile = vi.fn()
    context.inventory.catalog = {
      getBackends: async () => ({
        backends: [{ name: "p", model_discovery: true, available: true }],
      }),
      discoverModels: async () => ({
        models: ["sol", "shared"].map((id) => ({
          id,
          name: id,
          preset: { name: id, model: id, provider: "p" },
          context_window: 1000000,
        })),
      }),
      saveProfile,
    }
    const w = mountShared(context)
    await flushPromises()
    await openDrawer(w)
    await buttonByText(w, "Provider models").trigger("click")
    await flushPromises()
    await w.findAll(".model-row")[0].trigger("click")
    expect(w.get(".model-row.is-active").text()).toContain("local/model")
    expect(w.get("code").text()).toBe("p/sol")
    expect(w.get('[data-test="model-context-input"]').attributes("modelvalue")).toBe("262144")
    expect(w.find('[data-test="model-context-note"]').text()).not.toContain("1M")
    await w.findAll(".model-row")[1].trigger("click")
    expect(w.get("code").text()).toBe("—")
    await buttonByText(w, "B").trigger("click")
    expect(w.get(".model-row.is-active").text()).toContain("Bshared")
    await buttonByText(w, "Switch").trigger("click")
    await flushPromises()
    expect(context.switchModel).toHaveBeenCalledWith({ target: "beta", selector: "p/B" })
    expect(saveProfile).not.toHaveBeenCalled()
    w.unmount()
  })

  it("displays an orphaned preset with its model and blocks a switch", async () => {
    const context = makeContext({
      hostKey: ref("missing-provider"),
      models: [
        {
          provider: "gone",
          name: "kept",
          model: "real",
          source: "user",
          available: false,
          provider_missing: true,
        },
      ],
    })
    context.currentModel.value = ""
    const w = mountShared(context)
    await flushPromises()
    await openDrawer(w)
    expect(w.get(".model-row").text()).toContain("Provider missing")
    await buttonByText(w, "Switch").trigger("click")
    expect(context.switchModel).not.toHaveBeenCalled()
    w.unmount()
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
