// Inline-editor DOM contract only; jsdom does not measure browser geometry.
import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import ChatMessage from "../ChatMessage.vue"
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

function mountEditing(pinia) {
  const store = useChatStore()
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

describe("MessageRow inline editor narrow-layout contract", () => {
  let pinia

  beforeEach(() => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: { getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn() },
    })
    pinia = createPinia()
    setActivePinia(pinia)
  })

  it("nests the attach actions and textarea in the size-container form row", async () => {
    const wrapper = mountEditing(pinia)
    await wrapper.get('[aria-label="Edit and rerun message"]').trigger("click")

    const form = wrapper.get(".message-edit-form")
    const row = wrapper.get(".message-edit-input-row")
    const attach = wrapper.get(".message-edit-attach")
    const textarea = wrapper.get("textarea.message-edit-inline")

    // The form is the containment root for the responsive rule; the row is
    // its child, and the attach group + textarea are siblings inside the row.
    expect(form.element.contains(row.element)).toBe(true)
    expect(attach.element.parentElement).toBe(row.element)
    expect(textarea.element.parentElement).toBe(row.element)
    expect(textarea.element.classList.contains("message-edit-textarea")).toBe(true)

    // The two action buttons live in the attach group (never inline with the
    // textarea as separate row children).
    expect(attach.findAll("button").length).toBeGreaterThanOrEqual(2)
    expect(row.findAll(":scope > textarea").length).toBe(1)
  })
})
