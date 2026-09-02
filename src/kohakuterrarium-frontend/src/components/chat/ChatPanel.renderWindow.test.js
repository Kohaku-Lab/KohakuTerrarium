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
})
