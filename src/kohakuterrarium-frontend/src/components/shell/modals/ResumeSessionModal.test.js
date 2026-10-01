import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const { list, preflightResume, resume, fetchOne } = vi.hoisted(() => ({
  list: vi.fn(),
  preflightResume: vi.fn(),
  resume: vi.fn(),
  fetchOne: vi.fn(),
}))

vi.mock("@/utils/api", () => ({
  attachAPI: { getCreaturePolicies: vi.fn() },
  sessionAPI: { list, preflightResume, resume },
}))
vi.mock("@/stores/instances", () => ({
  useInstancesStore: () => ({ fetchOne }),
}))
vi.mock("@/components/cluster/SitePicker.vue", () => ({
  default: { name: "SitePicker", template: "<div />" },
}))
vi.mock("@/components/common/ModalShell.vue", () => ({
  default: {
    name: "ModalShell",
    template: "<div><slot name='title' /><slot /><slot name='footer' /></div>",
  },
}))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

import ResumeSessionModal from "./ResumeSessionModal.vue"
import { installWorkspaceResumeResolver } from "@/utils/workdirPrompt"
import { useTabsStore } from "@/stores/tabs"

const sessionName = "saved-session"

function expectNoRuntimeSideEffects(tabs, openSurface) {
  expect(resume).not.toHaveBeenCalled()
  expect(fetchOne).not.toHaveBeenCalled()
  expect(openSurface).not.toHaveBeenCalled()
  expect(tabs.tabs).toHaveLength(0)
}

describe("ResumeSessionModal session rows", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it("shows the file size the saved-session list actually sends", async () => {
    list.mockResolvedValue({
      sessions: [
        { name: "one", file_size: 2048 },
        { name: "two", size_bytes: 4096 },
        { name: "three" },
      ],
    })
    const wrapper = mount(ResumeSessionModal)
    await flushPromises()

    const rows = wrapper
      .findAll('input[type="radio"]')
      .map((input) => input.element.closest("label").textContent)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toContain("2 KB")
    expect(rows[1]).toContain("4 KB")
    expect(rows[2]).not.toMatch(/\d\s?(B|KB|MB)\b/)
  })
})

describe("ResumeSessionModal workspace preflight choices", () => {
  let uninstall
  let history

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    list.mockResolvedValue({ sessions: [{ session_name: sessionName, on_node: "worker-1" }] })
    preflightResume.mockResolvedValue({
      ready: false,
      gaps: [{ gap_id: "path:missing", saved_pwd: "/missing" }],
    })
    history = vi.fn()
    window.addEventListener("kt:open-saved-session-history", history)
  })

  afterEach(() => {
    uninstall?.()
    uninstall = undefined
    window.removeEventListener("kt:open-saved-session-history", history)
  })

  async function submitWithChoice(action) {
    uninstall = installWorkspaceResumeResolver(() => Promise.resolve({ action }))
    const tabs = useTabsStore()
    const createSession = vi.spyOn(tabs, "createSession")
    const openSurface = vi.spyOn(tabs, "openSurface")
    const wrapper = mount(ResumeSessionModal)

    await flushPromises()
    await wrapper.get(`input[type="radio"][value="${sessionName}"]`).setValue(true)
    const resumeButton = wrapper
      .findAll("button")
      .find((button) => button.text() === "shell.modal.resume.resume")
    expect(resumeButton).toBeDefined()
    await resumeButton.trigger("click")
    await flushPromises()

    return { wrapper, tabs, createSession, openSurface }
  }

  it("opens existing history and closes without runtime side effects", async () => {
    const { wrapper, tabs, createSession, openSurface } = await submitWithChoice("history")

    expect(createSession).toHaveBeenCalledOnce()
    expect(createSession).toHaveBeenCalledWith({
      kind: "resume",
      sessionName,
      attachMode: "chat",
      onNode: "worker-1",
    })
    expect(preflightResume).toHaveBeenCalledOnce()
    expect(preflightResume).toHaveBeenCalledWith(sessionName, { onNode: "worker-1" })
    expect(history).toHaveBeenCalledOnce()
    expect(history.mock.calls[0][0].detail).toEqual({ sessionName })
    expect(wrapper.emitted("close")).toHaveLength(1)
    expectNoRuntimeSideEffects(tabs, openSurface)
  })

  it("keeps the modal open on cancel without runtime side effects", async () => {
    const { wrapper, tabs, createSession, openSurface } = await submitWithChoice("cancel")

    expect(createSession).toHaveBeenCalledOnce()
    expect(createSession).toHaveBeenCalledWith({
      kind: "resume",
      sessionName,
      attachMode: "chat",
      onNode: "worker-1",
    })
    expect(preflightResume).toHaveBeenCalledOnce()
    expect(preflightResume).toHaveBeenCalledWith(sessionName, { onNode: "worker-1" })
    expect(history).not.toHaveBeenCalled()
    expect(wrapper.emitted("close")).toBeUndefined()
    expectNoRuntimeSideEffects(tabs, openSurface)
  })
})

describe("ResumeSessionModal server discovery", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  it("pages past the first results and searches all saved sessions using storage keys", async () => {
    list.mockResolvedValueOnce({
      sessions: [{ name: "file-1", agents: ["Named agent"] }],
      total: 80,
    })
    const wrapper = mount(ResumeSessionModal)
    await flushPromises()
    expect(wrapper.text()).toContain("Named agent")
    await wrapper.get('input[type="radio"]').setValue(true)
    list.mockResolvedValueOnce({ sessions: [{ name: "file-21" }], total: 80 })
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "sessions.next")
      .trigger("click")
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith({ limit: 20, offset: 20, search: "" })
    expect(wrapper.get(".btn-primary").attributes("disabled")).toBeDefined()
    list.mockResolvedValueOnce({
      sessions: [{ name: "file-77", agents: ["Distant result"] }],
      total: 1,
    })
    await wrapper.get('input[type="text"]').setValue("Distant")
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith({ limit: 20, offset: 0, search: "Distant" })
    expect(wrapper.get('input[type="radio"]').attributes("value")).toBe("file-77")
    wrapper.unmount()
  })

  it("ignores stale results during the debounce and cancels requests on unmount", async () => {
    let resolveOld
    list.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveOld = resolve
      }),
    )
    const wrapper = mount(ResumeSessionModal)
    await wrapper.get('input[type="text"]').setValue("new")
    resolveOld({ sessions: [{ name: "stale" }], total: 1 })
    await flushPromises()
    expect(wrapper.text()).not.toContain("stale")
    list.mockResolvedValueOnce({ sessions: [{ name: "fresh" }], total: 1 })
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()
    expect(wrapper.text()).toContain("fresh")
    await wrapper.get('input[type="text"]').setValue("abandoned")
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(300)
    expect(list).toHaveBeenCalledTimes(2)
  })
})

it("keeps the resume response name when the detail refresh fails", async () => {
  setActivePinia(createPinia())
  preflightResume.mockResolvedValue({ ready: true, gaps: [] })
  resume.mockResolvedValue({
    instance_id: "graph_resumed",
    session_name: "My saved project",
    type: "agent",
  })
  fetchOne.mockRejectedValue(new Error("temporary detail failure"))
  const tabs = useTabsStore()
  await tabs.createSession({ kind: "resume", sessionName: "file-key", alsoOpenInspector: true })
  const surfaces = tabs.tabs.filter((tab) => tab.target === "graph_resumed")
  expect(surfaces).toHaveLength(2)
  expect(surfaces.every((tab) => tab.config_name === "My saved project")).toBe(true)
})
