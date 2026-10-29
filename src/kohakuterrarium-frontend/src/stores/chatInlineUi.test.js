import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { useChatStore } from "./chat.js"
import {
  INLINE_UI_STATE_DEBOUNCE_MS,
  inlineUiKey,
  inlineUiStatesFromEvents,
} from "./chatInlineUi.js"

function store() {
  const chat = useChatStore()
  const sent = []
  chat._ws = { readyState: WebSocket.OPEN, send: (raw) => sent.push(JSON.parse(raw)) }
  return { chat, sent, frames: () => sent.filter((f) => f.type === "inline_ui_state") }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe("inline UI state in the chat store", () => {
  it("merges edits now and sends one debounced frame per block", () => {
    const { chat, frames } = store()
    chat.setInlineUiState("main", 2, 1, "0a1b2c3d.0", { people: 4 })
    chat.setInlineUiState("main", 2, 1, "0a1b2c3d.0", { note: "x" })
    chat.setInlineUiState("main", 2, 1, "ffffffff.0", { n: 1 })
    expect(chat.inlineUiStateOf("main", 2, 1, "0a1b2c3d.0")).toEqual({ people: 4, note: "x" })
    vi.advanceTimersByTime(INLINE_UI_STATE_DEBOUNCE_MS - 1)
    expect(frames()).toHaveLength(0)
    vi.advanceTimersByTime(1)
    expect(frames()).toEqual([
      expect.objectContaining({
        target: "main",
        ui_turn: 2,
        ui_branch: 1,
        block: "0a1b2c3d.0",
        state: { people: 4, note: "x" },
      }),
      expect.objectContaining({ block: "ffffffff.0", state: { n: 1 } }),
    ])
  })

  it("ignores edits without a full address", () => {
    const { chat, frames } = store()
    chat.setInlineUiState("main", undefined, 1, "0a1b2c3d.0", { a: 1 })
    chat.setInlineUiState("main", 1, 1, "", { a: 1 })
    chat.setInlineUiState("", 1, 1, "b.0", { a: 1 })
    vi.runAllTimers()
    expect(frames()).toHaveLength(0)
    expect(chat.inlineUiStateOf("main", undefined, 1, "0a1b2c3d.0")).toBeNull()
  })

  it("held edits of an unaddressed block are claimed once it has a turn", () => {
    const { chat, frames } = store()
    chat.holdInlineUiState("main", "0a1b2c3d.0", { people: 2 })
    chat.holdInlineUiState("main", "0a1b2c3d.0", { people: 5 })
    chat.claimInlineUiState("main", 3, 1, "ffffffff.0")
    chat.claimInlineUiState("main", 3, 1, "0a1b2c3d.0")
    vi.runAllTimers()
    expect(frames()).toEqual([
      expect.objectContaining({
        ui_turn: 3,
        ui_branch: 1,
        block: "0a1b2c3d.0",
        state: { people: 5 },
      }),
    ])
    chat.claimInlineUiState("main", 3, 1, "0a1b2c3d.0")
    vi.runAllTimers()
    expect(frames()).toHaveLength(1)
  })

  it("adopts recorded state from history, latest event wins", () => {
    const { chat } = store()
    const events = [
      { type: "inline_ui_state", ui_turn: 1, ui_branch: 1, block: "0a1b2c3d.0", state: { n: 1 } },
      { type: "text_chunk", content: "x" },
      { type: "inline_ui_state", ui_turn: 1, ui_branch: 1, block: "0a1b2c3d.0", state: { n: 7 } },
      { type: "inline_ui_state", ui_turn: "1", ui_branch: 1, block: "bad.0", state: { n: 9 } },
    ]
    expect(inlineUiStatesFromEvents(events)).toEqual({
      [inlineUiKey(1, 1, "0a1b2c3d.0")]: { n: 7 },
    })
    chat._adoptInlineUiStates("main", events)
    expect(chat.inlineUiStateOf("main", 1, 1, "0a1b2c3d.0")).toEqual({ n: 7 })
  })

  it("a history page load adopts the recorded state", () => {
    const { chat } = store()
    const records = [
      { type: "user_message", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
      {
        type: "text_chunk",
        content: "<ui><state n={1}/></ui>",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      { type: "processing_end", event_id: 3, turn_index: 1, branch_id: 1 },
      {
        type: "inline_ui_state",
        ui_turn: 1,
        ui_branch: 1,
        block: "0a1b2c3d.0",
        state: { n: 3 },
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
    ]
    const controller = { getState: () => ({ stream: "events" }) }
    chat._applyHistoryPageRecords("main", controller, records, { payload: {} })
    expect(chat.inlineUiStateOf("main", 1, 1, "0a1b2c3d.0")).toEqual({ n: 3 })
  })

  it("applies another client's frame and resets with the instance", () => {
    const { chat } = store()
    chat._handleInlineUiState("main", {
      ui_turn: 4,
      ui_branch: 2,
      block: "0a1b2c3d.1",
      state: { pick: "b" },
    })
    chat._handleInlineUiState("main", { ui_turn: 4, ui_branch: 2, block: "0a1b2c3d.1", state: "x" })
    chat._handleInlineUiState(null, {
      ui_turn: 4,
      ui_branch: 2,
      block: "0a1b2c3d.1",
      state: { pick: "c" },
    })
    expect(chat.inlineUiStateOf("main", 4, 2, "0a1b2c3d.1")).toEqual({ pick: "b" })
    expect(chat.inlineUiStateOf("main", 4, 1, "0a1b2c3d.1")).toBeNull()
  })
})
