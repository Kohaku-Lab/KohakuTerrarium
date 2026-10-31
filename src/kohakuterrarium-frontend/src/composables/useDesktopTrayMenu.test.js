import { mount } from "@vue/test-utils"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { defineComponent, reactive } from "vue"

const mocks = vi.hoisted(() => ({
  setTraySessions: vi.fn(async () => true),
  pickFolderNative: vi.fn(),
  navigateToAttention: vi.fn(() => true),
  openSessionScope: vi.fn(() => true),
  hasDesktopHost: vi.fn(() => true),
  list: [],
}))

vi.mock("@/utils/hostBridge", () => ({
  hasDesktopHost: mocks.hasDesktopHost,
  onHostReady: (listener) => {
    listener()
    return () => {}
  },
  pickFolderNative: mocks.pickFolderNative,
  setTraySessions: mocks.setTraySessions,
}))
vi.mock("@/utils/attentionNavigation", () => ({
  attentionTargetLabel: ({ scope, tab }) => `${scope}/${tab}`,
  navigateToAttention: mocks.navigateToAttention,
  openSessionScope: mocks.openSessionScope,
}))
vi.mock("@/stores/instances", () => ({ useInstancesStore: () => mocks.instances }))
vi.mock("@/utils/i18n", () => ({ useI18n: () => ({ t: (key) => key }) }))

import { OPEN_WORKSPACE_EVENT } from "@/composables/useDesktopOpen"
import {
  TRAY_ACTION_EVENT,
  buildTrayModel,
  useDesktopTrayMenu,
} from "@/composables/useDesktopTrayMenu"
import { clearAttentionRegistry, createAttentionState, publishAttention } from "@/stores/attention"

function mountMenu() {
  return mount(
    defineComponent({
      setup() {
        useDesktopTrayMenu()
        return () => null
      },
    }),
  )
}

async function settle() {
  await vi.advanceTimersByTimeAsync(300)
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.clearAllMocks()
  mocks.hasDesktopHost.mockReturnValue(true)
  mocks.instances = reactive({
    list: [
      { id: "i1", graph_id: "g1", session_name: "Alpha" },
      { id: "i2", config_name: "Beta" },
    ],
  })
  clearAttentionRegistry()
})

afterEach(() => {
  vi.useRealTimers()
  clearAttentionRegistry()
})

describe("buildTrayModel", () => {
  it("lists each waiting scope once and every listed session by its best name", () => {
    const model = buildTrayModel(
      [
        { scope: "g1", tab: "root", pending: 1 },
        { scope: "g1", tab: "helper", pending: 2 },
      ],
      [
        { id: "i1", graph_id: "g1", session_name: "Alpha" },
        { id: "i2", config_name: "Beta" },
        { id: "i2", config_name: "Beta again" },
        { session_name: "no id" },
      ],
      ({ scope, tab }) => `${scope}/${tab}`,
    )
    expect(model.waiting).toEqual([{ id: "g1", label: "g1/root" }])
    expect(model.sessions).toEqual([
      { id: "g1", label: "Alpha" },
      { id: "i2", label: "Beta" },
    ])
  })
})

describe("useDesktopTrayMenu", () => {
  it("sends the session entries once, even when nothing about them changed", async () => {
    const wrapper = mountMenu()
    await settle()
    expect(mocks.setTraySessions).toHaveBeenCalledTimes(1)
    expect(mocks.setTraySessions).toHaveBeenLastCalledWith({
      waiting: [],
      sessions: [
        { id: "g1", label: "Alpha" },
        { id: "i2", label: "Beta" },
      ],
    })

    publishAttention("g1", "root", {
      ...createAttentionState(),
      pending: new Set(["ask"]),
    })
    publishAttention("g1", "root", {
      ...createAttentionState(),
      pending: new Set(["ask"]),
    })
    await settle()
    expect(mocks.setTraySessions).toHaveBeenCalledTimes(2)
    expect(mocks.setTraySessions.mock.calls[1][0].waiting).toEqual([{ id: "g1", label: "g1/root" }])

    publishAttention("g1", "root", { ...createAttentionState(), completed: 1 })
    await settle()
    expect(mocks.setTraySessions.mock.calls.at(-1)[0].waiting).toEqual([])
    wrapper.unmount()
  })

  it("follows the session list", async () => {
    const wrapper = mountMenu()
    await settle()
    mocks.instances.list = [{ id: "i3", session_name: "Gamma" }]
    await settle()
    expect(mocks.setTraySessions.mock.calls.at(-1)[0].sessions).toEqual([
      { id: "i3", label: "Gamma" },
    ])
    wrapper.unmount()
  })

  it("does nothing without a desktop shell", async () => {
    mocks.hasDesktopHost.mockReturnValue(false)
    const wrapper = mountMenu()
    await settle()
    expect(mocks.setTraySessions).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it("opens a waiting session on its waiting tab and any other session in its own tab", async () => {
    const wrapper = mountMenu()
    publishAttention("g1", "root", {
      ...createAttentionState(),
      pending: new Set(["ask"]),
    })
    const act = (kind, id = "") =>
      window.dispatchEvent(new CustomEvent(TRAY_ACTION_EVENT, { detail: { kind, id } }))

    act("session", "g1")
    expect(mocks.navigateToAttention).toHaveBeenCalledWith({
      scope: "g1",
      tab: "root",
      pending: 1,
    })
    act("session", "i2")
    expect(mocks.openSessionScope).toHaveBeenCalledWith("i2")
    wrapper.unmount()
  })

  it("opens the new-session dialog, with or without a folder from the OS dialog", async () => {
    const wrapper = mountMenu()
    const seen = []
    const listener = (event) => seen.push(event.detail.path)
    window.addEventListener(OPEN_WORKSPACE_EVENT, listener)
    const act = (kind) =>
      window.dispatchEvent(new CustomEvent(TRAY_ACTION_EVENT, { detail: { kind } }))

    act("new")
    expect(seen).toEqual([""])

    mocks.pickFolderNative.mockResolvedValue("C:\\work\\repo")
    act("folder")
    await vi.advanceTimersByTimeAsync(0)
    expect(seen).toEqual(["", "C:\\work\\repo"])

    mocks.pickFolderNative.mockResolvedValue(null)
    act("folder")
    await vi.advanceTimersByTimeAsync(0)
    expect(seen).toHaveLength(2)

    window.removeEventListener(OPEN_WORKSPACE_EVENT, listener)
    wrapper.unmount()
    act("new")
    expect(seen).toHaveLength(2)
  })
})
