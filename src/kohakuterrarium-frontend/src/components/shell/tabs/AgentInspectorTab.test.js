import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { nextTick } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import AgentInspectorTab from "./AgentInspectorTab.vue"
import { useChatStore } from "@/stores/chat"
import { useInstancesStore } from "@/stores/instances"
import { useSessionDetailStore } from "@/stores/sessionDetail"

beforeEach(() => {
  const storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  })
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.useRealTimers()
})

async function mountInspector() {
  const instances = useInstancesStore()
  instances.list = [{ id: "g1", session_id: "g1", creatures: [] }]
  const chat = useChatStore("g1")
  chat.initForInstance = vi.fn() // don't open a real WS in the test
  chat.messagesByTab = { root: [] }
  const detail = useSessionDetailStore("g1")
  detail.load = vi.fn().mockResolvedValue() // requestReload calls this
  const reloadSpy = vi.spyOn(detail, "requestReload")

  const wrapper = mount(AgentInspectorTab, {
    props: { tab: { target: "g1" } },
    global: { stubs: { SessionViewerPage: true, InspectorHeader: true } },
  })
  await flushPromises()
  return { wrapper, chat, reloadSpy }
}

describe("AgentInspectorTab — live viewer auto-refresh (UXI-01)", () => {
  it("debounces a burst of round activity into one viewer reload", async () => {
    vi.useFakeTimers()
    const { wrapper, chat, reloadSpy } = await mountInspector()

    // Two rapid round signals (new top-level messages) within the window.
    chat.messagesByTab.root.push({ id: "m1" })
    await nextTick()
    chat.messagesByTab.root.push({ id: "m2" })
    await nextTick()
    expect(reloadSpy).not.toHaveBeenCalled() // still debouncing

    vi.advanceTimersByTime(1200)
    expect(reloadSpy).toHaveBeenCalledTimes(1)

    wrapper.unmount()
  })

  it("skips the reload while the browser tab is hidden", async () => {
    vi.useFakeTimers()
    const { wrapper, chat, reloadSpy } = await mountInspector()
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" })

    chat.messagesByTab.root.push({ id: "m1" })
    await nextTick()
    vi.advanceTimersByTime(1200)
    expect(reloadSpy).not.toHaveBeenCalled()

    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" })
    wrapper.unmount()
  })
})
