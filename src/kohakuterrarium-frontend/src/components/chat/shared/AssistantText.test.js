import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("mermaid", () => ({
  default: { initialize: vi.fn(), render: vi.fn(async () => ({ svg: "<svg></svg>" })) },
}))

vi.mock("@/utils/chatAttachments", () => ({
  buildMessageParts: async (text) => text,
  contentToEditableDraft: (content) => ({
    text: typeof content === "string" ? content : "",
    attachments: [],
  }),
  formatBytes: (size) => String(size),
  MAX_ATTACHMENT_BYTES: 10_000,
  MAX_IMAGE_BYTES: 10_000,
}))

import AssistantText from "./AssistantText.vue"
import ChatMessage from "../ChatMessage.vue"
import InlineUiBlock from "../widgets/ktui/InlineUiBlock.vue"
import { blockHash } from "../widgets/ktui/blockId.js"
import MarkdownRenderer from "../../../public/chat/MarkdownRenderer.vue"
import { useCanvasStore } from "@/stores/canvas"
import { useChatStore } from "@/stores/chat"

const BLOCK = '<ui><state n={2}/><button send="Pick {n}">Pick</button></ui>'

beforeEach(() => {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: { getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn() },
  })
  setActivePinia(createPinia())
})

describe("AssistantText", () => {
  it("keeps the single renderer path for text without a block", () => {
    const w = mount(AssistantText, { props: { content: "Plain **text** with <ul> tags" } })
    expect(w.findAllComponents(MarkdownRenderer)).toHaveLength(1)
    expect(w.find('[data-test="assistant-text"]').exists()).toBe(false)
  })

  it("draws markdown around a block and never shows the tags as text", () => {
    const w = mount(AssistantText, { props: { content: `Before\n\n${BLOCK}\n\nAfter` } })
    expect(w.findAllComponents(MarkdownRenderer).map((m) => m.props("content"))).toEqual([
      "Before\n\n",
      "\n\nAfter",
    ])
    expect(w.findAllComponents(InlineUiBlock)).toHaveLength(1)
    expect(w.text()).not.toContain("<ui")
    expect(w.text()).not.toContain("<button")
  })

  it("streaming appends reuse earlier segments and hold back a partial <ui tag", async () => {
    const w = mount(AssistantText, { props: { content: "Intro\n<u", streaming: true } })
    expect(w.text()).not.toContain("<u")
    const first = w.findAllComponents(MarkdownRenderer)[0]
    await w.setProps({ content: `Intro\n${BLOCK.slice(0, 20)}` })
    await w.setProps({ content: `Intro\n${BLOCK}\nmore` })
    const renderers = w.findAllComponents(MarkdownRenderer)
    expect(renderers[0].vm.$.uid).toBe(first.vm.$.uid)
    expect(renderers[0].props("content")).toBe("Intro\n")
    expect(renderers[1].props("content")).toBe("\nmore")
    expect(w.text()).not.toContain("<ui")
  })

  it("passes the streaming flag and forwards say", async () => {
    const w = mount(AssistantText, { props: { content: BLOCK, streaming: true } })
    expect(w.getComponent(InlineUiBlock).props("streaming")).toBe(true)
    await w.get('[data-test="ktui-button"]').trigger("click")
    expect(w.emitted("say")).toEqual([["Pick 2"]])
  })
})

describe("MessageRow inline UI", () => {
  function mountAssistant(content, part = {}) {
    const chat = useChatStore()
    const message = {
      role: "assistant",
      content,
      parts: [{ type: "text", content, ...part }],
      turnIndex: 1,
      branchId: 1,
      latestBranch: 1,
    }
    chat.messagesByTab.main = [message]
    chat.activeTab = "main"
    const send = vi.spyOn(chat, "send").mockResolvedValue(undefined)
    const w = mount(ChatMessage, {
      props: { message, messageIdx: 0, tabId: "main" },
      global: { stubs: { ToolCallBlock: true, UIEventBlock: true } },
    })
    return { w, send, chat }
  }

  it("draws a block in an assistant reply and a click sends a normal user message", async () => {
    const { w, send } = mountAssistant(`Choose:\n${BLOCK}`)
    await w.get('[data-test="ktui-button"]').trigger("click")
    await flushPromises()
    expect(send).toHaveBeenCalledWith("Pick 2")
  })

  it("refuses a message that would run a slash command", async () => {
    const { w, send } = mountAssistant('<ui><button send="/clear">Reset</button></ui>')
    await w.get('[data-test="ktui-button"]').trigger("click")
    await flushPromises()
    expect(send).not.toHaveBeenCalled()
  })

  it("a streaming text part is passed as streaming", () => {
    const { w } = mountAssistant("<ui><text>par", { _streaming: true })
    expect(w.getComponent(InlineUiBlock).props("streaming")).toBe(true)
    expect(w.find('[data-test="inline-ui-error"]').exists()).toBe(false)
  })

  it("Open in canvas pins a page block on the canvas", async () => {
    const { w } = mountAssistant('<ui layout="page" title="Board"><text>hi</text></ui>')
    await w.get('[data-test="inline-ui-open-canvas"]').trigger("click")
    const canvas = useCanvasStore()
    expect(canvas.activeArtifact).toMatchObject({
      type: "ktui",
      lang: "ktui",
      content: '<ui layout="page" title="Board"><text>hi</text></ui>',
    })
  })

  it("user text with <ui> stays plain markdown", () => {
    const chat = useChatStore()
    const message = {
      role: "user",
      content: BLOCK,
      turnIndex: 1,
      branchId: 1,
      latestBranch: 1,
      userPosition: 0,
    }
    chat.messagesByTab.main = [message]
    chat.activeTab = "main"
    const w = mount(ChatMessage, {
      props: { message, messageIdx: 0, tabId: "main" },
      global: { stubs: { ToolCallBlock: true, UIEventBlock: true } },
    })
    expect(w.findComponent(InlineUiBlock).exists()).toBe(false)
  })

  it("keeps block state per reply in the store, and holds it before the reply has a turn", async () => {
    const counter =
      '<ui><state n={1}/><button set={{n: n + 1}}>Add</button><stat label="N">{n}</stat></ui>'
    const { w, chat } = mountAssistant(counter)
    const block = `${blockHash(counter)}.0`
    await w.get('[data-test="ktui-button"]').trigger("click")
    expect(chat.inlineUiStateOf("main", 1, 1, block)).toEqual({ n: 2 })

    const holdSpy = vi.spyOn(chat, "holdInlineUiState")
    const live = {
      role: "assistant",
      content: counter,
      parts: [{ type: "text", content: counter }],
    }
    chat.messagesByTab.live = [live]
    const lw = mount(ChatMessage, {
      props: { message: live, messageIdx: 0, tabId: "live" },
      global: { stubs: { ToolCallBlock: true, UIEventBlock: true } },
    })
    await lw.get('[data-test="ktui-button"]').trigger("click")
    expect(holdSpy).toHaveBeenCalledWith("live", block, { n: 2 })
    expect(chat.inlineUiStateByTab.live).toBeUndefined()
  })

  it("shows recorded state for its block", async () => {
    const counter = '<ui><state n={1}/><stat label="N">{n}</stat></ui>'
    const chat = useChatStore()
    chat._handleInlineUiState("main", {
      ui_turn: 1,
      ui_branch: 1,
      block: `${blockHash(counter)}.0`,
      state: { n: 9 },
    })
    const { w } = mountAssistant(counter)
    await flushPromises()
    expect(w.get('[data-test="ktui-stat-value"]').text()).toBe("9")
  })
})
