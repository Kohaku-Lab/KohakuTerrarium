import { flushPromises, shallowMount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import ElementPlus, { ElMessage } from "element-plus"

vi.mock("@/utils/api", () => {
  const settingsAPI = {
    getKeys: vi.fn(),
    getBackends: vi.fn(),
    getNativeTools: vi.fn(),
    listMCP: vi.fn(),
    setDefaultModel: vi.fn(),
  }
  const configAPI = { getModels: vi.fn() }
  return { configAPI, settingsAPI }
})

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({
    t: (key, params) => (params?.name ? `${key}:${params.name}` : key),
  }),
}))

import SettingsPage from "./SettingsPage.vue"
import { configAPI, settingsAPI } from "@/utils/api"

describe("SettingsPage model presets", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    })
    setActivePinia(createPinia())
    settingsAPI.getKeys.mockResolvedValue({ providers: [] })
    settingsAPI.getBackends.mockResolvedValue({ backends: [] })
    settingsAPI.getNativeTools.mockResolvedValue({ tools: [] })
    settingsAPI.listMCP.mockResolvedValue({ servers: [] })
    settingsAPI.setDefaultModel.mockResolvedValue({})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("refreshes the selected preset without reporting a successful default change as failed", async () => {
    const preset = { name: "fast", provider: "openai", source: "user", is_default: false }
    const refreshed = { ...preset, is_default: true }
    configAPI.getModels.mockResolvedValueOnce([preset]).mockResolvedValueOnce([refreshed])
    const success = vi.spyOn(ElMessage, "success").mockImplementation(() => {})
    const error = vi.spyOn(ElMessage, "error").mockImplementation(() => {})

    const wrapper = shallowMount(SettingsPage, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    wrapper.vm.selectPreset(preset)

    await wrapper.vm.handleSetDefault(preset)

    expect(settingsAPI.setDefaultModel).toHaveBeenCalledWith("fast")
    expect(success).toHaveBeenCalledOnce()
    expect(error).not.toHaveBeenCalled()
    expect(wrapper.vm.editorPreset).toEqual(refreshed)
  })
})
