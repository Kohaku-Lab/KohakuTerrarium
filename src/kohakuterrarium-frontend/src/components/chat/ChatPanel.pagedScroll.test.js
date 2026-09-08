import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import ChatPanel from "./ChatPanel.vue"
import { useChatStore } from "@/stores/chat"

const mountedWrappers = new Set()

function mountChatPanel(options) {
  const wrapper = mount(ChatPanel, options)
  mountedWrappers.add(wrapper)
  return wrapper
}

let frames = new Map()
let nextFrameId = 1

beforeEach(() => {
  const values = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  })
  nextFrameId = 1
  frames = new Map()
  vi.stubGlobal("requestAnimationFrame", (callback) => {
    const id = nextFrameId++
    frames.set(id, callback)
    return id
  })
  vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))
  setActivePinia(createPinia())
})

afterEach(() => {
  for (const wrapper of mountedWrappers) {
    if (wrapper.exists()) wrapper.unmount()
  }
  mountedWrappers.clear()
  vi.unstubAllGlobals()
})

function runScrollFrames() {
  const pending = [...frames.values()]
  frames.clear()
  for (const frame of pending) frame()
}

// Live tab driven by server-side history paging: the store holds one
// bounded page (historyPagingByTab.hasMore) and older pages arrive via
// ``loadOlderLiveHistory``. The pre-paging scroll contract must survive:
// scrolling up auto-loads the next page, scrolling back to the bottom
// narrows the render window back to the live tail.
describe("ChatPanel paged live history scrolling", () => {
  const PAGE = 120

  function seedFirstPage(chat) {
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    if (!chat.activeTab) chat.activeTab = "kohaku"
    if (!chat.tabs.length) chat.tabs = ["kohaku"]
    chat.commandInventoryByTab = { kohaku: { commands: [], skills: [] } }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    chat.messagesByTab = {
      kohaku: Array.from({ length: PAGE }, (_, i) => ({
        id: `m_${i}`,
        role: i % 2 ? "assistant" : "user",
        content: `message ${i}`,
      })),
    }
    chat.historyPagingByTab = {
      kohaku: { hasMore: true, oldestEventId: PAGE, total: 1000 },
    }
  }

  function mountPanel(chat) {
    return mountChatPanel({
      props: {
        instance: {
          id: "graph_1",
          graph_id: "graph_1",
          creatures: [{ name: "kohaku", status: "idle" }],
        },
      },
      global: {
        provide: { chatStore: chat },
        stubs: {
          ChatMessage: {
            props: ["message"],
            template: '<div class="chat-message-stub">{{ message?.id }}</div>',
          },
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })
  }

  function spyLoadOlder(chat, { pages = Infinity } = {}) {
    const calls = []
    chat.loadOlderLiveHistory = async (tab) => {
      calls.push(tab)
      const current = chat.messagesByTab[tab]
      const firstSeq = Number(current[0].id.replace("m_", "")) || 0
      const prepended = Array.from({ length: PAGE }, (_, i) => ({
        id: `m_${firstSeq - PAGE + i}`,
        role: "user",
        content: `message ${firstSeq - PAGE + i}`,
      }))
      chat.messagesByTab[tab] = [...prepended, ...current]
      chat.historyPagingByTab[tab] = {
        ...chat.historyPagingByTab[tab],
        hasMore: calls.length < pages,
        oldestEventId: (chat.historyPagingByTab[tab].oldestEventId ?? 0) - PAGE,
      }
      return calls.length < pages
    }
    return calls
  }

  function renderedIds(wrapper) {
    return wrapper.findAll(".chat-message-stub").map((el) => el.text())
  }

  function stubGeometry(wrapper, { clientHeight = 200 } = {}) {
    const viewport = wrapper.find(".chat-messages-viewport").element
    // Track the rendered row count so scrollHeight moves like a real
    // scrollbar would: narrowing the window must visibly shrink it.
    Object.defineProperty(viewport, "scrollHeight", {
      configurable: true,
      get() {
        return wrapper.findAll(".chat-message-stub").length * 30
      },
    })
    Object.defineProperty(viewport, "clientHeight", {
      configurable: true,
      value: clientHeight,
    })
    return viewport
  }

  function scrollViewport(viewport, scrollTop) {
    viewport.scrollTop = scrollTop
    viewport.dispatchEvent(new Event("scroll"))
    runScrollFrames()
  }

  it("auto-fetches the next page when scrolled to the top of the loaded log", async () => {
    const chat = useChatStore("graph_1")
    seedFirstPage(chat)
    const calls = spyLoadOlder(chat)
    const wrapper = mountPanel(chat)
    await flushPromises()
    const viewport = stubGeometry(wrapper)

    scrollViewport(viewport, 9800)
    scrollViewport(viewport, 0)
    await flushPromises()

    expect(calls.length).toBe(1)
    // The freshly prepended page must be RENDERED, not just in the store.
    expect(renderedIds(wrapper)[0]).toBe("m_-120")
    expect(renderedIds(wrapper).length).toBe(2 * PAGE)
    wrapper.unmount()
  })

  it("keeps auto-fetching while the reader keeps scrolling up", async () => {
    const chat = useChatStore("graph_1")
    seedFirstPage(chat)
    const calls = spyLoadOlder(chat)
    const wrapper = mountPanel(chat)
    await flushPromises()
    const viewport = stubGeometry(wrapper)

    scrollViewport(viewport, 9800)
    scrollViewport(viewport, 0)
    await flushPromises()
    scrollViewport(viewport, 0)
    await flushPromises()

    // The reader is still pinned at the top of the log; the next older
    // page must load automatically instead of requiring the button.
    expect(calls.length).toBe(2)
    expect(renderedIds(wrapper).length).toBe(3 * PAGE)
    wrapper.unmount()
  })

  it("narrows the render window back to the tail when scrolling to the bottom", async () => {
    const chat = useChatStore("graph_1")
    seedFirstPage(chat)
    const calls = spyLoadOlder(chat, { pages: 2 })
    const wrapper = mountPanel(chat)
    await flushPromises()
    const viewport = stubGeometry(wrapper)

    scrollViewport(viewport, 9800)
    scrollViewport(viewport, 0)
    await flushPromises()
    scrollViewport(viewport, 0)
    await flushPromises()
    const expandedCount = renderedIds(wrapper).length
    const expandedHeight = viewport.scrollHeight
    expect(expandedCount).toBe(3 * PAGE)

    // Scroll back to the bottom: history mode must close and the render
    // window must narrow back to the live tail. The scrollbar-visible
    // signal is the scrollHeight drop.
    scrollViewport(viewport, 40000)
    await flushPromises()

    const narrowedCount = renderedIds(wrapper).length
    expect(narrowedCount).toBeLessThan(expandedCount)
    expect(narrowedCount).toBeLessThanOrEqual(200)
    expect(viewport.scrollHeight).toBeLessThan(expandedHeight * 0.75)
    wrapper.unmount()
  })
})
