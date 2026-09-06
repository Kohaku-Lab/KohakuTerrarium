import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"

import { compileStyle, parse } from "@vue/compiler-sfc"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { ElMessage, ElMessageBox } from "element-plus"
import { defineComponent, h, onMounted, ref } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import ChatPanel from "./ChatPanel.vue"
import { createChatScrollScheduler } from "@/components/chat/chatScrollScheduler"
import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

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
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("ChatPanel transcript attachment drops", () => {
  function mountPanel({ groupId = null } = {}) {
    const chat = useChatStore("graph_drop")
    chat._instanceId = "graph_drop"
    chat._instanceGraphId = "graph_drop"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
    chat.messagesByTab = { kohaku: [] }
    if (groupId) {
      chat.groups = { [groupId]: { id: groupId, tabs: ["kohaku"], activeTab: "kohaku" } }
      chat.focusedGroupId = groupId
    }
    return mount(ChatPanel, {
      props: {
        instance: {
          id: "graph_drop",
          graph_id: "graph_drop",
          creatures: [{ name: "kohaku", status: "idle" }],
        },
        groupId,
      },
      global: {
        provide: { chatStore: chat },
        stubs: { ChatMessage: true, ModelSwitcher: true, SiteChip: true, StatusDot: true },
      },
    })
  }

  function dragEvent(files, types = ["Files"]) {
    return { dataTransfer: { files, types }, preventDefault: vi.fn(), stopPropagation: vi.fn() }
  }

  it.each([
    ["standalone", null],
    ["grouped", "group_1"],
  ])("attaches a file dropped on the %s transcript once", async (_label, groupId) => {
    const wrapper = mountPanel({ groupId })
    const bubble = wrapper.findComponent({ name: "ChatTranscriptSection" }).element.parentElement
    const dropped = new File(["notes"], "notes.txt", { type: "text/plain" })
    const drop = dragEvent([dropped])

    bubble.dispatchEvent(
      Object.assign(new Event("drop", { bubbles: true, cancelable: true }), drop),
    )
    await flushPromises()

    expect(drop.preventDefault).toHaveBeenCalledOnce()
    expect(drop.stopPropagation).toHaveBeenCalledOnce()
    expect(wrapper.findAll(".kt-chat-composer__chip")).toHaveLength(1)
    expect(wrapper.text()).toContain("notes.txt")
  })

  it("preserves attachment validation for transcript drops", async () => {
    const wrapper = mountPanel()
    const error = vi.spyOn(ElMessage, "error").mockImplementation(() => {})
    const bubble = wrapper.findComponent({ name: "ChatTranscriptSection" }).element.parentElement
    const dropped = { name: "huge.bin", type: "application/octet-stream", size: 100 * 1024 * 1024 }

    bubble.dispatchEvent(
      Object.assign(new Event("drop", { bubbles: true, cancelable: true }), dragEvent([dropped])),
    )
    await flushPromises()

    expect(wrapper.findAll(".kt-chat-composer__chip")).toHaveLength(0)
    expect(error).toHaveBeenCalledOnce()
  })

  it("does not double-add a file dropped directly on the composer", async () => {
    const wrapper = mountPanel()
    const dropped = new File(["notes"], "notes.txt", { type: "text/plain" })

    await wrapper.findComponent({ name: "ChatComposer" }).trigger("drop", {
      dataTransfer: { files: [dropped], types: ["Files"] },
    })
    await flushPromises()

    expect(wrapper.findAll(".kt-chat-composer__chip")).toHaveLength(1)
  })

  it("preserves grouped tab drops", async () => {
    const wrapper = mountPanel({ groupId: "group_1" })
    const bubble = wrapper.findComponent({ name: "ChatTranscriptSection" }).element.parentElement
    const drop = dragEvent([], ["application/x-kt-tab"])
    const onBubbleDrop = vi.spyOn(wrapper.vm.tabDrag, "onBubbleDrop")

    bubble.dispatchEvent(
      Object.assign(new Event("drop", { bubbles: true, cancelable: true }), drop),
    )
    await flushPromises()

    expect(onBubbleDrop).toHaveBeenCalledOnce()
    expect(drop.preventDefault).toHaveBeenCalled()
    expect(drop.stopPropagation).toHaveBeenCalledOnce()
    expect(wrapper.findAll(".kt-chat-composer__chip")).toHaveLength(0)
  })
})

describe("ChatPanel disconnected sends", () => {
  function mountPanel({ tab = "kohaku", wsOpen = false } = {}) {
    const chat = useChatStore("graph_send")
    chat._instanceId = "graph_send"
    chat._instanceGraphId = "graph_send"
    chat.activeTab = tab
    chat.tabs = [tab]
    chat.messagesByTab = { [tab]: [] }
    chat.processingByTab = { [tab]: false }
    chat.wsStatus = wsOpen ? "open" : "reconnecting"
    chat._ws = wsOpen ? { readyState: WebSocket.OPEN, send: vi.fn() } : null
    const wrapper = mount(ChatPanel, {
      props: {
        instance: {
          id: "graph_send",
          graph_id: "graph_send",
          creatures: [{ name: "kohaku", status: "idle" }],
        },
      },
      global: {
        provide: { chatStore: chat },
        stubs: { ChatMessage: true, ModelSwitcher: true, SiteChip: true, StatusDot: true },
      },
    })
    return { chat, wrapper }
  }

  it("keeps the draft when there is no active tab", async () => {
    const error = vi.spyOn(ElMessage, "error").mockImplementation(() => {})
    const { chat, wrapper } = mountPanel()
    chat.activeTab = null
    await flushPromises()

    await wrapper.find("textarea").setValue("wait for a tab")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(wrapper.find("textarea").element.value).toBe("wait for a tab")
    expect(error).toHaveBeenCalledWith("Select a chat before sending")
  })

  it("keeps the draft and attachment when a creature socket is disconnected", async () => {
    const error = vi.spyOn(ElMessage, "error").mockImplementation(() => {})
    const { chat, wrapper } = mountPanel()
    const textarea = wrapper.find("textarea")
    const file = new File(["notes"], "notes.txt", { type: "text/plain" })

    await textarea.setValue("keep this draft")
    await wrapper.findComponent({ name: "ChatComposer" }).trigger("drop", {
      dataTransfer: { files: [file], types: ["Files"] },
    })
    await flushPromises()
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(textarea.element.value).toBe("keep this draft")
    expect(wrapper.findAll(".kt-chat-composer__chip")).toHaveLength(1)
    expect(localStorage.getItem("kt.chat.draft.graph_send.kohaku")).toBe("keep this draft")
    expect(chat.messagesByTab.kohaku).toEqual([])
    expect(error).toHaveBeenCalledWith("Chat is not connected")
  })

  it("uses the socket state when the connection status is stale", async () => {
    const error = vi.spyOn(ElMessage, "error").mockImplementation(() => {})
    const { chat, wrapper } = mountPanel()
    chat.wsStatus = "open"
    chat._ws = { readyState: WebSocket.CLOSED, send: vi.fn() }

    await wrapper.find("textarea").setValue("keep stale-status draft")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(wrapper.find("textarea").element.value).toBe("keep stale-status draft")
    expect(chat.messagesByTab.kohaku).toEqual([])
    expect(error).toHaveBeenCalledWith("Chat is not connected")
  })

  it("keeps the draft when the socket closes between the panel and store checks", async () => {
    const error = vi.spyOn(ElMessage, "error").mockImplementation(() => {})
    const { chat, wrapper } = mountPanel()
    let readyStateReads = 0
    chat.wsStatus = "open"
    chat._ws = {
      get readyState() {
        return readyStateReads++ === 0 ? WebSocket.OPEN : WebSocket.CLOSED
      },
      send: vi.fn(),
    }

    await wrapper.find("textarea").setValue("keep raced draft")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(wrapper.find("textarea").element.value).toBe("keep raced draft")
    expect(chat.messagesByTab.kohaku).toEqual([])
    expect(error).toHaveBeenCalledWith("Command failed: Chat is not connected")
  })

  it("keeps an attached goal message on the websocket path while disconnected", async () => {
    const error = vi.spyOn(ElMessage, "error").mockImplementation(() => {})
    const execute = vi.spyOn(terrariumAPI, "executeCreatureCommand")
    const { chat, wrapper } = mountPanel()
    chat.commandInventoryByTab = {
      kohaku: { commands: [{ name: "goal", aliases: [] }], skills: [] },
    }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }
    const file = new File(["notes"], "notes.txt", { type: "text/plain" })

    await wrapper.find("textarea").setValue("/goal list")
    await wrapper.findComponent({ name: "ChatComposer" }).trigger("drop", {
      dataTransfer: { files: [file], types: ["Files"] },
    })
    await flushPromises()
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(execute).not.toHaveBeenCalled()
    expect(wrapper.find("textarea").element.value).toBe("/goal list")
    expect(wrapper.findAll(".kt-chat-composer__chip")).toHaveLength(1)
    expect(chat.messagesByTab.kohaku).toEqual([])
    expect(chat._slashTargetByTab.kohaku).toBeUndefined()
    expect(chat._pendingCommandResultContextsByTab.kohaku).toBeUndefined()
    expect(error).toHaveBeenCalledWith("Chat is not connected")
  })

  it("still sends channel messages and attachments while the creature socket is disconnected", async () => {
    const sendToChannel = vi.spyOn(terrariumAPI, "sendToChannel").mockResolvedValue({})
    const { chat, wrapper } = mountPanel({ tab: "ch:general" })
    const file = new File(["notes"], "notes.txt", { type: "text/plain" })

    await wrapper.find("textarea").setValue("channel update")
    await wrapper.findComponent({ name: "ChatComposer" }).trigger("drop", {
      dataTransfer: { files: [file], types: ["Files"] },
    })
    await flushPromises()
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(sendToChannel).toHaveBeenCalledWith(
      "graph_send",
      "general",
      [
        { type: "text", text: "channel update" },
        {
          type: "file",
          file: expect.objectContaining({ name: "notes.txt", content: "notes" }),
        },
      ],
      "human",
    )
    expect(wrapper.find("textarea").element.value).toBe("")
    expect(wrapper.findAll(".kt-chat-composer__chip")).toHaveLength(0)
    expect(chat.messagesByTab["ch:general"]).toHaveLength(1)
  })

  it("does not clear a replacement draft after an accepted creature send", async () => {
    const { chat, wrapper } = mountPanel({ wsOpen: true })
    let resolveSend
    const send = vi.spyOn(chat, "send").mockReturnValue(
      new Promise((resolve) => {
        resolveSend = resolve
      }),
    )

    await wrapper.find("textarea").setValue("first draft")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await vi.waitFor(() => expect(send).toHaveBeenCalledOnce())
    await wrapper.find("textarea").setValue("replacement draft")
    await wrapper.find("textarea").setValue("first draft")
    resolveSend()
    await flushPromises()

    expect(wrapper.find("textarea").element.value).toBe("first draft")
    expect(localStorage.getItem("kt.chat.draft.graph_send.kohaku")).toBe("first draft")
  })

  it("does not clear replacement attachments after an accepted creature send", async () => {
    const { chat, wrapper } = mountPanel({ wsOpen: true })
    let resolveSend
    const send = vi.spyOn(chat, "send").mockReturnValue(
      new Promise((resolve) => {
        resolveSend = resolve
      }),
    )
    const first = new File(["first"], "first.txt", { type: "text/plain" })
    const replacement = new File(["replacement"], "replacement.txt", { type: "text/plain" })

    await wrapper.findComponent({ name: "ChatComposer" }).trigger("drop", {
      dataTransfer: { files: [first], types: ["Files"] },
    })
    await flushPromises()
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await vi.waitFor(() => expect(send).toHaveBeenCalledOnce())
    wrapper.vm.attachments = [{ file: replacement, name: replacement.name, kind: "file" }]
    await flushPromises()
    resolveSend()
    await flushPromises()

    expect(wrapper.findAll(".kt-chat-composer__chip")).toHaveLength(1)
    expect(wrapper.text()).toContain("replacement.txt")
  })

  it("ignores a second submit while the first dispatch is pending", async () => {
    const { chat, wrapper } = mountPanel({ wsOpen: true })
    let resolveSend
    const send = vi.spyOn(chat, "send").mockReturnValue(
      new Promise((resolve) => {
        resolveSend = resolve
      }),
    )

    await wrapper.find("textarea").setValue("send once")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await vi.waitFor(() => expect(send).toHaveBeenCalledOnce())
    await wrapper.find('button[aria-label="Send message"]').trigger("click")

    expect(send).toHaveBeenCalledOnce()
    resolveSend()
    await flushPromises()
  })

  it("surfaces a synchronous creature send failure without clearing the draft", async () => {
    const error = vi.spyOn(ElMessage, "error").mockImplementation(() => {})
    const { chat, wrapper } = mountPanel({ wsOpen: true })
    vi.spyOn(chat, "send").mockImplementation(() => {
      throw Error("sync send failure")
    })

    await wrapper.find("textarea").setValue("keep sync-failure draft")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(wrapper.find("textarea").element.value).toBe("keep sync-failure draft")
    expect(error).toHaveBeenCalledWith("Command failed: sync send failure")
  })

  it("drops a delayed goal result after a same-id generation change", async () => {
    let resolveCommand
    const execute = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockReturnValue(
      new Promise((resolve) => {
        resolveCommand = resolve
      }),
    )
    const { chat, wrapper } = mountPanel()
    chat._instanceGeneration = 4
    chat.commandInventoryByTab = {
      kohaku: { commands: [{ name: "goal", aliases: [] }], skills: [] },
    }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }

    await wrapper.find("textarea").setValue("/goal list")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await vi.waitFor(() => expect(execute).toHaveBeenCalledOnce())
    chat._instanceGeneration = 5
    resolveCommand({ output: "stale goals", data: { type: "list", title: "Goals", items: [] } })
    await flushPromises()

    expect(chat.messagesByTab.kohaku).toEqual([])
    expect(chat._pendingCommandResultContextsByTab.kohaku).toBeUndefined()
  })

  it("still dispatches a plain goal command while the creature socket is disconnected", async () => {
    const execute = vi.spyOn(terrariumAPI, "executeCreatureCommand").mockResolvedValue({
      output: "Goals",
      data: { type: "list", title: "Goals", items: [] },
    })
    const { chat, wrapper } = mountPanel()
    chat.commandInventoryByTab = {
      kohaku: { commands: [{ name: "goal", aliases: [] }], skills: [] },
    }
    chat._commandInventoryFetchedAtByTab = { kohaku: Date.now() }

    await wrapper.find("textarea").setValue("/goal list")
    await wrapper.find('button[aria-label="Send message"]').trigger("click")
    await flushPromises()

    expect(execute).toHaveBeenCalledWith("graph_send", "kohaku", "goal", "list")
    expect(wrapper.find("textarea").element.value).toBe("")
    expect(chat.messagesByTab.kohaku.at(-1)).toMatchObject({
      role: "command_result",
      content: "Goals",
    })
  })
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
})

describe("ChatPanel render window", () => {
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

  function seedMessages(chat, count) {
    chat.messagesByTab = {
      kohaku: Array.from({ length: count }, (_, i) => ({
        id: `m_${i}`,
        role: i % 2 ? "assistant" : "user",
        content: `message ${i}`,
      })),
    }
  }

  it.each(["text/uri-list", "text/plain"])(
    "prevents a %s drop on the conversation bubble without adding an attachment",
    async (type) => {
      const chat = useChatStore("graph_1")
      chat.messagesByTab = { kohaku: [] }
      const wrapper = mountPanel(chat)
      await flushPromises()
      const transcript = wrapper.findComponent({ name: "ChatTranscriptSection" })
      const bubble = transcript.element.parentElement
      const dataTransfer = { types: [type], files: [] }
      const dragOver = new Event("dragover", { bubbles: true, cancelable: true })
      Object.defineProperty(dragOver, "dataTransfer", { value: dataTransfer })
      const event = new Event("drop", { bubbles: true, cancelable: true })
      Object.defineProperty(event, "dataTransfer", { value: dataTransfer })

      bubble.dispatchEvent(dragOver)
      bubble.dispatchEvent(event)
      await flushPromises()

      expect(dragOver.defaultPrevented).toBe(true)
      expect(event.defaultPrevented).toBe(true)
      expect(wrapper.vm.attachments).toEqual([])
    },
  )

  it("uses the rendered transcript scroll viewport as the image query container", async () => {
    const chat = useChatStore("graph_1")
    chat.messagesByTab = { kohaku: [] }
    const wrapper = mountPanel(chat)
    await flushPromises()

    const transcript = wrapper.findComponent({ name: "ChatTranscriptSection" })
    const viewport = transcript.get(".kt-transcript-viewport")
    expect(viewport.classes()).toContain("chat-messages-viewport")

    const source = readFileSync(fileURLToPath(import.meta.resolve("./ChatPanel.vue")), "utf8")
    const { descriptor } = parse(source, { filename: "ChatPanel.vue" })
    expect(descriptor.styles).toEqual(
      expect.arrayContaining([expect.objectContaining({ scoped: true, src: "./chat-panel.css" })]),
    )
    const cssSource = readFileSync(fileURLToPath(import.meta.resolve("./chat-panel.css")), "utf8")
    const { code, errors } = compileStyle({
      filename: "ChatPanel.vue",
      id: "data-v-chat-panel",
      source: cssSource,
      scoped: true,
    })
    expect(errors).toEqual([])
    expect(code).toMatch(
      /\[data-v-chat-panel\]\s+\.chat-messages-viewport\s*{[^}]*container-type:\s*size/s,
    )
  })

  it("keeps a later uniquely-id'd message mounted when an earlier message is removed", async () => {
    const chat = useChatStore("graph_1")
    seedMessages(chat, 3)
    const mounts = new Map()
    const MessageStub = defineComponent({
      props: {
        message: { type: Object, required: true },
        messageIdx: { type: Number, required: true },
      },
      setup(props) {
        const messageId = props.message.id
        const expanded = ref(false)
        onMounted(() => mounts.set(messageId, (mounts.get(messageId) || 0) + 1))
        return () =>
          h(
            "button",
            {
              class: "identity-stub",
              "data-index": props.messageIdx,
              "data-message-id": messageId,
              onClick: () => (expanded.value = !expanded.value),
            },
            expanded.value
              ? `expanded:${props.message.content}`
              : `collapsed:${props.message.content}`,
          )
      },
    })
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.activeTab = "kohaku"
    chat.tabs = ["kohaku"]
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
          ChatMessage: MessageStub,
          ModelSwitcher: true,
          SiteChip: true,
          StatusDot: true,
        },
      },
    })
    await flushPromises()
    const later = wrapper.get('[data-message-key="m_2"]')
    const laterNode = later.element
    await later.trigger("click")
    expect(later.text()).toBe("expanded:message 2")

    chat.messagesByTab.kohaku.splice(0, 1)
    await flushPromises()

    const retained = wrapper.get('[data-message-key="m_2"]')
    expect(retained.element).toBe(laterNode)
    expect(retained.attributes("data-index")).toBe("1")
    expect(retained.text()).toBe("expanded:message 2")
    expect(mounts.get("m_2")).toBe(1)
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
    const wrapper = mountChatPanel({
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
    const wrapper = mountChatPanel({
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
      const wrapper = mountChatPanel({
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
      const wrapper = mountChatPanel({
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
      const viewport = wrapper.find(".kt-transcript-viewport").element
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
    const wrapper = mountChatPanel({
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
})
