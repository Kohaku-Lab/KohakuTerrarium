import { describe, expect, it } from "vitest"

import ConversationMessageImplementation from "../../components/chat/shared/ConversationMessage.js"
import ChatTranscriptSectionImplementation from "../../components/chat/shared/ChatTranscriptSection.js"
import MarkdownRendererImplementation from "./MarkdownRenderer.vue"
import * as chatUi from "@kohakuterrarium/chat-ui"
import {
  ChatTranscriptSection,
  computeRenderGroups,
  ConversationMessage,
  DEFAULT_TOOL_BATCH_THRESHOLD,
  MarkdownRenderer,
  summarizeBatch,
} from "@kohakuterrarium/chat-ui"

describe("public Chat UI entry", () => {
  it("exports exactly the temporary public contract", () => {
    expect(Object.keys(chatUi)).toEqual(
      expect.arrayContaining([
        "ChatComposer",
        "ChatTranscriptSection",
        "ConversationMessage",
        "MarkdownRenderer",
        "shouldSendOnEnter",
        "buildMessageParts",
        "validateAttachment",
        "validateAttachments",
      ]),
    )
  })

  it("exports the canonical shared component identities", () => {
    expect(ConversationMessage).toBe(ConversationMessageImplementation)
    expect(ChatTranscriptSection).toBe(ChatTranscriptSectionImplementation)
    expect(MarkdownRenderer).toBe(MarkdownRendererImplementation)
  })

  it("exports the canonical grouping API", () => {
    const tools = [
      { id: "a", type: "tool", kind: "tool", name: "read", status: "done" },
      { id: "b", type: "tool", kind: "tool", name: "read", status: "running" },
      { id: "c", type: "tool", kind: "tool", name: "bash", status: "done" },
    ]

    expect(DEFAULT_TOOL_BATCH_THRESHOLD).toBe(3)
    expect(computeRenderGroups(tools)).toEqual([{ type: "tool-batch", tools, id: "batch_a" }])
    expect(summarizeBatch(tools)).toEqual({
      counts: { done: 2, running: 1, error: 0, interrupted: 0, other: 0 },
      names: [
        ["read", 2],
        ["bash", 1],
      ],
      total: 3,
    })
  })
})
