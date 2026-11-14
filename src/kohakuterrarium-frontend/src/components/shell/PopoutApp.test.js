import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { markRaw } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const store = vi.hoisted(() => ({
  current: null,
  list: [],
  fetchAll: vi.fn(async () => []),
  fetchOne: vi.fn(async () => null),
}))
vi.mock("@/stores/instances", () => ({ useInstancesStore: () => store }))

import PopoutApp from "@/components/shell/PopoutApp.vue"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { _resetForTests } from "@/composables/useScope"

const AuthGate = { template: "<div><slot /></div>" }
const AttachTab = {
  props: ["tab"],
  template: '<div data-test="attach" :data-target="tab.target" :data-kind="tab.kind" />',
}
const ToastCenter = { template: '<div data-test="toasts" />' }

function mountApp(scope = "graph-7") {
  return mount(PopoutApp, {
    props: { scope },
    global: { stubs: { AuthGate, AttachTab, ToastCenter } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  store.current = null
  store.list = []
})
afterEach(() => {
  _resetForTests()
  delete window.__TAURI_INTERNALS__
})

describe("PopoutApp", () => {
  it("owns its visible chat while the observation reconnects and releases it on close", async () => {
    const chat = useChatStore("graph-7")
    const socket = { close: vi.fn() }
    chat._ws = markRaw(socket)
    chat._instanceId = "graph-7"
    chat._instanceType = "terrarium"
    chat.tabs = ["alice", "bob"]
    chat.activeTab = "alice"
    store.fetchOne.mockResolvedValueOnce({
      id: "graph-7",
      type: "terrarium",
      creatures: [
        { creature_id: "a", name: "alice" },
        { creature_id: "b", name: "bob" },
      ],
    })
    const wrapper = mountApp()
    try {
      await flushPromises()
      chat.activeTab = "bob"
      await flushPromises()
      expect(chat.activeTab).toBe("bob")
      expect(socket.close).not.toHaveBeenCalled()
      expect(useChatStore("graph-7")).toBe(chat)
      useHostsStore().activeHostId = "other-host"
      await flushPromises()
      expect(socket.close).not.toHaveBeenCalled()
      expect(useChatStore("graph-7")).toBe(chat)
    } finally {
      wrapper.unmount()
    }
    expect(socket.close).toHaveBeenCalledTimes(1)
  })
  it("shows the one session it was opened for and tells the shell the page is running", async () => {
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    const wrapper = mountApp()
    const chat = useChatStore("graph-7")
    chat.wsStatus = "open"
    const close = vi.fn()
    chat._ws = { close, onopen: null, onmessage: null, onclose: null, onerror: null }
    await flushPromises()

    const attach = wrapper.find('[data-test="attach"]')
    expect(attach.attributes("data-target")).toBe("graph-7")
    expect(attach.attributes("data-kind")).toBe("attach")
    expect(wrapper.find('[data-test="toasts"]').exists()).toBe(true)
    expect(invoke).toHaveBeenCalledWith("popout_ack", {})
    wrapper.unmount()
    expect(close).toHaveBeenCalledOnce()
    expect(chat._ws).toBeNull()
    expect(chat.wsStatus).toBe("closed")
  })

  it("titles the bar with the session's name and falls back to the app name", async () => {
    const unnamed = mountApp()
    expect(unnamed.get('[data-test="popout-title"]').text()).toBe("KohakuTerrarium")
    unnamed.unmount()

    store.list = [{ id: "i1", graph_id: "graph-7", session_name: "Refactor" }]
    const named = mountApp()
    await flushPromises()
    expect(named.get('[data-test="popout-title"]').text()).toBe("Refactor")
    named.unmount()
  })

  it("hands the session back to the main window from the bar", async () => {
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    const wrapper = mountApp()
    await wrapper.get('[data-test="popout-return"]').trigger("click")
    expect(invoke).toHaveBeenLastCalledWith("popout_return", { args: [true] })
    wrapper.unmount()
  })
})
