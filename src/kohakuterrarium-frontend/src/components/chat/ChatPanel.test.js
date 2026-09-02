import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { ElMessageBox } from "element-plus"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import ChatPanel from "./ChatPanel.vue"
import { createChatScrollScheduler } from "./chatScrollScheduler"
import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

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
  vi.unstubAllGlobals()
})

describe("ChatPanel scroll scheduling", () => {
  function setupScheduler({ nearBottom = true } = {}) {
    const frames = new Map()
    let nextFrame = 1
    const scroll = vi.fn()
    const scheduler = createChatScrollScheduler({
      afterDomCommit: (callback) => callback(),
      requestFrame: (callback) => {
        const id = nextFrame++
        frames.set(id, callback)
        return id
      },
      cancelFrame: (id) => frames.delete(id),
      shouldScroll: () => nearBottom,
      scroll,
    })
    return {
      frames,
      scroll,
      scheduler,
      runFrame() {
        const [[id, callback]] = frames
        frames.delete(id)
        callback()
      },
    }
  }

  it("coalesces repeated requests into one scroll per frame", () => {
    const { frames, scroll, scheduler, runFrame } = setupScheduler()

    scheduler.schedule()
    scheduler.schedule()
    scheduler.schedule()

    expect(frames.size).toBe(1)
    runFrame()
    expect(scroll).toHaveBeenCalledOnce()
  })

  it("upgrades a pending normal request when a force request arrives", () => {
    const { scroll, scheduler, runFrame } = setupScheduler({ nearBottom: false })

    scheduler.schedule()
    scheduler.schedule(true)
    runFrame()

    expect(scroll).toHaveBeenCalledOnce()
  })

  it("does not scroll for a normal request after leaving the bottom", () => {
    const { scroll, scheduler, runFrame } = setupScheduler({ nearBottom: false })

    scheduler.schedule()
    runFrame()

    expect(scroll).not.toHaveBeenCalled()
  })

  it("cancels the pending frame when disposed", () => {
    const { frames, scroll, scheduler } = setupScheduler()

    scheduler.schedule()
    scheduler.dispose()

    expect(frames.size).toBe(0)
    expect(scroll).not.toHaveBeenCalled()
  })

  it("does not run a forced frame after its scope is invalidated", () => {
    const { frames, scroll, scheduler } = setupScheduler({ nearBottom: false })

    scheduler.schedule(true, "instance:A")
    scheduler.invalidate()

    expect(frames.size).toBe(0)
    expect(scroll).not.toHaveBeenCalled()
  })

  it("does not create a frame from an invalidated DOM commit", () => {
    const commits = []
    const frames = new Map()
    const scheduler = createChatScrollScheduler({
      afterDomCommit: (callback) => commits.push(callback),
      requestFrame: (callback) => {
        frames.set(1, callback)
        return 1
      },
      cancelFrame: (id) => frames.delete(id),
      shouldScroll: () => true,
      scroll: vi.fn(),
    })

    scheduler.schedule(true, "instance:A")
    scheduler.invalidate()
    commits[0]()

    expect(frames.size).toBe(0)
  })

  it("does not merge force state across scopes", () => {
    const { scroll, scheduler, runFrame } = setupScheduler({ nearBottom: false })

    scheduler.schedule(true, "instance:A")
    scheduler.schedule(false, "instance:B")
    runFrame()

    expect(scroll).not.toHaveBeenCalled()
  })

  it("suppresses a pending forced scroll until follow mode resumes", () => {
    const { frames, scroll, scheduler } = setupScheduler({ nearBottom: true })

    scheduler.schedule(true, "instance:A")
    scheduler.suppress()

    expect(frames.size).toBe(0)
    expect(scroll).not.toHaveBeenCalled()

    scheduler.resume()
    scheduler.schedule(false, "instance:A")
    expect(frames.size).toBe(1)
  })
})

describe("ChatPanel long-session performance", () => {
  function mountPanel(chat, { groupId = null } = {}) {
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    if (!chat.activeTab) chat.activeTab = "kohaku"
    if (!chat.tabs.length) chat.tabs = ["kohaku"]
    chat.commandInventoryByTab = { kohaku: { commands: [], skills: [] } }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    return mount(ChatPanel, {
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

  it("preserves the message flex layout inside per-message anchors", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 2)
    const wrapper = mountPanel(chat)
    await flushPromises()

    const anchors = wrapper.findAll("[data-message-id]")
    expect(anchors).toHaveLength(2)
    expect(
      anchors.every(
        (anchor) => anchor.classes().includes("flex") && anchor.classes().includes("flex-col"),
      ),
    ).toBe(true)
  })

  it("caps simple transcripts by top-level message count", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    const wrapper = mountPanel(chat)
    await flushPromises()

    expect(renderedIds(wrapper).length).toBe(200)
    expect(renderedIds(wrapper)[0]).toBe("m_250")
    expect(renderedIds(wrapper).at(-1)).toBe("m_449")
    const earlier = wrapper.find("button.self-center")
    expect(earlier.exists()).toBe(true)
    expect(earlier.text()).toContain("250")
  })

  it("uses child parts to reduce the live-tail message count", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    for (const message of chat.messagesByTab.kohaku) {
      if (message.role !== "assistant") continue
      message.parts = Array.from({ length: 99 }, (_, index) => ({
        id: `${message.id}_part_${index}`,
        type: "text",
        content: `part ${index}`,
      }))
    }
    const wrapper = mountPanel(chat)
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(18)
    expect(renderedIds(wrapper)[0]).toBe("m_432")
    expect(renderedIds(wrapper).at(-1)).toBe("m_449")
  })

  it("keeps an oversized final message whole", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    chat.messagesByTab.kohaku[449].parts = Array.from({ length: 1500 }, (_, index) => ({
      id: `large_part_${index}`,
      type: "text",
      content: `part ${index}`,
    }))
    const wrapper = mountPanel(chat)
    await flushPromises()

    expect(renderedIds(wrapper)).toEqual(["m_448", "m_449"])
  })

  it("counts content parts and legacy tool calls in the render budget", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    for (const message of chat.messagesByTab.kohaku) {
      if (message.role === "assistant") {
        message.tool_calls = Array.from({ length: 99 }, (_, index) => ({
          id: `${message.id}_tool_${index}`,
        }))
      } else {
        message.contentParts = Array.from({ length: 99 }, (_, index) => ({
          type: "text",
          text: `part ${index}`,
        }))
      }
    }
    const wrapper = mountPanel(chat)
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(10)
    expect(renderedIds(wrapper)[0]).toBe("m_440")
  })

  it("does not double-count legacy assistant fields when parts are rendered", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    for (const message of chat.messagesByTab.kohaku) {
      if (message.role !== "assistant") continue
      message.parts = Array.from({ length: 49 }, (_, index) => ({
        id: `${message.id}_part_${index}`,
        type: "text",
        content: `part ${index}`,
      }))
      message.tool_calls = Array.from({ length: 99 }, (_, index) => ({
        id: `${message.id}_legacy_${index}`,
      }))
    }
    const wrapper = mountPanel(chat)
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(38)
    expect(renderedIds(wrapper)[0]).toBe("m_412")
  })

  it("counts direct tool children without recursively scanning their payloads", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    for (const message of chat.messagesByTab.kohaku) {
      if (message.role !== "assistant") continue
      message.parts = [
        {
          id: `${message.id}_tool`,
          type: "tool",
          children: Array.from({ length: 98 }, (_, index) => ({
            id: `${message.id}_child_${index}`,
          })),
        },
      ]
    }
    const wrapper = mountPanel(chat)
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(18)
    expect(renderedIds(wrapper)[0]).toBe("m_432")
  })

  it("counts direct tool result parts that render media outside the collapsed details", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    for (const message of chat.messagesByTab.kohaku) {
      if (message.role !== "assistant") continue
      message.parts = [
        {
          id: `${message.id}_tool`,
          type: "tool",
          resultParts: Array.from({ length: 98 }, (_, index) => ({
            type: "image_url",
            image_url: { url: `data:image/png;base64,${index}` },
          })),
        },
      ]
    }
    const wrapper = mountPanel(chat)
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(18)
    expect(renderedIds(wrapper)[0]).toBe("m_432")
    wrapper.unmount()
  })

  it("load-earlier expands the window toward the start", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    const wrapper = mountPanel(chat)
    await flushPromises()

    await wrapper.find("button.self-center").trigger("click")
    await flushPromises()

    expect(renderedIds(wrapper).length).toBe(400)
    expect(renderedIds(wrapper)[0]).toBe("m_50")
    expect(wrapper.find("button.self-center").text()).toContain("50")
  })

  it("shrinkage below an expanded window start falls back to the tail window", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    const wrapper = mountPanel(chat)
    await flushPromises()

    // Expand once: explicit window start at index 50.
    await wrapper.find("button.self-center").trigger("click")
    await flushPromises()
    expect(renderedIds(wrapper).length).toBe(400)

    // A resync replaces the transcript with a much shorter one.
    seedMessages(chat, 30)
    await flushPromises()

    // Without the out-of-range fallback the view would collapse to a
    // single message (clamp to total - 1).
    expect(renderedIds(wrapper).length).toBe(30)
    expect(renderedIds(wrapper)[0]).toBe("m_0")
    expect(wrapper.find("button.self-center").exists()).toBe(false)

    seedMessages(chat, 500)
    await flushPromises()

    expect(renderedIds(wrapper).length).toBe(200)
    expect(renderedIds(wrapper)[0]).toBe("m_300")
    expect(renderedIds(wrapper).at(-1)).toBe("m_499")
  })

  it("keeps the active history anchor on the same message after earlier messages are removed", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    const wrapper = mountPanel(chat)
    await flushPromises()

    await wrapper.find("button.self-center").trigger("click")
    await flushPromises()
    expect(renderedIds(wrapper)[0]).toBe("m_50")

    chat.messagesByTab.kohaku.splice(0, 10)
    await flushPromises()

    expect(renderedIds(wrapper)[0]).toBe("m_50")
    wrapper.unmount()
  })

  it("falls back to the bounded live tail when the active anchor disappears", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    const wrapper = mountPanel(chat)
    await flushPromises()

    await wrapper.find("button.self-center").trigger("click")
    await flushPromises()
    expect(renderedIds(wrapper)[0]).toBe("m_50")

    chat.messagesByTab.kohaku = Array.from({ length: 500 }, (_, index) => ({
      id: `replacement_${index}`,
      role: index % 2 ? "assistant" : "user",
      content: `replacement ${index}`,
    }))
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(200)
    expect(renderedIds(wrapper)[0]).toBe("replacement_300")
    expect(renderedIds(wrapper).at(-1)).toBe("replacement_499")
    wrapper.unmount()
  })

  it("new tail messages stay mounted inside the window while streaming", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 420)
    const wrapper = mountPanel(chat)
    await flushPromises()

    chat.messagesByTab.kohaku.push({ id: "m_420", role: "user", content: "live" })
    await flushPromises()

    expect(renderedIds(wrapper).length).toBe(200)
    expect(renderedIds(wrapper).at(-1)).toBe("m_420")
    expect(renderedIds(wrapper)).not.toContain("m_0")
  })

  it("keeps the history start fixed while new tail messages remain reachable", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    const wrapper = mountPanel(chat)
    await flushPromises()

    await wrapper.find("button.self-center").trigger("click")
    await flushPromises()
    expect(renderedIds(wrapper).length).toBe(400)

    for (let index = 450; index < 500; index += 1) {
      chat.messagesByTab.kohaku.push({
        id: `m_${index}`,
        role: index % 2 ? "assistant" : "user",
        content: `message ${index}`,
      })
    }
    await flushPromises()

    expect(renderedIds(wrapper).length).toBe(450)
    expect(renderedIds(wrapper)[0]).toBe("m_50")
    expect(renderedIds(wrapper).at(-1)).toBe("m_499")
  })

  it("pins the live-tail start when the user scrolls upward", async () => {
    const frames = new Map()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))

    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    for (const message of chat.messagesByTab.kohaku) {
      if (message.role !== "assistant") continue
      message.parts = Array.from({ length: 99 }, (_, index) => ({
        id: `${message.id}_part_${index}`,
        type: "text",
        content: `part ${index}`,
      }))
    }
    const wrapper = mountPanel(chat)
    await flushPromises()
    frames.clear()

    const viewport = wrapper.find(".chat-messages-viewport").element
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 1000 })
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 200 })
    viewport.scrollTop = 800
    viewport.dispatchEvent(new Event("scroll"))
    let [[frameId, frame]] = frames
    frames.delete(frameId)
    frame()

    viewport.scrollTop = 300
    viewport.dispatchEvent(new Event("scroll"))
    ;[[frameId, frame]] = frames
    frames.delete(frameId)
    frame()

    chat.messagesByTab.kohaku.push({ id: "m_450", role: "user", content: "new user" })
    chat.messagesByTab.kohaku.push({
      id: "m_451",
      role: "assistant",
      parts: Array.from({ length: 99 }, (_, index) => ({
        id: `m_451_part_${index}`,
        type: "text",
        content: `part ${index}`,
      })),
    })
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(20)
    expect(renderedIds(wrapper)[0]).toBe("m_432")
    expect(renderedIds(wrapper).at(-1)).toBe("m_451")
    wrapper.unmount()
  })

  it("returns an expanded history view to the bounded live tail at the bottom", async () => {
    const frames = new Map()
    let nextFrame = 1
    vi.stubGlobal("requestAnimationFrame", (callback) => {
      const id = nextFrame++
      frames.set(id, callback)
      return id
    })
    vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))

    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    const wrapper = mountPanel(chat)
    await flushPromises()
    frames.clear()

    await wrapper.find("button.self-center").trigger("click")
    await flushPromises()
    expect(renderedIds(wrapper)).toHaveLength(400)

    const viewport = wrapper.find(".chat-messages-viewport").element
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 1000 })
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 200 })
    viewport.scrollTop = 300
    viewport.dispatchEvent(new Event("scroll"))
    let [[frameId, frame]] = frames
    frames.delete(frameId)
    frame()

    viewport.scrollTop = 800
    viewport.dispatchEvent(new Event("scroll"))
    ;[[frameId, frame]] = frames
    frames.delete(frameId)
    frame()
    await flushPromises()

    expect(renderedIds(wrapper)).toHaveLength(200)
    expect(renderedIds(wrapper)[0]).toBe("m_250")
    expect(renderedIds(wrapper).at(-1)).toBe("m_449")
    wrapper.unmount()
  })

  it("restores an expanded history window after switching tabs", async () => {
    const chat = useChatStore("graph_1")
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku", "reviewer"]
    seedMessages(chat, 450)
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

    await wrapper.find("button.self-center").trigger("click")
    await flushPromises()
    expect(renderedIds(wrapper)[0]).toBe("m_50")

    chat.setGroupActiveTab(groupId, "reviewer")
    await flushPromises()
    expect(renderedIds(wrapper)[0]).toBe("r_0")

    chat.setGroupActiveTab(groupId, "kohaku")
    await flushPromises()
    expect(renderedIds(wrapper)[0]).toBe("m_50")
    wrapper.unmount()
  })

  it("restores history and scroll state independently when a panel changes groups", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 450)
    const firstGroupId = chat.enableGroups()
    const secondGroupId = chat.addGroup(["kohaku"], "kohaku")
    chat.groups[firstGroupId].tabs = ["kohaku"]
    chat.groups[firstGroupId].activeTab = "kohaku"
    const wrapper = mountPanel(chat, { groupId: firstGroupId })
    await flushPromises()

    await wrapper.find("button.self-center").trigger("click")
    await flushPromises()
    expect(renderedIds(wrapper)[0]).toBe("m_50")

    const viewport = wrapper.find(".chat-messages-viewport").element
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 1000 })
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 200 })
    viewport.scrollTop = 300

    await wrapper.setProps({ groupId: secondGroupId })
    await flushPromises()
    expect(renderedIds(wrapper)[0]).toBe("m_250")

    viewport.scrollTop = 700
    await wrapper.setProps({ groupId: firstGroupId })
    await flushPromises()

    expect(renderedIds(wrapper)[0]).toBe("m_50")
    expect(viewport.scrollTop).toBe(300)
    wrapper.unmount()
  })

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
})

describe("ChatPanel command results", () => {
  it("keeps clear behind the existing composer button", async () => {
    const command = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockResolvedValue({
      output: "Conversation cleared",
      data: { type: "notify", message: "Context cleared", level: "success" },
    })
    const confirm = vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm")
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    chat.commandInventoryByTab = { kohaku: { commands: [], skills: [] } }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    const wrapper = mount(ChatPanel, {
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
          ChatMessage: true,
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })
    await wrapper.find('button[aria-label="Clear context"]').trigger("click")
    await flushPromises()

    expect(command).toHaveBeenCalledWith("graph_1", "kohaku", "clear", "--force")
    expect(confirm).toHaveBeenCalledOnce()
    command.mockRestore()
    confirm.mockRestore()
  })

  it("renders /goal structured results inside the chat", async () => {
    const command = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockResolvedValue({
      output: "Goals: drive_1",
      data: {
        type: "list",
        title: "Goals",
        items: [{ label: "Ship release", description: "id=drive_1" }],
      },
    })
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    chat.commandInventoryByTab = {
      kohaku: {
        commands: [{ name: "goal", aliases: [] }],
        skills: [],
      },
    }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    const wrapper = mount(ChatPanel, {
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
          ChatMessage: true,
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })

    await wrapper.find("textarea").setValue("/goal list")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(command).toHaveBeenCalledWith("graph_1", "kohaku", "goal", "list")
    expect(chat.messagesByTab.kohaku).toHaveLength(1)
    expect(chat.messagesByTab.kohaku[0]).toMatchObject({
      role: "command_result",
      command: "/goal list",
      content: "Goals: drive_1",
      data: { type: "list", title: "Goals" },
    })
    command.mockRestore()
  })

  it.each([
    ["successful", false],
    ["failed", true],
  ])(
    "anchors a %s delayed /goal result to the branch visible at dispatch",
    async (_case, rejects) => {
      let settleCommand
      const command = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockReturnValue(
        new Promise((resolve, reject) => {
          settleCommand = rejects ? reject : resolve
        }),
      )
      const chat = useChatStore("graph_1")
      chat._instanceId = "graph_1"
      chat._instanceGraphId = "graph_1"
      chat.activeTab = "kohaku"
      chat.tabs = ["kohaku"]
      chat.eventsByTab = {
        kohaku: [
          {
            type: "user_input",
            event_id: 1,
            turn_index: 1,
            branch_id: 1,
            content: "branch one",
          },
          {
            type: "processing_start",
            event_id: 2,
            turn_index: 1,
            branch_id: 1,
          },
          {
            type: "text_chunk",
            event_id: 3,
            turn_index: 1,
            branch_id: 1,
            content: "reply",
          },
          {
            type: "processing_end",
            event_id: 4,
            turn_index: 1,
            branch_id: 1,
          },
          {
            type: "user_input",
            event_id: 5,
            turn_index: 1,
            branch_id: 2,
            content: "branch two",
          },
        ],
      }
      chat.branchViewByTab = { kohaku: { 1: 1 } }
      chat._rebuildMessages("kohaku")
      chat.commandInventoryByTab = {
        kohaku: {
          commands: [{ name: "goal", aliases: [] }],
          skills: [],
        },
      }
      chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
      const addResult = vi.spyOn(chat, "addCommandResult")
      const wrapper = mount(ChatPanel, {
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
            ChatMessage: true,
            ModelSwitcher: true,
            SiteChip: true,
            StatusDot: true,
          },
        },
      })

      await wrapper.find("textarea").setValue("/goal list")
      await wrapper.find('button[aria-label="Send message"]').trigger("click")
      await flushPromises()
      expect(command).toHaveBeenCalledOnce()

      chat.branchViewByTab.kohaku = { 1: 2 }
      chat._rebuildMessages("kohaku")
      settleCommand(
        rejects
          ? new Error("goal failed")
          : {
              output: "Goals",
              data: { type: "list", title: "Goals", items: [] },
            },
      )
      await flushPromises()

      expect(addResult).toHaveBeenCalledWith(
        "kohaku",
        "/goal list",
        rejects
          ? { error: "goal failed" }
          : {
              output: "Goals",
              data: { type: "list", title: "Goals", items: [] },
            },
        expect.objectContaining({
          branchSelection: [[1, 1]],
          anchorIndex: 2,
        }),
      )

      command.mockRestore()
      wrapper.unmount()
    },
  )

  it.each([
    ["successful", false],
    ["failed", true],
  ])(
    "does not scroll a newly selected tab for a %s result from another tab",
    async (_case, rejects) => {
      let settleCommand
      const command = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockReturnValue(
        new Promise((resolve, reject) => {
          settleCommand = rejects ? reject : resolve
        }),
      )
      const chat = useChatStore("graph_1")
      chat._instanceId = "graph_1"
      chat._instanceGraphId = "graph_1"
      chat.activeTab = "kohaku"
      chat.tabs = ["kohaku", "reviewer"]
      chat.messagesByTab = { kohaku: [], reviewer: [] }
      chat.eventsByTab = { kohaku: [], reviewer: [] }
      chat.commandInventoryByTab = {
        kohaku: {
          commands: [{ name: "goal", aliases: [] }],
          skills: [],
        },
      }
      chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
      const wrapper = mount(ChatPanel, {
        props: {
          instance: {
            id: "graph_1",
            graph_id: "graph_1",
            creatures: [
              { name: "kohaku", status: "idle" },
              { name: "reviewer", status: "idle" },
            ],
          },
        },
        global: {
          provide: { chatStore: chat },
          stubs: {
            ChatMessage: true,
            ModelSwitcher: true,
            SiteChip: true,
            StatusDot: true,
          },
        },
      })

      await wrapper.find("textarea").setValue("/goal list")
      await wrapper.find('button[aria-label="Send message"]').trigger("click")
      await flushPromises()
      expect(command).toHaveBeenCalledOnce()

      chat.activeTab = "reviewer"
      await flushPromises()
      const viewport = wrapper.find(".chat-messages-viewport").element
      Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 420 })
      viewport.scrollTop = 73

      settleCommand(
        rejects
          ? new Error("goal failed")
          : {
              output: "Goals",
              data: { type: "list", title: "Goals", items: [] },
            },
      )
      await flushPromises()

      expect(chat.messagesByTab.kohaku.at(-1)).toMatchObject({
        role: "command_result",
        ...(rejects ? { error: "goal failed" } : { content: "Goals" }),
      })
      expect(viewport.scrollTop).toBe(73)
      command.mockRestore()
      wrapper.unmount()
    },
  )

  it("drops a delayed /goal result after the chat store switches sessions", async () => {
    let resolveCommand
    const command = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockReturnValue(
      new Promise((resolve) => {
        resolveCommand = resolve
      }),
    )
    const chat = useChatStore("graph_1")
    chat._instanceGeneration = 3
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    chat.commandInventoryByTab = {
      kohaku: {
        commands: [{ name: "goal", aliases: [] }],
        skills: [],
      },
    }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    const wrapper = mount(ChatPanel, {
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
          ChatMessage: true,
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })

    await wrapper.find("textarea").setValue("/goal list")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()
    expect(command).toHaveBeenCalledOnce()

    chat._instanceGeneration += 1
    chat._instanceId = "graph_2"
    chat._instanceGraphId = "graph_2"
    resolveCommand({
      output: "wrong session",
      data: { type: "list", title: "Goals", items: [] },
    })
    await flushPromises()

    expect(chat.messagesByTab.kohaku).toEqual([])
    command.mockRestore()
    wrapper.unmount()
  })

  it("does not send a slash target to a tab selected during inventory lookup", async () => {
    let resolveTarget
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku", "reviewer"]
    chat.messagesByTab = { kohaku: [], reviewer: [] }
    localStorage.setItem("kt.chat.draft.graph_1.reviewer", "/review")
    vi.spyOn(chat, "prepareSlashSend").mockReturnValue(
      new Promise((resolve) => {
        resolveTarget = resolve
      }),
    )
    const execute = vi.spyOn(terrariumAPI, "executeCreatureCommand")
    const wrapper = mount(ChatPanel, {
      props: {
        instance: {
          id: "graph_1",
          graph_id: "graph_1",
          creatures: [
            { name: "kohaku", status: "idle" },
            { name: "reviewer", status: "idle" },
          ],
        },
      },
      global: {
        provide: { chatStore: chat },
        stubs: {
          ChatMessage: true,
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })
    await wrapper.find("textarea").setValue("/review")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    chat.activeTab = "reviewer"
    await flushPromises()

    resolveTarget({ type: "skill", name: "review" })
    await flushPromises()

    expect(execute).not.toHaveBeenCalled()
    execute.mockRestore()
  })

  it.each([
    [
      "instance generation",
      (chat) => {
        chat._instanceGeneration += 1
      },
    ],
    [
      "session id",
      (chat) => {
        chat._instanceId = "session_2"
      },
    ],
    [
      "graph id",
      (chat) => {
        chat._instanceGraphId = "graph_2"
      },
    ],
  ])(
    "does not dispatch to a same-named tab when the %s changes during slash lookup",
    async (_field, changeContext) => {
      let resolveTarget
      const chat = useChatStore("session_1")
      chat._instanceGeneration = 4
      chat._instanceId = "session_1"
      chat._instanceGraphId = "graph_1"
      chat.activeTab = "kohaku"
      chat.tabs = ["kohaku"]
      chat.messagesByTab = { kohaku: [] }
      vi.spyOn(chat, "prepareSlashSend").mockReturnValue(
        new Promise((resolve) => {
          resolveTarget = resolve
        }),
      )
      const execute = vi
        .spyOn(terrariumAPI, "executeCreatureCommand")
        .mockResolvedValue({ output: "unexpected" })
      const wrapper = mount(ChatPanel, {
        props: {
          instance: {
            id: "session_1",
            graph_id: "graph_1",
            creatures: [{ name: "kohaku", status: "idle" }],
          },
        },
        global: {
          provide: { chatStore: chat },
          stubs: {
            ChatMessage: true,
            ModelSwitcher: true,
            SiteChip: true,
            StatusDot: true,
          },
        },
      })
      const textarea = wrapper.find("textarea")
      await textarea.setValue("/review focus")
      chat.markSlashTarget("kohaku", { type: "skill", name: "old-review" })
      const staleTarget = chat._slashTargetByTab.kohaku
      await wrapper.find('button[aria-label="Send message"]').trigger("click")

      changeContext(chat)
      chat.activeTab = "kohaku"
      resolveTarget({ type: "skill", name: "review" })
      await flushPromises()

      expect(execute).not.toHaveBeenCalled()
      expect(chat._slashTargetByTab.kohaku).toBeUndefined()
      expect(staleTarget).toMatchObject({ type: "skill", name: "old-review" })
      expect(textarea.element.value).toBe("/review focus")
      execute.mockRestore()
      wrapper.unmount()
    },
  )

  it("does not dispatch when another chat group takes focus during slash lookup", async () => {
    let resolveTarget
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku", "reviewer"]
    chat.messagesByTab = { kohaku: [], reviewer: [] }
    const sourceGroup = chat.enableGroups()
    const otherGroup = chat.splitGroup(sourceGroup, "horizontal", "after", "reviewer")
    chat.setFocusedGroup(sourceGroup)
    vi.spyOn(chat, "prepareSlashSend").mockReturnValue(
      new Promise((resolve) => {
        resolveTarget = resolve
      }),
    )
    const execute = vi
      .spyOn(terrariumAPI, "executeCreatureCommand")
      .mockResolvedValue({ output: "unexpected" })
    const wrapper = mount(ChatPanel, {
      props: {
        instance: {
          id: "graph_1",
          graph_id: "graph_1",
          creatures: [
            { name: "kohaku", status: "idle" },
            { name: "reviewer", status: "idle" },
          ],
        },
        groupId: sourceGroup,
      },
      global: {
        provide: { chatStore: chat },
        stubs: {
          ChatMessage: true,
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })
    const textarea = wrapper.find("textarea")
    await textarea.setValue("/review focus")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")

    chat.setFocusedGroup(otherGroup)
    expect(chat.activeTab).toBe("reviewer")
    expect(chat.groups[sourceGroup].activeTab).toBe("kohaku")
    resolveTarget({ type: "skill", name: "review" })
    await flushPromises()

    expect(execute).not.toHaveBeenCalled()
    expect(textarea.element.value).toBe("/review focus")
    execute.mockRestore()
    wrapper.unmount()
  })

  it("dismisses the slash menu without interrupting an active turn", async () => {
    const chat = useChatStore("graph_1")
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    chat.processingByTab = { kohaku: true }
    chat.commandInventoryByTab = {
      kohaku: {
        commands: [{ name: "help", aliases: [], description: "Show help" }],
        skills: [],
      },
    }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    const interrupt = vi.spyOn(chat, "interrupt").mockResolvedValue(undefined)
    const wrapper = mount(ChatPanel, {
      props: {
        instance: {
          id: "graph_1",
          graph_id: "graph_1",
          creatures: [{ name: "kohaku", status: "running" }],
        },
      },
      global: {
        provide: { chatStore: chat },
        stubs: {
          ChatMessage: true,
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })
    const textarea = wrapper.find("textarea")
    await textarea.setValue("/")
    await flushPromises()
    expect(wrapper.find("#slash-command-menu").exists()).toBe(true)

    await textarea.trigger("keydown", { key: "Escape" })
    await flushPromises()

    expect(wrapper.find("#slash-command-menu").exists()).toBe(false)
    expect(textarea.attributes("aria-expanded")).toBe("false")
    expect(interrupt).not.toHaveBeenCalled()

    await textarea.trigger("blur")
    await textarea.trigger("focus")
    await flushPromises()
    expect(wrapper.find("#slash-command-menu").exists()).toBe(true)

    await textarea.trigger("keydown", { key: "Escape" })
    await textarea.setValue("/h")
    await flushPromises()
    expect(wrapper.find("#slash-command-menu").exists()).toBe(true)
    expect(interrupt).not.toHaveBeenCalled()
    interrupt.mockRestore()
    wrapper.unmount()
  })
})
