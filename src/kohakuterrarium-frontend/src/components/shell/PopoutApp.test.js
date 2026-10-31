import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const store = vi.hoisted(() => ({ current: null, list: [], fetchAll: vi.fn(async () => []) }))
vi.mock("@/stores/instances", () => ({ useInstancesStore: () => store }))

import PopoutApp from "@/components/shell/PopoutApp.vue"

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
  delete window.__TAURI_INTERNALS__
})

describe("PopoutApp", () => {
  it("shows the one session it was opened for and tells the shell the page is running", async () => {
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    const wrapper = mountApp()
    await flushPromises()

    const attach = wrapper.find('[data-test="attach"]')
    expect(attach.attributes("data-target")).toBe("graph-7")
    expect(attach.attributes("data-kind")).toBe("attach")
    expect(wrapper.find('[data-test="toasts"]').exists()).toBe(true)
    expect(invoke).toHaveBeenCalledWith("popout_ack", {})
    wrapper.unmount()
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
