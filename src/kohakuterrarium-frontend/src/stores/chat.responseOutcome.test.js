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
    expect(chat.processingByTab.main).toBe(false)
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
