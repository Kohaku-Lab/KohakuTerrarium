import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ElementPlus from "element-plus"
vi.mock("@/utils/api", () => ({
  settingsAPI: { getAntigravityStatus: vi.fn(), getGrokStatus: vi.fn() },
}))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))
import { settingsAPI } from "@/utils/api"
import OAuthProviderRow from "./OAuthProviderRow.vue"
const backend = (type, available = true) => ({ name: type, backend_type: type, available })
function row(type, props = {}) {
  return mount(OAuthProviderRow, {
    props: { backend: backend(type), ...props },
    global: { plugins: [ElementPlus] },
  })
}
describe("OAuth provider actions and status", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    settingsAPI.getAntigravityStatus.mockResolvedValue({ state: "ready", refresh_available: true })
    settingsAPI.getGrokStatus.mockResolvedValue({
      authenticated: true,
      source: "grok-cli",
      expires_at: Date.now() / 1000 + 3600,
    })
  })
  it("keeps Codex's only action as login, without reading borrowed credentials", async () => {
    const wrapper = row("codex")
    expect(wrapper.findAll("button")).toHaveLength(1)
    expect(wrapper.get("button").text()).toBe("settings.oauth.relogin")
    await wrapper.get("button").trigger("click")
    expect(wrapper.emitted("login")).toHaveLength(1)
    await wrapper.setProps({ backend: backend("codex", false) })
    expect(wrapper.get("button").text()).toBe("settings.oauth.login")
    expect(settingsAPI.getAntigravityStatus).not.toHaveBeenCalled()
    expect(settingsAPI.getGrokStatus).not.toHaveBeenCalled()
  })
  it("checks Antigravity without exposing refresh or model discovery actions", async () => {
    const wrapper = row("google-antigravity")
    await flushPromises()
    expect(wrapper.findAll("button")).toHaveLength(1)
    expect(wrapper.get("button").text()).toBe("settings.oauth.check")
    expect(wrapper.text()).toContain("settings.oauth.ready")
    settingsAPI.getAntigravityStatus.mockResolvedValue({
      state: "expired",
      refresh_available: true,
    })
    await wrapper.get("button").trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.pending")
    expect(wrapper.text()).not.toContain("settings.oauth.ready")
    expect(wrapper.text()).toContain("settings.oauth.autoRefresh")
  })
  it("shows expired credentials requiring user action when agy cannot refresh", async () => {
    settingsAPI.getAntigravityStatus.mockResolvedValue({
      state: "expired",
      refresh_available: false,
    })
    const wrapper = row("google-antigravity")
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.expired")
    expect(wrapper.text()).not.toContain("settings.oauth.pending")
  })
  it("distinguishes permission failures from missing login", async () => {
    settingsAPI.getAntigravityStatus.mockRejectedValue({ response: { status: 403 } })
    const wrapper = row("google-antigravity")
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain("settings.oauth.adminRequired")
    expect(wrapper.text()).not.toContain("settings.oauth.missing")
  })
  it("does not read Antigravity credentials for remote nodes", async () => {
    const wrapper = row("google-antigravity", { node: "worker" })
    await flushPromises()
    expect(settingsAPI.getAntigravityStatus).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain("settings.antigravity.localOnly")
    expect(wrapper.get("button").attributes("disabled")).toBeDefined()
  })
  it("shows expired Grok CLI credentials as awaiting automatic refresh", async () => {
    settingsAPI.getGrokStatus.mockResolvedValue({
      authenticated: true,
      source: "grok-cli",
      expires_at: Date.now() / 1000 - 60,
    })
    const wrapper = row("grok-subscription")
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.pending")
    expect(wrapper.findAll("button")).toHaveLength(1)
    settingsAPI.getGrokStatus.mockRejectedValue(new Error("network"))
    await wrapper.get("button").trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.failed")
    expect(wrapper.text()).not.toContain("settings.oauth.missing")
  })
  it("ignores stale Grok responses after switching nodes", async () => {
    let resolveOld
    settingsAPI.getGrokStatus.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve
        }),
    )
    const wrapper = row("grok-subscription")
    settingsAPI.getGrokStatus.mockResolvedValue({ authenticated: false })
    await wrapper.setProps({ node: "worker" })
    await flushPromises()
    resolveOld({ authenticated: true, source: "grok-cli" })
    await flushPromises()
    expect(wrapper.text()).toContain("settings.oauth.missing")
    expect(wrapper.text()).not.toContain("settings.oauth.ready")
  })
})
