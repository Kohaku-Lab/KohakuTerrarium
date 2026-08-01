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
