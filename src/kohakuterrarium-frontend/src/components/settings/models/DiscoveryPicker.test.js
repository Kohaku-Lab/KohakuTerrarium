import { flushPromises, mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"

import DiscoveryPicker from "./DiscoveryPicker.vue"
import { settingsAPI } from "@/utils/api"

vi.mock("@/utils/api", () => ({ settingsAPI: { discoverModels: vi.fn() } }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

const stubs = {
  ElDialog: { template: "<div><slot/><slot name='footer'/></div>" },
  ElSelect: { props: ["modelValue"], template: "<div/>" },
  ElInput: { props: ["modelValue"], template: "<input/>" },
  ElOption: true,
}

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
    expect(settingsAPI.discoverModels.mock.calls).toEqual([["openrouter"], ["openrouter"]])
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
})
