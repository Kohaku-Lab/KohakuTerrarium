/**
 * AccountSection tests — the L4 self-service surface: identity render,
 * password change (incl. the mismatch guard), and API-token create /
 * one-time-display / revoke.  ``authApi`` is mocked; we assert the
 * wrapper calls it with the right shape and reflects results in the DOM.
 */

import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { ElMessage, ElMessageBox } from "element-plus"

vi.mock("@/utils/authApi", () => {
  const authApi = {
    listTokens: vi.fn(),
    createToken: vi.fn(),
    revokeToken: vi.fn(),
    changePassword: vi.fn(),
  }
  return { authApi, default: authApi }
})

import AccountSection from "./AccountSection.vue"
import { authApi } from "@/utils/authApi"
import { _resetHostsStorageForTests } from "@/stores/hosts"
import { useAuthStore } from "@/stores/auth"

let storage
let pinia

beforeEach(() => {
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
    clear: () => storage.clear(),
  })
  _resetHostsStorageForTests()
  pinia = createPinia()
  setActivePinia(pinia)
  authApi.listTokens.mockReset().mockResolvedValue([])
  authApi.createToken.mockReset()
  authApi.revokeToken.mockReset().mockResolvedValue({ status: "revoked" })
  authApi.changePassword.mockReset().mockResolvedValue({ status: "ok" })
  // Silence the toast singletons (jsdom DOM churn) without losing the
  // ability to assert they fired.
  vi.spyOn(ElMessage, "success").mockImplementation(() => {})
  vi.spyOn(ElMessage, "error").mockImplementation(() => {})
  vi.spyOn(ElMessage, "warning").mockImplementation(() => {})
})

afterEach(() => {
  document.body.innerHTML = ""
  vi.restoreAllMocks()
})

async function mountWith(user = { id: 1, username: "alice", role: "admin" }) {
  const auth = useAuthStore()
  auth.sameOriginUser = user
  const wrapper = mount(AccountSection, {
    attachTo: document.body,
    global: { plugins: [pinia] },
  })
  await flushPromises()
  return { wrapper, auth }
}

function buttonByText(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().includes(text))
}

describe("AccountSection — identity", () => {
  it("renders the signed-in username + role", async () => {
    const { wrapper } = await mountWith()
    expect(wrapper.text()).toContain("alice")
    expect(wrapper.text()).toContain("admin")
  })

  it("loads tokens on mount", async () => {
    await mountWith()
    expect(authApi.listTokens).toHaveBeenCalledTimes(1)
  })
})

describe("AccountSection — change password", () => {
  it("blocks mismatched passwords without calling the API", async () => {
    const { wrapper } = await mountWith()
    await wrapper.find('input[autocomplete="current-password"]').setValue("old")
    const next = wrapper.findAll('input[autocomplete="new-password"]')
    await next[0].setValue("aaa")
    await next[1].setValue("bbb")
    await buttonByText(wrapper, "Change password").trigger("click")
    await flushPromises()
    expect(authApi.changePassword).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain("do not match")
  })

  it("submits a matching password change", async () => {
    const { wrapper } = await mountWith()
    await wrapper.find('input[autocomplete="current-password"]').setValue("old")
    const next = wrapper.findAll('input[autocomplete="new-password"]')
    await next[0].setValue("secret9")
    await next[1].setValue("secret9")
    await buttonByText(wrapper, "Change password").trigger("click")
    await flushPromises()
    expect(authApi.changePassword).toHaveBeenCalledWith({
      currentPassword: "old",
      newPassword: "secret9",
    })
  })

  it("surfaces a wrong-current-password 401 inline", async () => {
    authApi.changePassword.mockRejectedValueOnce({ response: { status: 401 } })
    const { wrapper } = await mountWith()
    await wrapper.find('input[autocomplete="current-password"]').setValue("wrong")
    const next = wrapper.findAll('input[autocomplete="new-password"]')
    await next[0].setValue("secret9")
    await next[1].setValue("secret9")
    await buttonByText(wrapper, "Change password").trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("incorrect")
  })
})

describe("AccountSection — API tokens", () => {
  it("creates a token and shows the plaintext once", async () => {
    authApi.createToken.mockResolvedValueOnce({ token: "kt_PLAINTEXT_123", id: 9, name: "laptop" })
    const { wrapper } = await mountWith()
    await wrapper.find('input[type="text"]').setValue("laptop")
    await buttonByText(wrapper, "Create token").trigger("click")
    await flushPromises()
    expect(authApi.createToken).toHaveBeenCalledWith("laptop")
    // Plaintext shown for copy + the list is refreshed.
    expect(wrapper.text()).toContain("kt_PLAINTEXT_123")
    expect(authApi.listTokens).toHaveBeenCalledTimes(2)
  })

  it("revokes a token after confirmation", async () => {
    authApi.listTokens
      .mockReset()
      .mockResolvedValue([{ id: 3, name: "old-laptop", created_at: null, last_used_at: null }])
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm")
    const { wrapper } = await mountWith()
    expect(wrapper.text()).toContain("old-laptop")
    await buttonByText(wrapper, "Revoke").trigger("click")
    await flushPromises()
    expect(authApi.revokeToken).toHaveBeenCalledWith(3)
  })

  it("does not revoke when the confirm is dismissed", async () => {
    authApi.listTokens.mockReset().mockResolvedValue([{ id: 3, name: "keep", created_at: null }])
    vi.spyOn(ElMessageBox, "confirm").mockRejectedValue("cancel")
    const { wrapper } = await mountWith()
    await buttonByText(wrapper, "Revoke").trigger("click")
    await flushPromises()
    expect(authApi.revokeToken).not.toHaveBeenCalled()
  })
})
