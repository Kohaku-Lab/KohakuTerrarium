// The shared production ToolCallBlock's truncation detail: a tool whose result
// the backend truncated must surface the 'Output truncated' strip with the
// omitted byte count, so the detail is reachable from the real tool result the
// backend returns (result_meta.truncated / result_meta.omitted_text_bytes).
import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import ToolCallBlock from "./ToolCallBlock.vue"
import { useChatStore } from "@/stores/chat"

beforeEach(() => {
  const values = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  })
  setActivePinia(createPinia())
})

describe("ToolCallBlock — truncated output detail", () => {
  it("shows the truncation strip with the omitted byte count for a truncated result", () => {
    const chat = useChatStore()
    chat._instanceGraphId = "g1"
    chat.activeTab = "root"
    const wrapper = mount(ToolCallBlock, {
      props: {
        expanded: true,
        tc: {
          type: "tool",
          id: "t-truncated",
          name: "bash",
          kind: "tool",
          args: {},
          status: "done",
          result: "head of the output",
          resultMeta: { truncated: true, omitted_text_bytes: 2048 },
        },
      },
      global: { stubs: { MarkdownRenderer: true } },
    })

    expect(wrapper.text()).toContain("Output truncated")
    expect(wrapper.text()).toContain((2048).toLocaleString())
    expect(wrapper.text()).toContain("bytes omitted")
  })

  it("omits the truncation strip when the result was not truncated", () => {
    const chat = useChatStore()
    chat._instanceGraphId = "g1"
    chat.activeTab = "root"
    const wrapper = mount(ToolCallBlock, {
      props: {
        expanded: true,
        tc: {
          type: "tool",
          id: "t-full",
          name: "bash",
          kind: "tool",
          args: {},
          status: "done",
          result: "complete",
        },
      },
      global: { stubs: { MarkdownRenderer: true } },
    })

    expect(wrapper.text()).not.toContain("Output truncated")
  })
})
