import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { h, ref } from "vue"

const api = vi.hoisted(() => ({
  list: vi.fn(),
  preflightResume: vi.fn(),
  resume: vi.fn(),
  getExchanges: vi.fn(),
}))
const statsAPI = vi.hoisted(() => ({
  diskUsage: vi.fn(async () => ({})),
  metrics: vi.fn(async () => ({})),
}))
const instances = vi.hoisted(() => ({ fetchOne: vi.fn() }))
const toast = vi.hoisted(() => ({ error: vi.fn() }))
vi.mock("@/utils/api", () => ({
  attachAPI: { getCreaturePolicies: vi.fn() },
  sessionAPI: api,
  statsAPI,
}))
vi.mock("@/stores/instances", () => ({ useInstancesStore: () => instances }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (k) => k }) }))
vi.mock("element-plus", () => ({ ElMessage: toast }))

import LabRecentPanel from "./LabRecentPanel.vue"
import { RECENT_LIMIT, useRecentSessions } from "@/components/lab/composables/useRecentSessions"
import { useTabsStore } from "@/stores/tabs"
import { installWorkspaceResumeResolver } from "@/utils/workdirPrompt"

enableAutoUnmount(afterEach)

const key = ref("")
function mountPanel() {
  return mount({
    setup() {
      const recent = useRecentSessions(key)
      return () => h(LabRecentPanel, { recent })
    },
  })
}

let uninstall
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  localStorage.clear()
  key.value = ""
  api.getExchanges.mockResolvedValue({
    exchanges: [{ turn: 4, user: "Why is CI red?", reply: "A flaky timeout." }],
  })
  api.list.mockResolvedValue({
    total: 40,
    sessions: [
      {
        name: "saved_ab12",
        terrarium_name: "Research notes",
        title: "Nightly triage",
        summary: "Triaging CI",
        stop_reason: "crash",
        on_node: "worker-1",
        agents: ["a", "b"],
      },
      {
        name: "plain_cd34",
        terrarium_name: "plain",
        config_path: "/x/general",
        agents: ["a"],
        turn_count: 3,
      },
    ],
  })
  api.preflightResume.mockResolvedValue({ ready: true, gaps: [] })
  api.resume.mockResolvedValue({
    instance_id: "graph_1",
    type: "terrarium",
    session_name: "Research notes",
  })
  instances.fetchOne.mockRejectedValue(new Error("no detail"))
})
afterEach(() => {
  uninstall?.()
  uninstall = undefined
})

describe("LabRecentPanel", () => {
  it("leads each row with its summary, names it only by the user's title, and shows no config, members or id", async () => {
    const w = mountPanel()
    await flushPromises()
    expect(api.list).toHaveBeenCalledWith({ limit: RECENT_LIMIT, sort: "last_active" })
    expect(w.find("[data-test='lab-recent']").text()).toContain("40")
    const row = w.find("[data-test='lab-recent-saved_ab12']")
    expect(row.find("[data-test='lab-recent-headline']").text()).toBe("Triaging CI")
    expect(row.find("[data-test='lab-recent-title']").text()).toBe("Nightly triage")
    expect(row.find("[data-test='lab-recent-status-crashed']").exists()).toBe(true)
    const plain = w.find("[data-test='lab-recent-plain_cd34']")
    expect(plain.find("[data-test='lab-recent-headline']").text()).toBe("plain")
    expect(plain.find("[data-test='lab-recent-title']").exists()).toBe(false)
    for (const hidden of ["general", "agentCount", "turns", "plain_cd34", "saved_ab12"]) {
      expect(w.find("[data-test='lab-recent']").text()).not.toContain(hidden)
    }
  })

  it("opens one row at a time to its whole summary and quoted messages, nothing else", async () => {
    const w = mountPanel()
    await flushPromises()
    const toggle = (k) => w.find(`[data-test='lab-recent-${k}'] [data-test='lab-recent-toggle']`)
    await toggle("saved_ab12").trigger("click")
    await flushPromises()
    const open = w.find("[data-test='lab-recent-saved_ab12']")
    expect(open.find("[data-test='lab-recent-headline']").text()).toContain("Triaging CI")
    expect(open.find("[data-test='lab-recent-headline']").classes()).not.toContain("line-clamp-2")
    const detail = open.find("[data-test='lab-recent-detail']")
    expect(api.getExchanges).toHaveBeenCalledWith("saved_ab12", 3)
    expect(detail.text()).toContain("Why is CI red?")
    expect(detail.text()).toContain("A flaky timeout.")
    expect(detail.text()).not.toContain("saved_ab12")
    expect(detail.find("[data-test='lab-recent-view']").exists()).toBe(true)
    expect(toggle("saved_ab12").attributes("aria-expanded")).toBe("true")
    await toggle("plain_cd34").trigger("click")
    expect(
      w.find("[data-test='lab-recent-saved_ab12'] [data-test='lab-recent-detail']").exists(),
    ).toBe(false)
    expect(w.find("[data-test='lab-recent-plain_cd34']").text()).not.toContain("/x/general")
    await toggle("plain_cd34").trigger("click")
    expect(w.find("[data-test='lab-recent-detail']").exists()).toBe(false)
  })

  it("collapses to a strip and resizes by its grip, remembering both", async () => {
    const w = mountPanel()
    await flushPromises()
    const panel = () => w.find("[data-test='lab-recent']")
    expect(panel().attributes("style")).toContain("width: 320px")
    const grip = w.find("[data-test='lab-recent-grip']")
    vi.spyOn(panel().element, "getBoundingClientRect").mockReturnValue({ left: 1000, right: 1320 })
    await grip.trigger("pointerdown", { pointerId: 1 })
    document.dispatchEvent(new MouseEvent("pointermove", { clientX: 900 }))
    document.dispatchEvent(new MouseEvent("pointerup"))
    await flushPromises()
    expect(panel().attributes("style")).toContain("width: 420px")
    expect(localStorage.getItem("kt.lab.recent.width")).toBe("420")
    await grip.trigger("dblclick")
    expect(panel().attributes("style")).toContain("width: 320px")
    await w.find("[data-test='lab-recent-collapse']").trigger("click")
    expect(w.find("[data-test='lab-recent-grip']").exists()).toBe(false)
    expect(w.find("[data-test='lab-recent-saved_ab12']").exists()).toBe(false)
    expect(localStorage.getItem("kt.lab.recent.collapsed")).toBe("1")
    w.unmount()
    const again = mountPanel()
    await flushPromises()
    expect(again.find("[data-test='lab-recent-grip']").exists()).toBe(false)
    await again.find("[data-test='lab-recent-collapse']").trigger("click")
    expect(again.find("[data-test='lab-recent-saved_ab12']").exists()).toBe(true)
  })

  it("views a session from its open row and opens History from All history", async () => {
    const tabs = useTabsStore()
    const openTab = vi.spyOn(tabs, "openTab")
    const w = mountPanel()
    await flushPromises()
    await w
      .find("[data-test='lab-recent-saved_ab12'] [data-test='lab-recent-toggle']")
      .trigger("click")
    expect(openTab).not.toHaveBeenCalled()
    await w.find("[data-test='lab-recent-view']").trigger("click")
    expect(openTab).toHaveBeenLastCalledWith(
      expect.objectContaining({
        kind: "session-viewer",
        id: "session:saved_ab12",
        name: "saved_ab12",
      }),
    )
    await w.find("[data-test='lab-recent-all']").trigger("click")
    expect(openTab).toHaveBeenLastCalledWith({ kind: "saved-sessions", id: "saved-sessions" })
  })

  it("resumes where the session last ran without viewing it, and opens it named by the server", async () => {
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface").mockResolvedValue()
    const openTab = vi.spyOn(tabs, "openTab")
    const w = mountPanel()
    await flushPromises()
    await w
      .find("[data-test='lab-recent-saved_ab12'] [data-test='lab-recent-resume']")
      .trigger("click")
    await flushPromises()
    expect(api.resume).toHaveBeenCalledWith(
      "saved_ab12",
      expect.objectContaining({ onNode: "worker-1" }),
    )
    expect(openSurface).toHaveBeenCalledWith("graph_1", "chat", {
      config_name: "Research notes",
      type: "terrarium",
    })
    expect(openTab).not.toHaveBeenCalledWith(expect.objectContaining({ kind: "session-viewer" }))
  })

  it.each(["history", "cancel"])("a workspace %s choice resumes nothing", async (action) => {
    api.preflightResume.mockResolvedValue({
      ready: false,
      gaps: [{ gap_id: "path:gone", saved_pwd: "/gone" }],
    })
    uninstall = installWorkspaceResumeResolver(() => Promise.resolve({ action }))
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface")
    const w = mountPanel()
    await flushPromises()
    await w.find("[data-test='lab-recent-resume']").trigger("click")
    await flushPromises()
    expect(api.resume).not.toHaveBeenCalled()
    expect(openSurface).not.toHaveBeenCalled()
  })

  it("shows a failed resume and a failed load", async () => {
    api.resume.mockRejectedValue(new Error("boom"))
    const w = mountPanel()
    await flushPromises()
    await w.find("[data-test='lab-recent-resume']").trigger("click")
    await flushPromises()
    expect(toast.error).toHaveBeenCalledWith("sessions.resumeFailed")
    api.list.mockRejectedValue(new Error("offline"))
    key.value = "changed"
    await flushPromises()
    expect(w.find("[role='alert']").text()).toBe("offline")
  })

  it("keeps the newest reply when an older one lands late", async () => {
    let releaseOld
    api.list.mockReturnValueOnce(new Promise((resolve) => (releaseOld = resolve)))
    api.list.mockResolvedValueOnce({
      total: 1,
      sessions: [{ name: "new_1", terrarium_name: "fresh" }],
    })
    const w = mountPanel()
    key.value = "next"
    await flushPromises()
    releaseOld({ total: 1, sessions: [{ name: "old_1", terrarium_name: "stale" }] })
    await flushPromises()
    expect(w.find("[data-test='lab-recent-new_1']").exists()).toBe(true)
    expect(w.find("[data-test='lab-recent-old_1']").exists()).toBe(false)
  })
})
