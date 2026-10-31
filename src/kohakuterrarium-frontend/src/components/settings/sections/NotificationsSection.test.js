import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import ElementPlus from "element-plus"

const { requestAttentionAudioUnlock, requestNotificationPermission } = vi.hoisted(() => ({
  requestAttentionAudioUnlock: vi.fn(),
  requestNotificationPermission: vi.fn(),
}))

vi.mock("@/composables/useAttentionEffects", () => ({
  requestAttentionAudioUnlock,
  requestNotificationPermission,
}))

vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (key) => key }),
}))

import NotificationsSection from "./NotificationsSection.vue"

function mountSection() {
  return mount(NotificationsSection, { global: { plugins: [ElementPlus] } })
}

describe("NotificationsSection", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    })
    setActivePinia(createPinia())
    requestNotificationPermission.mockReset()
    requestNotificationPermission.mockResolvedValue("granted")
    requestAttentionAudioUnlock.mockReset()
    vi.stubGlobal("Notification", { permission: "default" })
    delete window.pywebview
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    delete window.pywebview
  })

  it("groups attention preferences by channel instead of rendering one flat list", async () => {
    const wrapper = mountSection()
    await flushPromises()

    for (const group of ["in-app", "notifications", "sound", "desktop"]) {
      expect(wrapper.find(`[data-attention-group="${group}"]`).exists()).toBe(true)
    }
    expect(wrapper.findAll("[data-attention-setting]")).toHaveLength(11)
    expect(wrapper.find("[data-in-app-toggle]").exists()).toBe(true)
  })

  it("asks for browser permission instead of offering the system-notification switch", async () => {
    const wrapper = mountSection()
    await flushPromises()

    const permissionButton = wrapper.find("[data-notification-permission-action]")
    expect(permissionButton.exists()).toBe(true)
    expect(wrapper.find("[data-system-notifications]").exists()).toBe(false)
    expect(
      wrapper.find('[data-attention-setting="notifyWaiting"]').attributes("disabled"),
    ).toBeDefined()

    await permissionButton.trigger("click")
    await flushPromises()

    expect(requestNotificationPermission).toHaveBeenCalledOnce()
    expect(wrapper.vm.attentionPrefs.state.systemNotifications).toBe(true)
    expect(wrapper.vm.notificationPermission).toBe("granted")
    expect(wrapper.find("[data-system-notifications]").exists()).toBe(true)
  })

  it("unlocks audio immediately when attention sound is enabled", async () => {
    const wrapper = mountSection()
    await flushPromises()

    wrapper.vm.setAttentionPreference("attentionSound", true)

    expect(wrapper.vm.attentionPrefs.state.attentionSound).toBe(true)
    expect(requestAttentionAudioUnlock).toHaveBeenCalledOnce()
  })

  it("does not offer browser notification authorization inside the desktop shell", async () => {
    window.pywebview = { api: {} }
    vi.stubGlobal("Notification", { permission: "denied" })

    const wrapper = mountSection()
    await flushPromises()

    expect(wrapper.vm.desktopSurface).toBe(true)
    expect(wrapper.vm.notificationPermission).toBe("desktop")
    expect(wrapper.find("[data-notification-permission-action]").exists()).toBe(false)
    expect(
      wrapper.find('[data-attention-group="notifications"]').attributes("data-desktop-surface"),
    ).toBe("true")
  })

  it("updates notification capability when pywebview is injected after mount", async () => {
    const wrapper = mountSection()
    await flushPromises()
    expect(wrapper.vm.notificationPermission).toBe("default")

    window.pywebview = { api: {} }
    window.dispatchEvent(new Event("pywebviewready"))
    await flushPromises()

    expect(wrapper.vm.desktopSurface).toBe(true)
    expect(wrapper.vm.notificationPermission).toBe("desktop")
    expect(wrapper.find("[data-notification-permission-action]").exists()).toBe(false)
  })

  it("keeps the desktop-only attention row inside its own group", async () => {
    const wrapper = mountSection()
    await flushPromises()

    const desktop = wrapper.find('[data-attention-group="desktop"]')
    expect(desktop.attributes("data-attention-setting")).toBeUndefined()
    expect(desktop.find('[data-attention-setting="desktopAttention"]').exists()).toBe(true)
  })
})
