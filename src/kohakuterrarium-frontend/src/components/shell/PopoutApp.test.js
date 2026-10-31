import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/stores/instances", () => ({
  useInstancesStore: () => ({ fetchAll: vi.fn(async () => []) }),
}))

import PopoutApp from "@/components/shell/PopoutApp.vue"

const AuthGate = { template: "<div><slot /></div>" }
const AttachTab = {
  props: ["tab"],
  template: '<div data-test="attach" :data-target="tab.target" :data-kind="tab.kind" />',
}
const ToastCenter = { template: '<div data-test="toasts" />' }

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => {
  delete window.__TAURI_INTERNALS__
})

describe("PopoutApp", () => {
  it("shows the one session it was opened for and tells the shell the page is running", async () => {
    const invoke = vi.fn(async () => true)
    window.__TAURI_INTERNALS__ = { invoke }
    const wrapper = mount(PopoutApp, {
      props: { scope: "graph-7" },
      global: { stubs: { AuthGate, AttachTab, ToastCenter } },
    })
    await flushPromises()

    const attach = wrapper.find('[data-test="attach"]')
    expect(attach.attributes("data-target")).toBe("graph-7")
    expect(attach.attributes("data-kind")).toBe("attach")
    expect(wrapper.find('[data-test="toasts"]').exists()).toBe(true)
    expect(invoke).toHaveBeenCalledWith("popout_ack", {})
    wrapper.unmount()
  })
})
