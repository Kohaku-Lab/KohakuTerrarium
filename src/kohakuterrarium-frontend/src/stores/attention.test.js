import { beforeEach, describe, expect, it } from "vitest"

import {
  attentionForScope,
  clearAttentionRegistry,
  createAttentionState,
  markAttentionRead,
  publishAttention,
  reduceAttention,
  removeAttentionScope,
  totalAttention,
} from "./attention"

describe("chat attention", () => {
  beforeEach(() => clearAttentionRegistry())

  it("counts an interactive event once and keeps it pending until accepted", () => {
    let state = createAttentionState()
    state = reduceAttention(state, { type: "ask_text", event_id: "x", interactive: true })
    state = reduceAttention(state, { type: "ask_text", event_id: "x", interactive: true })
    expect(state.pending.size).toBe(1)

    state = reduceAttention(state, { type: "ui_reply_ack", event_id: "x", status: "unknown" })
    expect(state.pending.size).toBe(1)
    state = reduceAttention(state, { type: "ui_reply_ack", event_id: "x", status: "accepted" })
    expect(state.pending.size).toBe(0)
  })

  it("creates one ordinary completion on the processing edge, not end plus idle", () => {
    let state = createAttentionState()
    state = reduceAttention(state, { type: "processing_start" })
    state = reduceAttention(state, { type: "processing_end" })
    state = reduceAttention(state, { type: "idle" })
    expect(state.completed).toBe(1)
  })

  it("marks completion read without clearing pending input", () => {
    let state = createAttentionState()
    state = reduceAttention(state, { type: "processing_start" })
    state = reduceAttention(state, { type: "processing_end" })
    state = reduceAttention(state, { type: "confirm", event_id: "approve", interactive: true })
    const read = markAttentionRead(state)
    expect(read.completed).toBe(0)
    expect(read.pending).toEqual(new Set(["approve"]))
  })

  it("aggregates by stable scope and removes disposed scopes", () => {
    publishAttention("graph-a", "root", { ...createAttentionState(), completed: 2 })
    publishAttention("graph-a", "reviewer", {
      ...createAttentionState(),
      pending: new Set(["ask"]),
    })
    publishAttention("graph-b", "root", { ...createAttentionState(), completed: 1 })

    expect(attentionForScope("graph-a")).toEqual({ pending: 1, completed: 2 })
    expect(totalAttention()).toEqual({ pending: 1, completed: 3 })

    removeAttentionScope("graph-a")
    expect(totalAttention()).toEqual({ pending: 0, completed: 1 })
  })
})
