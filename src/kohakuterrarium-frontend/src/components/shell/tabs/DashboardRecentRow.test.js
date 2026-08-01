import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const { preflightResume, resume, fetchAll, openTab } = vi.hoisted(() => ({
  preflightResume: vi.fn(),
  resume: vi.fn(),
  fetchAll: vi.fn(),
  openTab: vi.fn(),
}))

vi.mock("@/utils/api", () => ({
  sessionAPI: { preflightResume, resume },
}))
vi.mock("@/stores/instances", () => ({
  useInstancesStore: () => ({ fetchAll }),
}))
vi.mock("@/stores/tabs", () => ({
  useTabsStore: () => ({ openTab }),
}))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))
vi.mock("element-plus", () => ({ ElMessage: { success: vi.fn(), error: vi.fn() } }))

import DashboardRecentRow from "./DashboardRecentRow.vue"
import { installWorkspaceResumeResolver } from "@/utils/workdirPrompt"

describe("DashboardRecentRow workspace choices", () => {
  let uninstall
  let history

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    preflightResume.mockResolvedValue({
      ready: false,
      gaps: [{ gap_id: "path:gone", saved_pwd: "/gone" }],
    })
    history = vi.fn()
    window.addEventListener("kt:open-saved-session-history", history)
  })

  afterEach(() => {
    uninstall?.()
    window.removeEventListener("kt:open-saved-session-history", history)
  })

  async function clickResume(action) {
    uninstall = installWorkspaceResumeResolver(() => Promise.resolve({ action }))
    const wrapper = mount(DashboardRecentRow, {
      props: { session: { session_name: "saved", name: "saved", on_node: "worker-1" } },
    })
    const buttons = wrapper.findAll("button")
    await buttons.at(buttons.length - 1).trigger("click")
    await new Promise((resolve) => setTimeout(resolve, 0))
    return wrapper
  }

  it("opens the existing history entry without resume side effects", async () => {
    await clickResume("history")
    expect(history).toHaveBeenCalledTimes(1)
    expect(history.mock.calls[0][0].detail).toEqual({ sessionName: "saved" })
    expect(resume).not.toHaveBeenCalled()
    expect(fetchAll).not.toHaveBeenCalled()
    expect(openTab).not.toHaveBeenCalled()
  })

  it("cancels with zero resume side effects", async () => {
    await clickResume("cancel")
    expect(history).not.toHaveBeenCalled()
    expect(resume).not.toHaveBeenCalled()
    expect(fetchAll).not.toHaveBeenCalled()
    expect(openTab).not.toHaveBeenCalled()
  })
})
