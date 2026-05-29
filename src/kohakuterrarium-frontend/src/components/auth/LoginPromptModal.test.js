/**
 * LoginPromptModal tests — the global login dialog that makes
 * ``auth.requestLogin()`` (the api.js 401 interceptor + proactive
 * callers) actually surface a UI.  Verifies it tracks the pending
 * prompt and that dismissing it rejects the awaiter instead of hanging.
 */

import { flushPromises, mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import LoginPromptModal from "./LoginPromptModal.vue"
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
})

afterEach(() => {
  document.body.innerHTML = ""
})

describe("LoginPromptModal", () => {
  it("is hidden until a login prompt is pending", async () => {
    const auth = useAuthStore()
    mount(LoginPromptModal, { attachTo: document.body, global: { plugins: [pinia] } })
    await flushPromises()
    expect(auth.pendingLoginPrompt).toBeNull()
    // No dialog body teleported while idle.
    expect(document.querySelector(".el-dialog")).toBeNull()
  })

  it("rejecting the pending prompt resolves the awaiting promise as a rejection", async () => {
    const auth = useAuthStore()
    mount(LoginPromptModal, { attachTo: document.body, global: { plugins: [pinia] } })
    const pending = auth.requestLogin()
    await flushPromises()
    // Simulate the dialog being dismissed.
    auth.rejectLoginPrompt()
    await expect(pending).rejects.toThrow(/cancelled/)
    expect(auth.pendingLoginPrompt).toBeNull()
  })
})
