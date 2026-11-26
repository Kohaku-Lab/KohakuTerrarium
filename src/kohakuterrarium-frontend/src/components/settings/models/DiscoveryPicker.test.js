import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import DiscoveryPicker from "./DiscoveryPicker.vue"
import { settingsAPI } from "@/utils/api"
import {
  PROVIDER_MODELS_TTL_MS,
  _resetProviderModelsForTests,
  invalidateProviderModels,
} from "@/utils/providerModels"

vi.mock("@/utils/api", () => ({ settingsAPI: { discoverModels: vi.fn() } }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

const stubs = {
  ElDialog: { template: "<div><slot/><slot name='footer'/></div>" },
  ElSelect: { props: ["modelValue"], template: "<div/>" },
  ElInput: { props: ["modelValue"], template: "<input/>" },
  ElOption: true,
}

beforeEach(() => {
  _resetProviderModelsForTests()
  settingsAPI.discoverModels.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe("provider model discovery", () => {
  it("searches the whole catalog while rendering a page at a time and refreshes only the selected provider", async () => {
    settingsAPI.discoverModels.mockReset()
    const rows = Array.from({ length: 120 }, (_, i) => ({ id: `model-${i}`, name: `Model ${i}` }))
    settingsAPI.discoverModels
      .mockResolvedValueOnce({ models: rows })
      .mockResolvedValueOnce({ models: [{ id: "replacement", name: "Replacement" }] })
    const wrapper = mount(DiscoveryPicker, {
      props: {
        modelValue: true,
        backends: ["openrouter", "other"].map((name) => ({
          name,
          model_discovery: true,
          available: true,
        })),
      },
      global: { stubs },
    })
    await flushPromises()
    expect(wrapper.findAll("button.w-full")).toHaveLength(50)
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "discovery.more")
      .trigger("click")
    expect(wrapper.findAll("button.w-full")).toHaveLength(100)
    wrapper.findComponent(stubs.ElInput).vm.$emit("update:modelValue", "model-119")
    await flushPromises()
    expect(wrapper.get("button.w-full").text()).toContain("Model 119")
    wrapper.findComponent(stubs.ElInput).vm.$emit("update:modelValue", "")
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "discovery.refresh")
      .trigger("click")
    await flushPromises()
    expect(settingsAPI.discoverModels.mock.calls).toEqual([
      ["openrouter"],
      ["openrouter", { refresh: true }],
    ])
    expect(wrapper.get("button.w-full").text()).toContain("Replacement")
    expect(wrapper.findAll("button.w-full")).toHaveLength(1)
  })
  it("ignores an old catalog after switching providers and picks the filtered draft", async () => {
    let finishOld
    const row = { id: "new", name: "New model", preset: { provider: "qwen", model: "new" } }
    settingsAPI.discoverModels
      .mockReturnValueOnce(
        new Promise((resolve) => {
          finishOld = resolve
        }),
      )
      .mockResolvedValueOnce({ models: [row] })
    const wrapper = mount(DiscoveryPicker, {
      props: {
        modelValue: true,
        backends: [
          { name: "deepseek", model_discovery: true, available: true },
          { name: "qwen", model_discovery: true },
        ],
      },
      global: { stubs },
    })
    wrapper.findComponent(stubs.ElSelect).vm.$emit("update:modelValue", "qwen")
    await flushPromises()
    finishOld({ models: [{ id: "old", name: "Old model" }] })
    await flushPromises()
    expect(wrapper.text()).not.toContain("Old model")
    wrapper.findComponent(stubs.ElInput).vm.$emit("update:modelValue", "unmatched")
    await flushPromises()
    expect(wrapper.text()).toContain("settings.models.noMatch")
    wrapper.findComponent(stubs.ElInput).vm.$emit("update:modelValue", "")
    await flushPromises()
    await wrapper.get("button.w-full").trigger("click")
    expect(wrapper.emitted("pick")).toEqual([[row.preset]])
  })
  it("says whether a preset exists and which reasoning levels a new one offers", async () => {
    settingsAPI.discoverModels.mockReset()
    const fresh = {
      id: "gpt-6-sol",
      name: "GPT-6 Sol",
      has_preset: false,
      reasoning_levels: ["none", "minimal", "low", "medium", "high", "xhigh", "ultra"],
      context_window: 1000000,
      max_context: 400000,
      preset: {
        provider: "openai",
        model: "gpt-6-sol",
        context_window: 1000000,
        variation_groups: { reasoning: { low: {}, high: {} } },
      },
    }
    const known = {
      id: "gpt-5",
      name: "gpt-5",
      has_preset: true,
      reasoning_levels: [],
      context_window: null,
    }
    const legacy = { id: "old", name: "old" }
    settingsAPI.discoverModels.mockResolvedValueOnce({ models: [fresh, known, legacy] })
    const wrapper = mount(DiscoveryPicker, {
      props: {
        modelValue: true,
        backends: [{ name: "openai", model_discovery: true, available: true }],
      },
      global: { stubs },
    })
    await flushPromises()
    const [freshRow, knownRow, legacyRow] = wrapper.findAll("button.w-full")
    expect(freshRow.get("[data-preset-badge]").attributes("data-preset-badge")).toBe("new")
    expect(freshRow.get("[data-preset-badge]").text()).toBe("discovery.newPreset")
    expect(freshRow.findAll("[data-reasoning-level]").map((chip) => chip.text())).toEqual([
      "none",
      "minimal",
      "low",
      "medium",
      "high",
      "xhigh",
    ])
    expect(freshRow.get("[data-reasoning-levels]").text()).toContain("+1")
    expect(freshRow.get("[data-context]").exists()).toBe(true)
    expect(knownRow.get("[data-preset-badge]").attributes("data-preset-badge")).toBe("existing")
    expect(knownRow.get("[data-preset-badge]").text()).toBe("discovery.hasPreset")
    expect(knownRow.find("[data-reasoning-levels]").exists()).toBe(false)
    expect(knownRow.find("[data-context]").exists()).toBe(false)
    expect(legacyRow.find("[data-preset-badge]").exists()).toBe(false)
    await freshRow.trigger("click")
    expect(wrapper.emitted("pick")).toEqual([[fresh.preset]])
  })
  it("shows cached rows at once on provider switch and reopen, and revalidates only after the TTL", async () => {
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(new Date("2026-11-26T10:00:00Z"))
    const catalogs = {
      deepseek: {
        models: [{ id: "ds", name: "DeepSeek" }],
        fetched_at: "2026-11-26T10:00:00+00:00",
      },
      qwen: { models: [{ id: "qw", name: "Qwen" }] },
    }
    settingsAPI.discoverModels.mockImplementation(async (name) => catalogs[name])
    const backends = ["deepseek", "qwen"].map((name) => ({
      name,
      model_discovery: true,
      available: true,
    }))
    const wrapper = mount(DiscoveryPicker, {
      props: { modelValue: true, backends },
      global: { stubs },
    })
    await flushPromises()
    expect(wrapper.text()).toContain("DeepSeek")
    expect(wrapper.get("[data-fetched-at]").text()).toBe("discovery.updated")
    const select = wrapper.findComponent(stubs.ElSelect)
    select.vm.$emit("update:modelValue", "qwen")
    await flushPromises()
    select.vm.$emit("update:modelValue", "deepseek")
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain("DeepSeek")
    expect(wrapper.text()).not.toContain("discovery.loading")
    await wrapper.setProps({ modelValue: false })
    await wrapper.setProps({ modelValue: true })
    expect(wrapper.text()).toContain("DeepSeek")
    await flushPromises()
    expect(settingsAPI.discoverModels.mock.calls).toEqual([["deepseek"], ["qwen"]])

    let revalidate
    settingsAPI.discoverModels.mockReturnValueOnce(
      new Promise((resolve) => {
        revalidate = resolve
      }),
    )
    vi.setSystemTime(Date.now() + PROVIDER_MODELS_TTL_MS)
    await wrapper.setProps({ modelValue: false })
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.findAll("button.w-full").map((b) => b.text())).toEqual(["DeepSeekds"])
    expect(wrapper.text()).not.toContain("discovery.loading")
    revalidate({ models: [{ id: "ds2", name: "DeepSeek Two" }] })
    await flushPromises()
    expect(wrapper.text()).toContain("DeepSeek Two")
    expect(settingsAPI.discoverModels.mock.calls).toEqual([["deepseek"], ["qwen"], ["deepseek"]])
  })

  it("keeps the rows when a refresh fails, and refetches after the provider settings change", async () => {
    settingsAPI.discoverModels.mockResolvedValueOnce({ models: [{ id: "kept", name: "Kept" }] })
    const wrapper = mount(DiscoveryPicker, {
      props: {
        modelValue: true,
        backends: [{ name: "mimo", model_discovery: true, available: true }],
      },
      global: { stubs },
    })
    await flushPromises()
    let fail
    settingsAPI.discoverModels.mockReturnValueOnce(
      new Promise((_, reject) => {
        fail = reject
      }),
    )
    const refresh = wrapper.findAll("button").find((b) => b.text() === "discovery.refresh")
    await refresh.trigger("click")
    expect(refresh.attributes("disabled")).toBeDefined()
    expect(wrapper.text()).toContain("Kept")
    expect(wrapper.text()).not.toContain("discovery.loading")
    fail({ response: { data: { detail: "Provider is offline" } } })
    await flushPromises()
    expect(wrapper.text()).toContain("Kept")
    expect(wrapper.text()).toContain("Provider is offline")
    expect(settingsAPI.discoverModels.mock.calls).toEqual([["mimo"], ["mimo", { refresh: true }]])

    invalidateProviderModels("mimo")
    settingsAPI.discoverModels.mockResolvedValueOnce({
      models: [{ id: "new-key", name: "New key" }],
    })
    await wrapper.setProps({ modelValue: false })
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.text()).toContain("New key")
    expect(wrapper.text()).not.toContain("Provider is offline")
    expect(settingsAPI.discoverModels).toHaveBeenCalledTimes(3)
  })
})
