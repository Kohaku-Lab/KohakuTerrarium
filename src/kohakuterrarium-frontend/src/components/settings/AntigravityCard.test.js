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
  it("loads offline status and discovers models only on request", async () => {
    settingsAPI.getAntigravityModels.mockResolvedValue({
      models: [{ id: "gemini-3-flash", name: "Gemini 3 Flash" }],
    })
    const wrapper = mount(AntigravityCard)
    await flushPromises()
    expect(wrapper.text()).toContain("settings.antigravity.ready")
    expect(settingsAPI.getAntigravityModels).not.toHaveBeenCalled()
    await wrapper.get("[data-agy-models]").trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("gemini-3-flash")
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
