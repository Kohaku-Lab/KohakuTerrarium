/**
 * AdminTab gate test — the portal must only render its panes for an
 * admin-role user on a multi-user host.  Non-admins get the
 * not-authorized placeholder and the panes never mount (so they never
 * hit the admin endpoints).  This is the client half of the gate; the
 * backend 403s regardless.
 */

import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/utils/authApi", () => {
  const authApi = {
    listUsers: vi.fn().mockResolvedValue([]),
    listInvitations: vi.fn().mockResolvedValue([]),
    tokenStatus: vi.fn().mockResolvedValue({
      host_token: { enabled: false, tail: "" },
      admin_token: { enabled: false, tail: "" },
    }),
  }
  return { authApi, default: authApi }
})

import AdminTab from "./AdminTab.vue"
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
  for (const fn of Object.values(authApi)) fn.mockClear?.()
})

afterEach(() => {
  document.body.innerHTML = ""
})

function setAdmin(isAdmin) {
  const auth = useAuthStore()
  // isAdmin = multiUserEnabled && currentUser.role === "admin"
  auth.capabilitiesByHost = { _same_origin: { multi_user: { enabled: true } } }
  auth.sameOriginUser = { id: 1, username: "root", role: isAdmin ? "admin" : "user" }
  return auth
}

describe("AdminTab — role gate", () => {
  it("renders the not-authorized placeholder for a non-admin", async () => {
    setAdmin(false)
    const wrapper = mount(AdminTab, { global: { plugins: [pinia] } })
    await flushPromises()
    expect(wrapper.text()).toContain("admin account")
    // Panes must NOT mount — no admin endpoints hit.
    expect(authApi.listUsers).not.toHaveBeenCalled()
  })

  it("renders the portal + mounts the panes for an admin", async () => {
    setAdmin(true)
    const wrapper = mount(AdminTab, { global: { plugins: [pinia] } })
    await flushPromises()
    expect(wrapper.text()).not.toContain("admin account")
    // UsersPane mounted and fetched.
    expect(authApi.listUsers).toHaveBeenCalled()
  })
})
