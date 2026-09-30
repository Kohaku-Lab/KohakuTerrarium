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

describe("DashboardRecentRow summary", () => {
  function rowText(session) {
    return mount(DashboardRecentRow, { props: { session } }).text()
  }

  it("shows the file size the saved-session list actually sends", () => {
    expect(rowText({ name: "saved", file_size: 2048 })).toContain("2 KB")
    expect(rowText({ name: "saved", file_size: 5 * 1024 * 1024 })).toContain("5.0 MB")
    expect(rowText({ name: "saved", file_size: 300 })).toContain("300 B")
  })

  it("still accepts the older size field names and hides an unknown size", () => {
    expect(rowText({ name: "saved", size_bytes: 4096 })).toContain("4 KB")
    expect(rowText({ name: "saved" })).not.toMatch(/\d\s?(B|KB|MB)\b/)
  })

  it("shows the name from the payload and hides turns that are not sent", () => {
    const text = rowText({ name: "saved_ab12cd34", file_size: 100 })
    expect(text).toContain("saved_ab12cd34")
    expect(text).not.toContain("turns")
  })
})

describe("DashboardRecentRow resumed tab", () => {
  async function resumeWith(response) {
    preflightResume.mockResolvedValue({ ready: true })
    resume.mockResolvedValue(response)
    fetchAll.mockResolvedValue()
    const wrapper = mount(DashboardRecentRow, {
      props: { session: { name: "saved_ab12cd34", file_size: 10 } },
    })
    const buttons = wrapper.findAll("button")
    await buttons.at(buttons.length - 1).trigger("click")
    await new Promise((resolve) => setTimeout(resolve, 0))
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it("labels the attach tab with the name the server returns, not the file stem", async () => {
    await resumeWith({ instance_id: "graph_1", type: "agent", session_name: "swe" })
    expect(openTab).toHaveBeenCalledTimes(1)
    expect(openTab.mock.calls[0][0]).toMatchObject({
      kind: "attach",
      target: "graph_1",
      config_name: "swe",
      type: "creature",
    })
  })

  it("opens a terrarium session as a terrarium tab", async () => {
    await resumeWith({ instance_id: "graph_2", type: "terrarium", session_name: "team" })
    expect(openTab.mock.calls[0][0]).toMatchObject({ config_name: "team", type: "terrarium" })
  })
})

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
