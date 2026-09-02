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

beforeEach(() => {
  const values = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  })
  setActivePinia(createPinia())
})

afterEach(() => {
  for (const wrapper of mountedWrappers) {
    if (wrapper.exists()) wrapper.unmount()
  }
  mountedWrappers.clear()
  vi.unstubAllGlobals()
})

describe("ChatPanel long-session performance", () => {
  function mountPanel(chat, { groupId = null } = {}) {
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    if (!chat.activeTab) chat.activeTab = "kohaku"
    if (!chat.tabs.length) chat.tabs = ["kohaku"]
    chat.commandInventoryByTab = { kohaku: { commands: [], skills: [] } }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    return mountChatPanel({
      props: {
        instance: {
          id: "graph_1",
          graph_id: "graph_1",
          creatures: [{ name: "kohaku", status: "idle" }],
        },
        groupId,
      },
      global: {
        provide: { chatStore: chat },
        stubs: {
          ChatMessage: {
            props: ["message", "prevMessage", "messageIdx", "tabId"],
            template: '<div class="chat-message-stub">{{ message?.id }}</div>',
          },
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })
  }

  function renderedIds(wrapper) {
    return wrapper.findAll(".chat-message-stub").map((el) => el.text())
  }

  function seedMessages(chat, count) {
    chat.messagesByTab = {
      kohaku: Array.from({ length: count }, (_, i) => ({
        id: `m_${i}`,
        role: i % 2 ? "assistant" : "user",
        content: `message ${i}`,
      })),
    }
  }

  it("keeps the live tail reachable when scrolling to an older pending message", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 1000)
    chat.messagesByTab.kohaku[10] = {
      id: "pending_10",
      role: "ui_event",
      content: "approval",
      interactive: true,
      replied: false,
    }
    const wrapper = mountPanel(chat)
    await flushPromises()
    await wrapper.find("textarea").setValue("draft")

    const scrollIntoView = vi.fn()
    const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView")
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    })
    try {
      await wrapper.find(".text-amber.hover\\:underline").trigger("click")
      await flushPromises()

      expect(renderedIds(wrapper)[0]).toBe("pending_10")
      expect(renderedIds(wrapper).at(-1)).toBe("m_999")
      expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "center" })
    } finally {
      if (descriptor) Object.defineProperty(Element.prototype, "scrollIntoView", descriptor)
      else delete Element.prototype.scrollIntoView
      wrapper.unmount()
    }
  })

  it("does not let a queued forced scroll override an older pending target", async () => {
    const frames = new Map()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))

    const chat = useChatStore("graph_1")
    seedMessages(chat, 1000)
    chat.messagesByTab.kohaku[10] = {
      id: "pending_10",
      role: "ui_event",
      content: "approval",
      interactive: true,
      replied: false,
    }
    const wrapper = mountPanel(chat)
    await flushPromises()
    await wrapper.find("textarea").setValue("draft")
    frames.clear()

    chat.messagesByTab.kohaku.push({ id: "m_1000", role: "assistant", content: "queued scroll" })
    await flushPromises()
    expect(frames.size).toBe(1)

    const scrollIntoView = vi.fn()
    const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView")
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    })
    try {
      await wrapper.find(".text-amber.hover\\:underline").trigger("click")
      await flushPromises()

      for (const [id, frame] of [...frames]) {
        frames.delete(id)
        frame()
      }
      await flushPromises()

      expect(renderedIds(wrapper)[0]).toBe("pending_10")
      expect(scrollIntoView).toHaveBeenCalledOnce()
    } finally {
      if (descriptor) Object.defineProperty(Element.prototype, "scrollIntoView", descriptor)
      else delete Element.prototype.scrollIntoView
      wrapper.unmount()
    }
  })

  it("does not let a queued scroll override a pending target already in the window", async () => {
    const frames = new Map()
    const canceledFrames = new Set()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => canceledFrames.add(id))

    const chat = useChatStore("graph_1")
    seedMessages(chat, 1000)
    chat.messagesByTab.kohaku[900] = {
      id: "pending_900",
      role: "ui_event",
      content: "approval",
      interactive: true,
      replied: false,
    }
    const wrapper = mountPanel(chat)
    await flushPromises()
    await wrapper.find("textarea").setValue("draft")
    frames.clear()

    const viewport = wrapper.find(".chat-messages-viewport").element
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 1000 })
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 200 })
    viewport.scrollTop = 800
    chat.messagesByTab.kohaku.push({ id: "m_1000", role: "assistant", content: "queued scroll" })
    await flushPromises()
    expect(frames.size).toBe(1)

    const scrollIntoView = vi.fn(() => {
      viewport.scrollTop = 300
    })
    const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView")
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    })
    try {
      await wrapper.find(".text-amber.hover\\:underline").trigger("click")
      await flushPromises()

      expect(canceledFrames).toEqual(new Set([1]))
      frames.get(1)()
      await flushPromises()

      expect(renderedIds(wrapper)[0]).toBe("m_801")
      expect(renderedIds(wrapper)).toContain("pending_900")
      expect(scrollIntoView).toHaveBeenCalledOnce()
      expect(viewport.scrollTop).toBe(300)

      frames.clear()
      chat.messagesByTab.kohaku.push({ id: "m_1001", role: "assistant", content: "stay put" })
      await flushPromises()
      expect(frames.size).toBe(0)
    } finally {
      if (descriptor) Object.defineProperty(Element.prototype, "scrollIntoView", descriptor)
      else delete Element.prototype.scrollIntoView
      wrapper.unmount()
    }
  })

  it("does not scan branch history before sending a regular message", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 250)
    const sendFrame = vi.fn()
    chat._ws = { readyState: WebSocket.OPEN, send: sendFrame }
    const capture = vi.spyOn(chat, "captureCommandResultContext")
    const wrapper = mountPanel(chat)
    await flushPromises()

    await wrapper.find("textarea").setValue("continue")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(capture).not.toHaveBeenCalled()
    expect(sendFrame).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it("coalesces native scroll state reads into one animation frame", async () => {
    const frames = new Map()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))

    const chat = useChatStore("graph_1")
    seedMessages(chat, 20)
    const wrapper = mountPanel(chat)
    await flushPromises()
    frames.clear()

    const viewport = wrapper.find(".chat-messages-viewport").element
    let heightReads = 0
    Object.defineProperty(viewport, "scrollHeight", {
      configurable: true,
      get() {
        heightReads += 1
        return 1000
      },
    })
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 200 })
    viewport.scrollTop = 300

    viewport.dispatchEvent(new Event("scroll"))
    viewport.dispatchEvent(new Event("scroll"))
    viewport.dispatchEvent(new Event("scroll"))

    expect(heightReads).toBe(0)
    expect(frames.size).toBe(1)
    const [[id, frame]] = frames
    frames.delete(id)
    frame()
    expect(heightReads).toBe(1)
    wrapper.unmount()
  })

  it("does not let a pending auto-scroll override a later manual scroll", async () => {
    const frames = new Map()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))

    const chat = useChatStore("graph_1")
    seedMessages(chat, 20)
    const wrapper = mountPanel(chat)
    await flushPromises()
    frames.clear()

    const viewport = wrapper.find(".chat-messages-viewport").element
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 1000 })
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 200 })
    viewport.scrollTop = 1000
    viewport.dispatchEvent(new Event("scroll"))
    frames.clear()

    chat.messagesByTab.kohaku.push({ id: "m_20", role: "assistant", content: "stream" })
    await flushPromises()
    viewport.scrollTop = 300
    viewport.dispatchEvent(new Event("scroll"))

    expect(frames.size).toBe(0)
    expect(viewport.scrollTop).toBe(300)
    wrapper.unmount()
  })

  it("resumes follow mode after manually returning to the bottom", async () => {
    const frames = new Map()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))

    const chat = useChatStore("graph_1")
    seedMessages(chat, 20)
    const wrapper = mountPanel(chat)
    await flushPromises()
    frames.clear()

    const viewport = wrapper.find(".chat-messages-viewport").element
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 1000 })
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 200 })
    viewport.scrollTop = 1000
    viewport.dispatchEvent(new Event("scroll"))
    const [[bottomId, bottomFrame]] = frames
    frames.delete(bottomId)
    bottomFrame()

    viewport.scrollTop = 300
    viewport.dispatchEvent(new Event("scroll"))
    expect(frames.size).toBe(1)
    const [[upId, upFrame]] = frames
    frames.delete(upId)
    upFrame()

    viewport.scrollTop = 800
    viewport.dispatchEvent(new Event("scroll"))
    const [[stateId, stateFrame]] = frames
    frames.delete(stateId)
    stateFrame()
    expect(frames.size).toBe(0)

    chat.messagesByTab.kohaku.push({ id: "m_20", role: "assistant", content: "stream" })
    await flushPromises()
    expect(frames.size).toBe(1)
    wrapper.unmount()
  })

  it("cancels a pending native scroll read when the tab changes", async () => {
    const frames = new Map()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))

    const chat = useChatStore("graph_1")
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku", "reviewer"]
    chat.messagesByTab = { kohaku: [], reviewer: [] }
    const groupId = chat.enableGroups()
    const wrapper = mountPanel(chat, { groupId })
    await flushPromises()
    frames.clear()

    wrapper.find(".chat-messages-viewport").element.dispatchEvent(new Event("scroll"))
    expect(frames.size).toBe(1)

    chat.setGroupActiveTab(groupId, "reviewer")
    await flushPromises()

    expect(frames.size).toBe(0)
    wrapper.unmount()
  })

  it("does not let a pending frame from the previous tab overwrite the new tab position", async () => {
    const frames = new Map()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))

    const chat = useChatStore("graph_1")
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku", "reviewer"]
    chat.messagesByTab = { kohaku: [], reviewer: [] }
    const groupId = chat.enableGroups()
    const wrapper = mountPanel(chat, { groupId })
    await flushPromises()

    chat.messagesByTab.kohaku.push({ id: "m_1", role: "user", content: "force scroll" })
    await flushPromises()
    const pendingFrame = [...frames.values()][0]
    expect(pendingFrame).toBeTypeOf("function")

    chat.setGroupActiveTab(groupId, "reviewer")
    await flushPromises()
    const viewport = wrapper.find(".chat-messages-viewport").element
    viewport.scrollTop = 73

    pendingFrame()
    expect(viewport.scrollTop).toBe(73)
    expect(frames.size).toBe(0)
    wrapper.unmount()
  })

  function stubIdleExpansion() {
    const idleCallbacks = []
    const idleCancelled = new Set()
    vi.stubGlobal("requestIdleCallback", (callback) => {
      idleCallbacks.push(callback)
      return idleCallbacks.length
    })
    vi.stubGlobal("cancelIdleCallback", (id) => idleCancelled.add(id))
    return { idleCallbacks, idleCancelled }
  }

  it("auto-expands the history window when scrolling reaches the top", async () => {
    const frames = new Map()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))
    const { idleCallbacks } = stubIdleExpansion()

    const chat = useChatStore("graph_1")
    seedMessages(chat, 1000)
    const wrapper = mountPanel(chat)
    await flushPromises()
    frames.clear()

    const viewport = wrapper.find(".chat-messages-viewport").element
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 1000 })
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 200 })

    viewport.scrollTop = 800
    viewport.dispatchEvent(new Event("scroll"))
    let [id, frame] = [...frames][0]
    frames.delete(id)
    frame()

    // Upward but away from the top: history mode pins the window,
    // no automatic expansion yet.
    viewport.scrollTop = 300
    viewport.dispatchEvent(new Event("scroll"))
    ;[id, frame] = [...frames][0]
    frames.delete(id)
    frame()
    expect(renderedIds(wrapper)).toHaveLength(200)

    // Reaching the top expands one small step, then the idle lookahead
    // pre-mounts the next step exactly once.
    viewport.scrollTop = 10
    viewport.dispatchEvent(new Event("scroll"))
    ;[id, frame] = [...frames][0]
    frames.delete(id)
    frame()
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(300)
    expect(renderedIds(wrapper)[0]).toBe("m_700")
    expect(viewport.scrollTop).toBe(10)
    expect(idleCallbacks).toHaveLength(1)

    idleCallbacks[0]()
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(400)
    expect(renderedIds(wrapper)[0]).toBe("m_600")
    expect(idleCallbacks).toHaveLength(1)
    wrapper.unmount()
  })

  it("cancels the pending idle expansion when the active tab changes", async () => {
    const frames = new Map()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))
    const { idleCallbacks, idleCancelled } = stubIdleExpansion()

    const chat = useChatStore("graph_1")
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku", "reviewer"]
    seedMessages(chat, 1000)
    chat.messagesByTab.reviewer = Array.from({ length: 20 }, (_, index) => ({
      id: `r_${index}`,
      role: index % 2 ? "assistant" : "user",
      content: `review ${index}`,
    }))
    chat.commandInventoryByTab.reviewer = { commands: [], skills: [] }
    chat._commandInventoryFetchedAtByTab.reviewer = Date.now()
    const groupId = chat.enableGroups()
    const wrapper = mountPanel(chat, { groupId })
    await flushPromises()
    frames.clear()

    const viewport = wrapper.find(".chat-messages-viewport").element
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 1000 })
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 200 })

    viewport.scrollTop = 800
    viewport.dispatchEvent(new Event("scroll"))
    let [id, frame] = [...frames][0]
    frames.delete(id)
    frame()

    viewport.scrollTop = 300
    viewport.dispatchEvent(new Event("scroll"))
    ;[id, frame] = [...frames][0]
    frames.delete(id)
    frame()

    viewport.scrollTop = 10
    viewport.dispatchEvent(new Event("scroll"))
    ;[id, frame] = [...frames][0]
    frames.delete(id)
    frame()
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(300)
    expect(idleCallbacks).toHaveLength(1)

    chat.setGroupActiveTab(groupId, "reviewer")
    await flushPromises()
    expect(idleCancelled.size).toBe(1)

    idleCallbacks[0]()
    await flushPromises()
    expect(renderedIds(wrapper)).toHaveLength(20)
    wrapper.unmount()
  })
})
