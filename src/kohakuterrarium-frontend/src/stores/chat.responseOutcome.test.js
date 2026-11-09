import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { _replayEvents, useChatStore } from "./chat"

const response_outcome = {
  code: "response_unconfirmed",
  outcome: "unknown",
  retry_safety: "may_duplicate",
}

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => useChatStore()._cleanup?.())

describe("persistent response outcomes", () => {
  it.each(["tool_done", "tool_error", "subagent_done", "subagent_error"])(
    "keeps uncached branch ancestry until processing_end for late %s",
    (activity_type) => {
      const chat = useChatStore()
      chat.activeTab = "main"
      chat.messagesByTab.main = [
        {
          role: "assistant",
          _streaming: true,
          parts: [{ type: "text", content: "partial", _streaming: true }],
        },
      ]
      chat.eventsByTab.main = []
      chat.branchViewByTab.main = { 1: 1 }
      chat.processingByTab.main = true
      chat._streamingBranchByTab.main = { turnIndex: 2, branchId: 3, parentBranchPath: [[1, 1]] }
      chat._onMessage({
        type: "activity",
        source: "main",
        activity_type: "processing_error",
        turn_index: 2,
        branch_id: 3,
        error: "unconfirmed",
        response_outcome,
      })
      expect(chat.messagesByTab.main[0]._streaming).toBe(false)
      expect(chat._streamingBranchByTab.main?.parentBranchPath).toEqual([[1, 1]])
      chat.branchViewByTab.main = { 1: 2 }
      const before = JSON.stringify(chat.messagesByTab.main)
      chat._onMessage({
        type: "activity",
        source: "main",
        activity_type,
        turn_index: 2,
        branch_id: 3,
        name: "worker",
        job_id: "job-1",
        result: "wrong branch result",
      })
      expect(JSON.stringify(chat.messagesByTab.main)).toBe(before)
      chat._onMessage({ type: "processing_end", source: "main" })
      expect(chat._streamingBranchByTab.main).toBeUndefined()
    },
  )
  it.each(["processing_end", "idle"])(
    "retires the running target on %s even if error was off-branch",
    (endType) => {
      const chat = useChatStore()
      chat.activeTab = "main"
      chat.messagesByTab.main = [{ role: "assistant", parts: [] }]
      chat.eventsByTab.main = []
      chat.branchViewByTab.main = { 1: 2 }
      chat.processingByTab.main = true
      chat._streamingBranchByTab.main = { turnIndex: 2, branchId: 3, parentBranchPath: [[1, 1]] }
      const before = JSON.stringify(chat.messagesByTab.main)
      chat._onMessage({
        type: "activity",
        source: "main",
        activity_type: "processing_error",
        turn_index: 2,
        branch_id: 3,
        error: "off branch",
      })
      expect(JSON.stringify(chat.messagesByTab.main)).toBe(before)
      expect(chat.processingByTab.main).toBe(true)
      expect(chat._streamingBranchByTab.main).toBeDefined()
      chat._onMessage({ type: endType, source: "main" })
      expect(chat.processingByTab.main).toBe(false)
      expect(chat._streamingBranchByTab.main).toBeUndefined()
    },
  )

  it("preserves pending ancestry if the error precedes branch target promotion", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.messagesByTab.main = [{ role: "assistant", parts: [] }]
    chat.processingByTab.main = true
    chat._pendingBranchPathByTab.main = [[1, 1]]
    chat._onMessage({
      type: "activity",
      source: "main",
      activity_type: "processing_error",
      error: "unconfirmed",
    })
    expect(chat._pendingBranchPathByTab.main).toEqual([[1, 1]])
    chat._onMessage({ type: "processing_end", source: "main" })
    expect(chat._pendingBranchPathByTab.main).toBeUndefined()
  })

  it("keeps partial text and outcome metadata in live and replay views", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.messagesByTab.main = []
    chat._onMessage({ type: "processing_start", source: "main" })
    chat._onMessage({ type: "text", source: "main", content: "Partial answer" })
    chat._onMessage({
      type: "activity",
      activity_type: "processing_error",
      source: "main",
      error_type: "ResponseStreamError",
      error: "Completion unconfirmed",
      response_outcome,
    })
    expect(chat.messagesByTab.main.find((message) => message.role === "assistant").parts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ type: "text", content: "Partial answer" }),
      ]),
    )
    expect(
      chat.messagesByTab.main.find((message) => message.role === "error").responseOutcome,
    ).toEqual(response_outcome)
    expect(chat.processingByTab.main).toBe(true)
    const partial = chat.messagesByTab.main.find((message) => message.role === "assistant")
    expect(partial._streaming).toBe(false)
    expect(partial.parts[0]._streaming).toBe(false)

    for (const event of [
      { type: "processing_error" },
      { type: "activity", activity_type: "processing_error" },
    ]) {
      const history = _replayEvents(
        [],
        [
          {
            type: "text_chunk",
            content: "Partial answer",
            event_id: 1,
            turn_index: 1,
            branch_id: 1,
          },
          {
            ...event,
            error_type: "ResponseStreamError",
            error: "Completion unconfirmed",
            response_outcome,
            event_id: 2,
            turn_index: 1,
            branch_id: 1,
          },
        ],
      )
      expect(history.messages.find((message) => message.role === "error").responseOutcome).toEqual(
        response_outcome,
      )
      expect(JSON.stringify(history)).toContain("Partial answer")
    }
  })
})
