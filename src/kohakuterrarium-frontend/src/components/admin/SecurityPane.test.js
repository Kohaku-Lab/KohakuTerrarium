/**
 * SecurityPane tests — renders the masked host/admin token status and
 * drives live rotation (confirm -> rotate -> one-time plaintext shown).
 * The store-level host-record sync is covered by stores/admin.test.js.
 */

import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"
import { ElMessage, ElMessageBox } from "element-plus"

vi.mock("@/utils/authApi", () => {
  const authApi = {
    tokenStatus: vi.fn(),
    rotateHostToken: vi.fn(),
    rotateAdminToken: vi.fn(),
  }
  return { authApi, default: authApi }
})

import SecurityPane from "./SecurityPane.vue"
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
  authApi.tokenStatus.mockReset().mockResolvedValue({
    host_token: { enabled: true, tail: "abc123" },
    admin_token: { enabled: false, tail: "" },
  })
  authApi.rotateHostToken.mockReset()
  authApi.rotateAdminToken.mockReset()
  vi.spyOn(ElMessage, "success").mockImplementation(() => {})
  vi.spyOn(ElMessage, "error").mockImplementation(() => {})
})

afterEach(() => {
  document.body.innerHTML = ""
  vi.restoreAllMocks()
})

async function mountPane() {
  const wrapper = mount(SecurityPane, { attachTo: document.body, global: { plugins: [pinia] } })
  await flushPromises()
  return wrapper
}

function buttonByText(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().includes(text))
}

describe("SecurityPane — status", () => {
  it("renders enabled (with tail) and disabled token status", async () => {
    const wrapper = await mountPane()
    expect(authApi.tokenStatus).toHaveBeenCalled()
    expect(wrapper.text()).toContain("abc123") // host token tail
    expect(wrapper.text().toLowerCase()).toContain("disabled") // admin token off
  })
})

describe("SecurityPane — rotation", () => {
  it("rotates the host token and shows the new value once", async () => {
    authApi.rotateHostToken.mockResolvedValueOnce({ token: "NEWHOST123", field: "host_token" })
    vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm")
    const wrapper = await mountPane()
    await buttonByText(wrapper, "Rotate host token").trigger("click")
    await flushPromises()
    expect(authApi.rotateHostToken).toHaveBeenCalled()
    expect(wrapper.text()).toContain("NEWHOST123")
  })

  it("does not rotate when the confirm is dismissed", async () => {
    vi.spyOn(ElMessageBox, "confirm").mockRejectedValue("cancel")
    const wrapper = await mountPane()
    await buttonByText(wrapper, "Rotate admin token").trigger("click")
    await flushPromises()
    expect(authApi.rotateAdminToken).not.toHaveBeenCalled()
  })
})
