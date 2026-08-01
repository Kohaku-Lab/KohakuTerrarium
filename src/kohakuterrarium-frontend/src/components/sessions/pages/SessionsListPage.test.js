import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const { list, preflightResume, resume, fetchAll, push } = vi.hoisted(() => ({
  list: vi.fn(),
  preflightResume: vi.fn(),
  resume: vi.fn(),
  fetchAll: vi.fn(),
  push: vi.fn(),
}))

vi.mock("@/utils/api", () => ({ sessionAPI: { list, preflightResume, resume } }))
vi.mock("@/stores/instances", () => ({ useInstancesStore: () => ({ fetchAll }) }))
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))
vi.mock("element-plus", () => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: {},
}))

import SessionsListPage from "./SessionsListPage.vue"
import { installWorkspaceResumeResolver } from "@/utils/workdirPrompt"

describe("SessionsListPage workspace choices", () => {
  let uninstall
  let history

  beforeEach(() => {
    globalThis.useRouter = () => ({ push })
    setActivePinia(createPinia())
    vi.clearAllMocks()
    list.mockResolvedValue({ sessions: [{ name: "saved", on_node: "worker-1" }], total: 1 })
    preflightResume.mockResolvedValue({
      ready: false,
      gaps: [{ gap_id: "path:gone", saved_pwd: "/gone" }],
    })
    history = vi.fn()
    window.addEventListener("kt:open-saved-session-history", history)
  })

  afterEach(() => {
    delete globalThis.useRouter
    uninstall?.()
    window.removeEventListener("kt:open-saved-session-history", history)
  })

  async function clickResume(action) {
    uninstall = installWorkspaceResumeResolver(() => Promise.resolve({ action }))
    const onResume = vi.fn()
    const wrapper = mount(SessionsListPage, {
      props: { onResume },
      global: {
        stubs: {
          ElDropdown: true,
          ElDropdownMenu: true,
          ElDropdownItem: true,
          BuildEmbeddingsModal: true,
        },
      },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.get(".btn-primary").trigger("click")
    await new Promise((resolve) => setTimeout(resolve, 0))
    return onResume
  }

  it("opens persisted history without resume side effects", async () => {
    const onResume = await clickResume("history")
    expect(history).toHaveBeenCalledTimes(1)
    expect(history.mock.calls[0][0].detail).toEqual({ sessionName: "saved" })
    expect(resume).not.toHaveBeenCalled()
    expect(fetchAll).not.toHaveBeenCalled()
    expect(onResume).not.toHaveBeenCalled()
    expect(push).not.toHaveBeenCalled()
  })

  it("cancels with zero resume side effects", async () => {
    const onResume = await clickResume("cancel")
    expect(history).not.toHaveBeenCalled()
    expect(resume).not.toHaveBeenCalled()
    expect(fetchAll).not.toHaveBeenCalled()
    expect(onResume).not.toHaveBeenCalled()
    expect(push).not.toHaveBeenCalled()
  })
})
