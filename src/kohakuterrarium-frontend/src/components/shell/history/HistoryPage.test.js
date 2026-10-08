import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { inject, provide } from "vue"

const api = vi.hoisted(() => ({
  list: vi.fn(),
  preflightResume: vi.fn(),
  resume: vi.fn(),
  delete: vi.fn(),
}))
const instances = vi.hoisted(() => ({ fetchOne: vi.fn(), fetchAll: vi.fn() }))
const cluster = vi.hoisted(() => ({ showPickers: false, sites: [] }))
const messages = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), confirm: vi.fn() }))

vi.mock("@/utils/api", () => ({ attachAPI: { getCreaturePolicies: vi.fn() }, sessionAPI: api }))
vi.mock("@/stores/instances", () => ({ useInstancesStore: () => instances }))
vi.mock("@/stores/cluster", () => ({ useClusterStore: () => cluster }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))
vi.mock("element-plus", () => ({
  ElMessage: { success: messages.success, error: messages.error },
  ElMessageBox: { confirm: messages.confirm },
}))
vi.mock("@/components/sessions/modals/BuildEmbeddingsModal.vue", () => ({
  default: {
    name: "BuildEmbeddingsModal",
    props: ["modelValue", "sessionName", "rebuild"],
    template: "<div data-test='build' :data-session='sessionName' :data-rebuild='rebuild' />",
  },
}))

import HistoryPage from "./HistoryPage.vue"
import { useTabsStore } from "@/stores/tabs"
import { installWorkspaceResumeResolver } from "@/utils/workdirPrompt"

const stubs = {
  ElDropdown: {
    emits: ["command"],
    setup(_, { emit }) {
      provide("pick", (cmd) => emit("command", cmd))
    },
    template: "<div><slot /><slot name='dropdown' /></div>",
  },
  ElDropdownMenu: { template: "<div><slot /></div>" },
  ElDropdownItem: {
    props: ["command", "disabled"],
    setup() {
      return { pick: inject("pick") }
    },
    template:
      "<button type='button' :data-cmd='command' :disabled='disabled' @click='pick(command)'><slot /></button>",
  },
  ElSelect: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      "<select :value='modelValue' @change=\"$emit('update:modelValue', $event.target.value)\"><slot /></select>",
  },
  ElOption: { props: ["value", "label"], template: "<option :value='value'>{{ label }}</option>" },
}

const row = (w, key) => w.find(`[data-test="history-row-${key}"]`)
const mountPage = () => mount(HistoryPage, { global: { stubs } })

let uninstall
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  cluster.showPickers = false
  cluster.sites = []
  api.list.mockResolvedValue({
    sessions: [
      {
        name: "saved",
        terrarium_name: "Saved team",
        config_type: "terrarium",
        on_node: "worker-1",
      },
    ],
    total: 1,
  })
  api.preflightResume.mockResolvedValue({ ready: true, gaps: [] })
  api.resume.mockResolvedValue({
    instance_id: "graph_1",
    type: "terrarium",
    session_name: "Saved team",
  })
  instances.fetchOne.mockRejectedValue(new Error("detail unavailable"))
})
afterEach(() => {
  uninstall?.()
  uninstall = undefined
  vi.useRealTimers()
})

describe("HistoryPage: resume", () => {
  it("resumes where the session last ran and opens it named by the server", async () => {
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface").mockResolvedValue()
    const w = mountPage()
    await flushPromises()
    await row(w, "saved").find('[data-test="history-resume"]').trigger("click")
    await flushPromises()
    expect(api.preflightResume).toHaveBeenCalledWith("saved", { onNode: "worker-1" })
    expect(api.resume).toHaveBeenCalledWith(
      "saved",
      expect.objectContaining({ onNode: "worker-1" }),
    )
    expect(openSurface).toHaveBeenCalledWith("graph_1", "chat", {
      config_name: "Saved team",
      type: "terrarium",
    })
    expect(instances.fetchAll).toHaveBeenCalled()
    expect(messages.success).toHaveBeenCalled()
  })

  it("resumes on the picked machine, with the inspector from the row menu", async () => {
    cluster.showPickers = true
    cluster.sites = [
      { nodeId: "_host", isHost: true },
      { nodeId: "worker-2", isHost: false },
    ]
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface").mockResolvedValue()
    const w = mountPage()
    await flushPromises()
    await w.find('[data-test="history-resume-on"]').setValue("worker-2")
    await row(w, "saved").find('[data-cmd="resumeInspector"]').trigger("click")
    await flushPromises()
    expect(api.resume).toHaveBeenCalledWith(
      "saved",
      expect.objectContaining({ onNode: "worker-2" }),
    )
    expect(openSurface.mock.calls.map((c) => c[1])).toEqual(["chat", "inspector"])
  })

  it.each(["history", "cancel"])("a workspace %s choice resumes nothing", async (action) => {
    api.preflightResume.mockResolvedValue({
      ready: false,
      gaps: [{ gap_id: "path:gone", saved_pwd: "/gone" }],
    })
    uninstall = installWorkspaceResumeResolver(() => Promise.resolve({ action }))
    const history = vi.fn()
    window.addEventListener("kt:open-saved-session-history", history)
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface")
    const w = mountPage()
    await flushPromises()
    await row(w, "saved").find('[data-test="history-resume"]').trigger("click")
    await flushPromises()
    window.removeEventListener("kt:open-saved-session-history", history)
    expect(api.resume).not.toHaveBeenCalled()
    expect(openSurface).not.toHaveBeenCalled()
    expect(instances.fetchAll).not.toHaveBeenCalled()
    expect(messages.success).not.toHaveBeenCalled()
    expect(history).toHaveBeenCalledTimes(action === "history" ? 1 : 0)
    expect(row(w, "saved").find('[data-test="history-resume"]').element.disabled).toBe(false)
  })

  it("reports a failed resume and frees the row", async () => {
    api.resume.mockRejectedValue({ response: { data: { detail: "boom" } } })
    const w = mountPage()
    await flushPromises()
    await row(w, "saved").find('[data-test="history-resume"]').trigger("click")
    await flushPromises()
    expect(messages.error).toHaveBeenCalledWith("sessions.resumeFailed")
    expect(row(w, "saved").find('[data-test="history-resume"]').element.disabled).toBe(false)
  })
})

describe("HistoryPage: finding sessions", () => {
  it("sends search and sort together, never a session kind, and drops older replies", async () => {
    vi.useFakeTimers()
    let resolveOld
    api.list.mockReturnValueOnce(new Promise((resolve) => (resolveOld = resolve)))
    const w = mountPage()
    expect(w.find('[data-test^="history-type-"]').exists()).toBe(false)
    await w.find('[data-test="history-sort-created_at"]').trigger("click")
    await w.find('[data-test="history-search"]').setValue("recent")
    resolveOld({ sessions: [{ name: "stale" }], total: 1 })
    await flushPromises()
    expect(w.text()).not.toContain("stale")
    api.list.mockResolvedValue({
      sessions: [{ name: "file-key", terrarium_name: "My project", preview: "recent work" }],
      total: 1,
    })
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith({
      limit: 30,
      offset: 0,
      search: "recent",
      refresh: false,
      sort: "created_at",
    })
    expect(w.text()).toContain("My project")
    expect(w.text()).toContain("file-key")
    expect(w.text()).toContain("recent work")
    w.unmount()
  })

  it("pages through results, rescans on refresh, and says when nothing matches", async () => {
    api.list.mockResolvedValueOnce({ sessions: [{ name: "first" }], total: 70 })
    const w = mountPage()
    await flushPromises()
    api.list.mockResolvedValueOnce({ sessions: [{ name: "second" }], total: 70 })
    await w.find('[data-test="history-next"]').trigger("click")
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 30 }))
    expect(w.text()).toContain("second")
    api.list.mockResolvedValueOnce({ sessions: [], total: 0 })
    await w.find('[data-test="history-sort-created_at"]').trigger("click")
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 0, sort: "created_at" }),
    )
    expect(w.find('[data-test="history-empty"]').text()).toContain("sessions.noSaved")
    api.list.mockResolvedValueOnce({ sessions: [], total: 0 })
    vi.useFakeTimers()
    await w.find('[data-test="history-search"]').setValue("zzz")
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()
    vi.useRealTimers()
    expect(w.find('[data-test="history-empty"]').text()).toContain("sessions.noMatch")
    await w.find('[data-test="history-refresh"]').trigger("click")
    await flushPromises()
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ refresh: true }))
  })

  it("shows a failed listing with retry", async () => {
    api.list.mockRejectedValueOnce(new Error("index locked"))
    const w = mountPage()
    await flushPromises()
    expect(w.find('[role="alert"]').text()).toContain("index locked")
    await w.find('[role="alert"] button').trigger("click")
    await flushPromises()
    expect(w.text()).toContain("Saved team")
  })
})

describe("HistoryPage: row actions", () => {
  it("views a session, builds embeddings, and deletes only after confirmation", async () => {
    const tabs = useTabsStore()
    const openTab = vi.spyOn(tabs, "openTab")
    api.list.mockResolvedValue({
      sessions: [{ name: "saved", terrarium_name: "Saved team" }],
      total: 1,
    })
    const w = mountPage()
    await flushPromises()
    await row(w, "saved").find('[data-test="history-view"]').trigger("click")
    expect(openTab).toHaveBeenCalledWith({
      kind: "session-viewer",
      id: "session:saved",
      name: "saved",
      config_name: "Saved team",
    })
    await row(w, "saved").find('[data-cmd="buildEmbeddings"]').trigger("click")
    expect(w.find('[data-test="build"]').attributes("data-session")).toBe("saved")
    messages.confirm.mockRejectedValueOnce(new Error("cancel"))
    await row(w, "saved").find('[data-cmd="delete"]').trigger("click")
    await flushPromises()
    expect(api.delete).not.toHaveBeenCalled()
    messages.confirm.mockResolvedValueOnce()
    await row(w, "saved").find('[data-cmd="delete"]').trigger("click")
    await flushPromises()
    expect(api.delete).toHaveBeenCalledWith("saved")
    expect(api.list).toHaveBeenLastCalledWith(expect.objectContaining({ refresh: true }))
  })
})
