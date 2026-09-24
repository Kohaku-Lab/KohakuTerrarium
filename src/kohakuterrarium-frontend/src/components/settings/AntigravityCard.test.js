import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({
  settingsAPI: {
    getAntigravityStatus: vi.fn(),
    refreshAntigravity: vi.fn(),
    getAntigravityModels: vi.fn(),
  },
}))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))
import { settingsAPI } from "@/utils/api"
import AntigravityCard from "./AntigravityCard.vue"

describe("Antigravity local account", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    settingsAPI.getAntigravityStatus.mockResolvedValue({
      state: "ready",
      source: "windows_keyring",
    })
  })
  it("keeps provider settings focused on credentials", async () => {
    settingsAPI.refreshAntigravity.mockResolvedValue({ state: "ready", source: "windows_keyring" })
    const wrapper = mount(AntigravityCard)
    await flushPromises()
    expect(wrapper.text()).toContain("settings.antigravity.ready")
    expect(wrapper.find("[data-agy-models]").exists()).toBe(false)
    await wrapper.get("[data-agy-refresh]").trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("settings.antigravity.ready")
    expect(settingsAPI.getAntigravityModels).not.toHaveBeenCalled()
  })
  it("shows permission failures separately from missing credentials", async () => {
    settingsAPI.getAntigravityStatus.mockRejectedValue({
      response: { status: 401, headers: { "x-auth-required": "admin" } },
    })
    const wrapper = mount(AntigravityCard)
    await flushPromises()
    expect(wrapper.text()).toContain("settings.antigravity.adminRequired")
    expect(wrapper.text()).not.toContain("settings.antigravity.login_required")
  })
  it("rejects worker selection without any account read", async () => {
    const wrapper = mount(AntigravityCard, { props: { node: "worker" } })
    await flushPromises()
    expect(wrapper.text()).toContain("settings.antigravity.localOnly")
    expect(settingsAPI.getAntigravityStatus).not.toHaveBeenCalled()
  })
})
