import { beforeEach, describe, expect, it } from "vitest"

import {
  attentionForScope,
  clearAttentionRegistry,
  createAttentionState,
  markAttentionRead,
  publishAttention,
  reduceAttention,
  reduceAttentionEdge,
  removeAttentionScope,
  subscribeAttentionEdges,
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

  it("rebuilds pending input from history without emitting an external edge", () => {
    const edges = []
    const unsubscribe = subscribeAttentionEdges((edge) => edges.push(edge))
    let state = createAttentionState()

    ;({ state } = reduceAttentionEdge(
      state,
      { type: "confirm", event_id: "ask", interactive: true, history: true },
      { scope: "graph-a", tab: "reviewer" },
    ))
    ;({ state } = reduceAttentionEdge(
      state,
      { type: "processing_start", history: true },
      { scope: "graph-a", tab: "reviewer" },
    ))
    ;({ state } = reduceAttentionEdge(
      state,
      { type: "processing_end", history: true },
      { scope: "graph-a", tab: "reviewer" },
    ))
    ;({ state } = reduceAttentionEdge(
      state,
      { type: "confirm", event_id: "resolved", interactive: true, history: true },
      { scope: "graph-a", tab: "reviewer" },
    ))
    ;({ state } = reduceAttentionEdge(
      state,
      { type: "ui_reply_ack", event_id: "resolved", status: "accepted", history: true },
      { scope: "graph-a", tab: "reviewer" },
    ))
    unsubscribe()

    expect(state.pending).toEqual(new Set(["ask"]))
    expect(state.completed).toBe(0)
    expect(edges).toEqual([])
  })

  it("creates one ordinary completion on the processing edge, not end plus idle", () => {
    let state = createAttentionState()
    state = reduceAttention(state, { type: "processing_start" })
    state = reduceAttention(state, { type: "processing_end" })
    state = reduceAttention(state, { type: "idle" })
    expect(state.completed).toBe(1)
  })

  it("does not count a turn that ended to wait for background jobs", () => {
    const edges = []
    const unsubscribe = subscribeAttentionEdges((edge) => edges.push(edge))
    let state = createAttentionState()

    state = reduceAttention(state, { type: "processing_start" })
    state = reduceAttention(state, { type: "processing_end", awaiting_background: true })
    state = reduceAttention(state, { type: "idle" })
    unsubscribe()

    expect(state.completed).toBe(0)
    expect(state.processing).toBe(false)
    expect(edges).toEqual([])
  })

  it("counts the genuine completion after the background wait ends", () => {
    let state = createAttentionState()
    state = reduceAttention(state, { type: "processing_start" })
    state = reduceAttention(state, { type: "processing_end", awaiting_background: true })
    state = reduceAttention(state, { type: "processing_start" })
    state = reduceAttention(state, { type: "processing_end" })
    expect(state.completed).toBe(1)
  })

  it("forwards the edge summary to reminder effects", () => {
    const edges = []
    const unsubscribe = subscribeAttentionEdges((edge) => edges.push(edge))
    let state = createAttentionState()

    ;({ state } = reduceAttentionEdge(
      state,
      { type: "ask_text", event_id: "ask", interactive: true, payload: { prompt: "Proceed?" } },
      { scope: "graph-a", tab: "reviewer", summary: "Proceed?" },
    ))
    unsubscribe()

    expect(edges).toEqual([
      {
        scope: "graph-a",
        tab: "reviewer",
        kind: "waiting-input",
        eventId: "ask",
        summary: "Proceed?",
      },
    ])
  })

  it("emits one target-keyed edge for genuine real-time attention", () => {
    const edges = []
    const unsubscribe = subscribeAttentionEdges((edge) => edges.push(edge))
    let state = createAttentionState()

    ;({ state } = reduceAttentionEdge(
      state,
      { type: "processing_start" },
      { scope: "graph-a", tab: "reviewer" },
    ))
    ;({ state } = reduceAttentionEdge(
      state,
      { type: "processing_end" },
      { scope: "graph-a", tab: "reviewer" },
    ))
    ;({ state } = reduceAttentionEdge(
      state,
      { type: "idle" },
      { scope: "graph-a", tab: "reviewer" },
    ))
    ;({ state } = reduceAttentionEdge(
      state,
      { type: "confirm", event_id: "approve", interactive: true },
      { scope: "graph-a", tab: "reviewer" },
    ))
    reduceAttentionEdge(
      state,
      { type: "confirm", event_id: "approve", interactive: true },
      { scope: "graph-a", tab: "reviewer" },
    )
    reduceAttentionEdge(
      state,
      { type: "processing_end", replay: true },
      { scope: "graph-a", tab: "reviewer" },
    )
    unsubscribe()

    expect(edges).toEqual([
      { scope: "graph-a", tab: "reviewer", kind: "completed" },
      { scope: "graph-a", tab: "reviewer", kind: "waiting-input", eventId: "approve" },
    ])
  })

  it("isolates edge listener failures from attention state reduction", () => {
    const unsubscribe = subscribeAttentionEdges(() => {
      throw new Error("notification unavailable")
    })
    let state = createAttentionState()
    ;({ state } = reduceAttentionEdge(
      state,
      { type: "confirm", event_id: "ask", interactive: true },
      { scope: "graph-a", tab: "reviewer" },
    ))
    unsubscribe()

    expect(state.pending).toEqual(new Set(["ask"]))
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
