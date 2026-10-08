import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({ list: vi.fn(), preflightResume: vi.fn(), resume: vi.fn() }))
const instances = vi.hoisted(() => ({ fetchOne: vi.fn() }))
vi.mock("@/utils/api", () => ({ attachAPI: { getCreaturePolicies: vi.fn() }, sessionAPI: api }))
vi.mock("@/stores/instances", () => ({ useInstancesStore: () => instances }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (k) => k }) }))
vi.mock("element-plus", () => ({ ElMessage: { error: vi.fn() } }))

import LabHistoryBubble from "./LabHistoryBubble.vue"
import { useTabsStore } from "@/stores/tabs"
import { installWorkspaceResumeResolver } from "@/utils/workdirPrompt"

let storage
let uninstall
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (k) => storage.get(k) ?? null,
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  })
  api.list.mockResolvedValue({
    sessions: [
      {
        name: "saved_ab12",
        terrarium_name: "Research notes",
        on_node: "worker-1",
        agents: ["a", "b"],
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
  vi.unstubAllGlobals()
})

describe("LabHistoryBubble", () => {
  it("lists the last five by display name and views by storage key", async () => {
    const tabs = useTabsStore()
    const openTab = vi.spyOn(tabs, "openTab")
    const w = mount(LabHistoryBubble)
    await flushPromises()
    expect(api.list).toHaveBeenCalledWith({ limit: 5, sort: "last_active" })
    expect(w.text()).toContain("Research notes")
    await w.find("[data-test='lab-history-saved_ab12'] button").trigger("click")
    expect(openTab).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: "session:saved_ab12", name: "saved_ab12" }),
    )
    await w.find("[data-test='lab-history-all']").trigger("click")
    expect(openTab).toHaveBeenLastCalledWith({ kind: "saved-sessions", id: "saved-sessions" })
  })

  it("resumes where the session last ran and opens it named by the server", async () => {
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface").mockResolvedValue()
    const w = mount(LabHistoryBubble)
    await flushPromises()
    await w.find("[data-test='lab-history-resume']").trigger("click")
    await flushPromises()
    expect(api.resume).toHaveBeenCalledWith(
      "saved_ab12",
      expect.objectContaining({ onNode: "worker-1" }),
    )
    expect(openSurface).toHaveBeenCalledWith("graph_1", "chat", {
      config_name: "Research notes",
      type: "terrarium",
    })
  })

  it.each(["history", "cancel"])("a workspace %s choice resumes nothing", async (action) => {
    api.preflightResume.mockResolvedValue({
      ready: false,
      gaps: [{ gap_id: "path:gone", saved_pwd: "/gone" }],
    })
    uninstall = installWorkspaceResumeResolver(() => Promise.resolve({ action }))
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface")
    const w = mount(LabHistoryBubble)
    await flushPromises()
    await w.find("[data-test='lab-history-resume']").trigger("click")
    await flushPromises()
    expect(api.resume).not.toHaveBeenCalled()
    expect(openSurface).not.toHaveBeenCalled()
  })

  it("starts closed on a busy bench, open on an empty one, and then keeps the user's choice", async () => {
    const w = mount(LabHistoryBubble, { props: { defaultOpen: false } })
    await flushPromises()
    expect(w.find("[data-test='lab-history-all']").exists()).toBe(false)
    await w.setProps({ defaultOpen: true })
    expect(w.find("[data-test='lab-history-all']").exists()).toBe(true)
    await w.find("[data-test='lab-history-toggle']").trigger("click")
    expect(storage.get("kt.lab.historyOpen")).toBe("0")
    await w.setProps({ defaultOpen: true })
    expect(w.find("[data-test='lab-history-all']").exists()).toBe(false)
    w.unmount()
    const again = mount(LabHistoryBubble, { props: { defaultOpen: true } })
    expect(again.find("[data-test='lab-history-all']").exists()).toBe(false)
  })

  it("re-reads when the running set changes", async () => {
    const w = mount(LabHistoryBubble, { props: { refreshKey: "a" } })
    await flushPromises()
    await w.setProps({ refreshKey: "a,b" })
    await flushPromises()
    expect(api.list).toHaveBeenCalledTimes(2)
  })
})
