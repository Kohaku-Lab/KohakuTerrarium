/**
 * InvitationsPane tests — render the invitation list and drive the
 * inline revoke flow.  Creation (dialog) + the one-time-token display
 * share the pattern tested in AccountSection; the store create/revoke
 * actions are covered by stores/admin.test.js.
 */

import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { ElMessage, ElMessageBox } from "element-plus"

vi.mock("@/utils/authApi", () => {
  const authApi = {
    listInvitations: vi.fn(),
    createInvitation: vi.fn(),
    revokeInvitation: vi.fn(),
  }
  return { authApi, default: authApi }
})

import InvitationsPane from "./InvitationsPane.vue"
import { authApi } from "@/utils/authApi"
import { _resetHostsStorageForTests } from "@/stores/hosts"

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
  authApi.listInvitations.mockReset()
  authApi.revokeInvitation.mockReset().mockResolvedValue({ status: "revoked" })
  vi.spyOn(ElMessage, "success").mockImplementation(() => {})
  vi.spyOn(ElMessage, "error").mockImplementation(() => {})
})

afterEach(() => {
  document.body.innerHTML = ""
  vi.restoreAllMocks()
})

async function mountWith(invitations) {
  authApi.listInvitations.mockResolvedValue(invitations)
  const wrapper = mount(InvitationsPane, { attachTo: document.body, global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().includes(text))
}

describe("InvitationsPane — render", () => {
  it("lists invitations", async () => {
    const wrapper = await mountWith([
      { id: 7, role: "admin", expires_at: null, created_at: null, created_by: 1 },
    ])
    expect(authApi.listInvitations).toHaveBeenCalled()
    expect(wrapper.text()).toContain("#7")
    expect(wrapper.text()).toContain("admin")
    expect(wrapper.text()).toContain("no expiry")
  })

  it("shows the empty state", async () => {
    const wrapper = await mountWith([])
    expect(wrapper.text()).toContain("No active invitations")
  })
})

describe("InvitationsPane — revoke", () => {
  it("revokes after confirmation", async () => {
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm")
    const wrapper = await mountWith([{ id: 7, role: "user", expires_at: null, created_at: null }])
    await buttonByText(wrapper, "Revoke").trigger("click")
    await flushPromises()
    expect(authApi.revokeInvitation).toHaveBeenCalledWith(7)
  })

  it("does not revoke when dismissed", async () => {
    vi.spyOn(ElMessageBox, "confirm").mockRejectedValue("cancel")
    const wrapper = await mountWith([{ id: 7, role: "user", expires_at: null, created_at: null }])
    await buttonByText(wrapper, "Revoke").trigger("click")
    await flushPromises()
    expect(authApi.revokeInvitation).not.toHaveBeenCalled()
  })
})
