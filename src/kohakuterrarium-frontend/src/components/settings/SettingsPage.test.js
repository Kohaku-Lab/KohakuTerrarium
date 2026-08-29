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

const { requestNotificationPermission } = vi.hoisted(() => ({
  requestNotificationPermission: vi.fn(),
}))

vi.mock("@/composables/useAttentionEffects", () => ({
  requestNotificationPermission,
}))

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({
    t: (key, params) => (params?.name ? `${key}:${params.name}` : key),
  }),
}))

import SettingsPage from "./SettingsPage.vue"
import { configAPI, settingsAPI } from "@/utils/api"

function mountSettingsPage() {
  return shallowMount(SettingsPage, {
    global: {
      plugins: [ElementPlus],
      stubs: {
        ElTabs: { template: "<div><slot /></div>" },
        ElTabPane: { template: "<section><slot /></section>" },
      },
    },
  })
}

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
    requestNotificationPermission.mockReset()
    requestNotificationPermission.mockResolvedValue("granted")
    vi.stubGlobal("Notification", { permission: "default" })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("groups attention preferences by channel instead of rendering one flat list", async () => {
    configAPI.getModels.mockResolvedValue([])
    const wrapper = mountSettingsPage()
    await flushPromises()

    expect(wrapper.find('[data-attention-group="in-app"]').exists()).toBe(true)
    expect(wrapper.find('[data-attention-group="notifications"]').exists()).toBe(true)
    expect(wrapper.find('[data-attention-group="sound"]').exists()).toBe(true)
    expect(wrapper.find('[data-attention-group="desktop"]').exists()).toBe(true)
    expect(wrapper.findAll("[data-attention-setting]")).toHaveLength(10)
    expect(wrapper.find("[data-in-app-toggle]").exists()).toBe(true)
  })

  it("uses a permission action instead of the system-notification preference switch", async () => {
    configAPI.getModels.mockResolvedValue([])
    const wrapper = mountSettingsPage()
    await flushPromises()

    const permissionButton = wrapper.find("[data-notification-permission-action]")
    expect(permissionButton.exists()).toBe(true)
    expect(wrapper.find('[data-attention-setting="systemNotifications"]').exists()).toBe(false)
    expect(
      wrapper.find('[data-attention-setting="notifyWaiting"]').attributes("disabled"),
    ).toBeDefined()

    await permissionButton.trigger("click")
    await flushPromises()

    expect(requestNotificationPermission).toHaveBeenCalledOnce()
    expect(wrapper.vm.attentionPrefs.state.systemNotifications).toBe(true)
    expect(wrapper.vm.notificationPermission).toBe("granted")
  })

  it("refreshes the selected preset without reporting a successful default change as failed", async () => {
    const preset = { name: "fast", provider: "openai", source: "user", is_default: false }
    const refreshed = { ...preset, is_default: true }
    configAPI.getModels.mockResolvedValueOnce([preset]).mockResolvedValueOnce([refreshed])
    const success = vi.spyOn(ElMessage, "success").mockImplementation(() => {})
    const error = vi.spyOn(ElMessage, "error").mockImplementation(() => {})

    const wrapper = mountSettingsPage()
    await flushPromises()
    wrapper.vm.selectPreset(preset)

    await wrapper.vm.handleSetDefault(preset)

    expect(settingsAPI.setDefaultModel).toHaveBeenCalledWith("fast")
    expect(success).toHaveBeenCalledOnce()
    expect(error).not.toHaveBeenCalled()
    expect(wrapper.vm.editorPreset).toEqual(refreshed)
  })
})
