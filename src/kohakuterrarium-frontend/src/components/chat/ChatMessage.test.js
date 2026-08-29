import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import ChatMessage from "./ChatMessage.vue"
import { useChatStore } from "@/stores/chat"

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

function mountMessage(store, pinia) {
  const message = {
    role: "user",
    content: "original draft",
    turnIndex: 1,
    branchId: 1,
    latestBranch: 1,
    userPosition: 0,
  }
  store.messagesByTab.main = [message]
  store.activeTab = "main"
  return mount(ChatMessage, {
    props: { message, messageIdx: 0, tabId: "main" },
    global: {
      plugins: [pinia],
      stubs: {
        MarkdownRenderer: true,
        ToolCallBlock: true,
        ToolBatchGroup: true,
        UIEventBlock: true,
        ContentParts: true,
      },
    },
  })
}

describe("ChatMessage branch operations", () => {
  let pinia

  beforeEach(() => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: vi.fn(() => null),
        setItem: vi.fn(),
        removeItem: vi.fn(),
      },
    })
    pinia = createPinia()
    setActivePinia(pinia)
  })

  it("renders a bounded reasoning preview and mounts the full text only while expanded", async () => {
    const store = useChatStore()
    const longReasoning = "think ".repeat(100)
    const message = {
      id: "a1",
      role: "assistant",
      parts: [
        {
          type: "reasoning",
          id: "r1",
          source: "reasoning_content",
          text: longReasoning,
          signature: "sig",
        },
        { type: "text", id: "t1", content: "answer" },
      ],
    }
    store.messagesByTab.main = [message]
    store.activeTab = "main"
    const wrapper = mount(ChatMessage, {
      props: { message, messageIdx: 0, tabId: "main" },
      global: {
        plugins: [pinia],
        stubs: {
          MarkdownRenderer: true,
          ToolCallBlock: true,
          ToolCallBatch: true,
          UIEventBlock: true,
        },
      },
    })

    const reasoning = wrapper.get("details")
    const summary = reasoning.get("summary")
    expect(summary.classes()).not.toContain("flex")
    expect(summary.get(".reasoning-summary-row").classes()).toContain("inline-flex")
    expect(summary.get(".reasoning-preview").classes()).toContain("truncate")
    expect(reasoning.get(".reasoning-preview").text()).toBe(`${longReasoning.slice(0, 240)}…`)
    expect(reasoning.find(".reasoning-full").exists()).toBe(false)

    reasoning.element.open = true
    await reasoning.trigger("toggle")
    expect(reasoning.get(".reasoning-full").text()).toBe(`${longReasoning}\n[signature: sig]`)

    reasoning.element.open = false
    await reasoning.trigger("toggle")
    expect(reasoning.find(".reasoning-full").exists()).toBe(false)
  })

  it("does not mount prototype-named reasoning before expansion", async () => {
    const store = useChatStore()
    const message = {
      id: "a1",
      role: "assistant",
      parts: [{ type: "reasoning", id: "constructor", source: "reasoning", text: "private" }],
    }
    store.messagesByTab.main = [message]
    store.activeTab = "main"
    const wrapper = mount(ChatMessage, {
      props: { message, messageIdx: 0, tabId: "main" },
      global: {
        plugins: [pinia],
        stubs: {
          MarkdownRenderer: true,
          ToolCallBlock: true,
          ToolCallBatch: true,
          UIEventBlock: true,
        },
      },
    })

    const reasoning = wrapper.get("details")
    expect(reasoning.find(".reasoning-full").exists()).toBe(false)
    reasoning.element.open = true
    await reasoning.trigger("toggle")
    expect(reasoning.get(".reasoning-full").text()).toBe("private")
  })

  it("does not split a surrogate pair at the preview boundary", () => {
    const store = useChatStore()
    const text = `${"a".repeat(239)}😀tail`
    const message = {
      id: "a1",
      role: "assistant",
      parts: [{ type: "reasoning", id: "r1", source: "reasoning", text }],
    }
    store.messagesByTab.main = [message]
    store.activeTab = "main"
    const wrapper = mount(ChatMessage, {
      props: { message, messageIdx: 0, tabId: "main" },
      global: {
        plugins: [pinia],
        stubs: {
          MarkdownRenderer: true,
          ToolCallBlock: true,
          ToolCallBatch: true,
          UIEventBlock: true,
        },
      },
    })

    expect(wrapper.get(".reasoning-preview").text()).toBe(`${"a".repeat(239)}…`)
  })

  it("preserves signature-only reasoning when expanded", async () => {
    const store = useChatStore()
    const message = {
      id: "a1",
      role: "assistant",
      parts: [{ type: "reasoning", id: "r1", source: "reasoning", text: "", signature: "sig" }],
    }
    store.messagesByTab.main = [message]
    store.activeTab = "main"
    const wrapper = mount(ChatMessage, {
      props: { message, messageIdx: 0, tabId: "main" },
      global: {
        plugins: [pinia],
        stubs: {
          MarkdownRenderer: true,
          ToolCallBlock: true,
          ToolCallBatch: true,
          UIEventBlock: true,
        },
      },
    })

    const reasoning = wrapper.get("details")
    reasoning.element.open = true
    await reasoning.trigger("toggle")

    expect(reasoning.get(".reasoning-full").text()).toBe("[signature: sig]")
  })

  it("keeps Save & Rerun bound to the message tab after the active tab changes", async () => {
    const store = useChatStore()
    store._instanceId = "instance"
    const editSpy = vi.spyOn(store, "editMessage").mockResolvedValue({ ok: true })
    const wrapper = mountMessage(store, pinia)

    await wrapper.get('[aria-label="Edit and rerun message"]').trigger("click")
    store.activeTab = "other"
    await wrapper.get("textarea").setValue("updated draft")
    await wrapper.get('[aria-label="Save and rerun"]').trigger("click")

    expect(editSpy).toHaveBeenCalledWith(
      0,
      "updated draft",
      expect.objectContaining({ tabId: "main" }),
    )
  })

  it("keeps the editor mounted and disabled until Save & Rerun is accepted", async () => {
    const store = useChatStore()
    store._instanceId = "instance"
    let resolveEdit
    store.editMessage = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveEdit = resolve
        }),
    )
    const wrapper = mountMessage(store, pinia)

    await wrapper.get('[aria-label="Edit and rerun message"]').trigger("click")
    const textarea = wrapper.get("textarea")
    await textarea.setValue("updated draft")
    await wrapper.get('[aria-label="Save and rerun"]').trigger("click")

    expect(wrapper.get("textarea").attributes("disabled")).toBeDefined()
    expect(wrapper.text()).toContain("Starting")
    resolveEdit({ ok: true })
    await vi.waitFor(() => expect(wrapper.find("textarea").exists()).toBe(false))
  })

  it("disables branch controls only for the active tab operation", async () => {
    const store = useChatStore()
    store._instanceId = "instance"
    const wrapper = mountMessage(store, pinia)
    store.branchOperationByTab.main = { type: "edit", phase: "starting" }
    await wrapper.vm.$nextTick()
    expect(
      wrapper.get('[aria-label="Edit and rerun message"]').attributes("disabled"),
    ).toBeDefined()

    store.branchOperationByTab.main = null
    store.branchOperationByTab.other = { type: "regenerate", phase: "starting" }
    await wrapper.vm.$nextTick()
    expect(
      wrapper.get('[aria-label="Edit and rerun message"]').attributes("disabled"),
    ).toBeUndefined()

    await wrapper.setProps({ tabId: "other" })
    await wrapper.vm.$nextTick()
    expect(
      wrapper.get('[aria-label="Edit and rerun message"]').attributes("disabled"),
    ).toBeDefined()
  })

  it("preserves and refocuses the editor while showing a rejected operation error", async () => {
    const store = useChatStore()
    store._instanceId = "instance"
    store.editMessage = vi.fn().mockResolvedValue({ ok: false, error: "branch collision" })
    const wrapper = mountMessage(store, pinia)
    document.body.appendChild(wrapper.element)

    await wrapper.get('[aria-label="Edit and rerun message"]').trigger("click")
    const textarea = wrapper.get("textarea")
    await textarea.setValue("keep this draft")
    await wrapper.get('[aria-label="Save and rerun"]').trigger("click")

    await vi.waitFor(() => expect(wrapper.text()).toContain("branch collision"))
    expect(wrapper.get("textarea").element.value).toBe("keep this draft")
    expect(document.activeElement).toBe(wrapper.get("textarea").element)
  })
})
