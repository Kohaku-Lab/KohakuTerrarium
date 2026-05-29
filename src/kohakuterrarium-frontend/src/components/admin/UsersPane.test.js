/**
 * UsersPane tests — renders the user list (real admin store over a
 * mocked authApi), tags the current user, and drives the delete flow
 * including the backend-guard error surface (e.g. last-admin).  Role /
 * active mutations are thin wrappers over ``admin.patchUser`` and are
 * covered by stores/admin.test.js.
 */

import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { ElMessage, ElMessageBox } from "element-plus"

vi.mock("@/utils/authApi", () => {
  const authApi = {
    listUsers: vi.fn(),
    createUser: vi.fn(),
    patchUser: vi.fn(),
    deleteUser: vi.fn(),
  }
  return { authApi, default: authApi }
})

import UsersPane from "./UsersPane.vue"
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
  authApi.listUsers.mockReset()
  authApi.deleteUser.mockReset()
  vi.spyOn(ElMessage, "success").mockImplementation(() => {})
  vi.spyOn(ElMessage, "error").mockImplementation(() => {})
})

afterEach(() => {
  document.body.innerHTML = ""
  vi.restoreAllMocks()
})

async function mountWith(users) {
  authApi.listUsers.mockResolvedValue(users)
  const auth = useAuthStore()
  auth.sameOriginUser = { id: 1, username: "root", role: "admin" }
  const wrapper = mount(UsersPane, { attachTo: document.body, global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().includes(text))
}

describe("UsersPane — render", () => {
  it("lists users and tags the current user as 'you'", async () => {
    const wrapper = await mountWith([
      {
        id: 1,
        username: "root",
        role: "admin",
        is_active: true,
        created_at: null,
        last_login_at: null,
      },
      {
        id: 2,
        username: "bob",
        role: "user",
        is_active: true,
        created_at: null,
        last_login_at: null,
      },
    ])
    expect(authApi.listUsers).toHaveBeenCalled()
    expect(wrapper.text()).toContain("root")
    expect(wrapper.text()).toContain("bob")
    expect(wrapper.text()).toContain("you")
  })

  it("shows the empty state when there are no users", async () => {
    const wrapper = await mountWith([])
    expect(wrapper.text()).toContain("No users")
  })
})

describe("UsersPane — delete flow", () => {
  it("deletes after confirmation", async () => {
    authApi.deleteUser.mockResolvedValueOnce({ status: "deleted", id: 2 })
    const wrapper = await mountWith([
      { id: 2, username: "bob", role: "user", is_active: true, created_at: null },
    ])
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm")
    await buttonByText(wrapper, "Delete").trigger("click")
    await flushPromises()
    expect(authApi.deleteUser).toHaveBeenCalledWith(2)
  })

  it("surfaces the backend last-admin guard as an error toast", async () => {
    authApi.deleteUser.mockRejectedValueOnce({
      response: {
        data: { detail: { error: "last_admin", message: "cannot delete last active admin" } },
      },
    })
    const wrapper = await mountWith([
      { id: 1, username: "root", role: "admin", is_active: true, created_at: null },
    ])
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm")
    await buttonByText(wrapper, "Delete").trigger("click")
    await flushPromises()
    expect(ElMessage.error).toHaveBeenCalledWith("cannot delete last active admin")
  })

  it("does not delete when the confirm is dismissed", async () => {
    const wrapper = await mountWith([
      { id: 2, username: "bob", role: "user", is_active: true, created_at: null },
    ])
    vi.spyOn(ElMessageBox, "confirm").mockRejectedValue("cancel")
    await buttonByText(wrapper, "Delete").trigger("click")
    await flushPromises()
    expect(authApi.deleteUser).not.toHaveBeenCalled()
  })
})
