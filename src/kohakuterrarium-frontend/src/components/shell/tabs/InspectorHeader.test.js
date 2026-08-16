import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import InspectorHeader from "./InspectorHeader.vue"
import { useChatStore } from "@/stores/chat"
import { useSessionDetailStore } from "@/stores/sessionDetail"
import { useStatusStore } from "@/stores/status"

beforeEach(() => {
  const storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  })
  setActivePinia(createPinia())
})

function mountHeader(instance = null) {
  return mount(InspectorHeader, {
    props: {
      target: "g1",
      sessionName: "g1",
      instance: instance || {
        id: "g1",
        session_id: "g1",
        config_name: "root",
        type: "terrarium",
        status: "running",
      },
    },
    // The macro shell provides the scope; the header's no-arg
    // ``useChatStore()`` / ``useStatusStore()`` resolve through it.
    global: { provide: { "kt:scope": "g1" } },
  })
}

describe("InspectorHeader — token total source (Bug 1)", () => {
  it("shows the backend graph summary total, not the partially-restored chat total", () => {
    // The exact blind spot: after a hard refresh only ONE creature's
    // usage was restored into the chat store (the WS open loads only
    // tabs[0]/active/channels), while the backend graph summary counts
    // every creature + sub-agent. The header must match the summary.
    const chat = useChatStore("g1")
    chat.tokenUsage = { root: { prompt: 100, completion: 50, cached: 0, total: 150 } }

    const detail = useSessionDetailStore("g1")
    detail.summary = { totals: { tokens: { prompt: 300, completion: 150, cached: 0 } } }

    const wrapper = mountHeader()
    const text = wrapper.text()
    // 300 + 150 = 450 (backend graph total), NOT 100 + 50 = 150 (the
    // single restored creature the chat store knows about).
    expect(text).toContain("450 tok")
    expect(text).not.toContain("150 tok")
    wrapper.unmount()
  })

  it("prefers the active tab runtime model over the instance default", () => {
    const chat = useChatStore("g1")
    chat.modelByTab["g1"] = {
      llmName: "openai/gpt-5",
      model: "gpt-5",
    }
    const status = useStatusStore("g1")
    status.sessionInfo.model = "default-model"

    const wrapper = mountHeader({ id: "g1", model: "default-model" })
    expect(wrapper.text()).toContain("openai/gpt-5")
    expect(wrapper.text()).not.toContain("default-model")
    wrapper.unmount()
  })

  it("falls back to the live chat total until the summary poll lands", () => {
    // Before the first session-detail summary poll returns, the header
    // shows the live chat total so it isn't blank on first paint.
    const chat = useChatStore("g1")
    chat.tokenUsage = { root: { prompt: 100, completion: 50, cached: 0, total: 150 } }

    const detail = useSessionDetailStore("g1")
    detail.summary = null

    const wrapper = mountHeader()
    expect(wrapper.text()).toContain("150 tok")
    wrapper.unmount()
  })
})
