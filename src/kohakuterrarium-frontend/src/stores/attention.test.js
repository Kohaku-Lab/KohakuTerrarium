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
  restoreAttentionFromHistory,
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

  it("treats interactive legacy widgets and awaiting widgets as waiting input", () => {
    let state = createAttentionState()
    const confirm = {
      type: "widget",
      event_id: "w1",
      interactive: true,
      payload: { renderer: "kt.confirm" },
    }
    const progress = {
      type: "widget",
      event_id: "w2",
      interactive: true,
      payload: { renderer: "kt.progress" },
    }
    const app = {
      type: "widget",
      event_id: "w3",
      interactive: true,
      payload: { renderer: "kt.app", awaiting: true },
    }
    const idleApp = {
      type: "widget",
      event_id: "w4",
      interactive: true,
      payload: { renderer: "kt.app" },
    }
    const displayCard = {
      type: "widget",
      event_id: "w5",
      interactive: false,
      payload: { renderer: "kt.card" },
    }
    for (const event of [confirm, progress, app, idleApp, displayCard])
      state = reduceAttention(state, event)
    expect(state.pending).toEqual(new Set(["w1", "w3"]))

    const edge = reduceAttentionEdge(createAttentionState(), confirm, { scope: "s", tab: "t" }).edge
    expect(edge).toMatchObject({ kind: "waiting-input", eventId: "w1" })

    state = reduceAttention(state, {
      type: "widget_ack",
      event_id: "w1",
      kind: "message",
      status: "rate_limited",
    })
    state = reduceAttention(state, {
      type: "widget_ack",
      event_id: "w1",
      kind: "state",
      status: "accepted",
    })
    expect(state.pending.has("w1")).toBe(true)
    state = reduceAttention(state, {
      type: "widget_ack",
      event_id: "w1",
      kind: "message",
      status: "accepted",
    })
    state = reduceAttention(state, {
      type: "widget",
      event_id: "w3",
      update_target: "w3",
      payload: { awaiting: false },
    })
    expect(state.pending.size).toBe(0)
  })

  it("rebuilds widget prompts from history rows", () => {
    let state = createAttentionState()
    const rows = [
      { type: "widget", widget_id: "a", renderer: "kt.ask_text", interactive: true },
      { type: "widget", widget_id: "b", renderer: "kt.selection", interactive: true },
      { type: "widget_message", widget_id: "b", name: "submit" },
      { type: "widget", widget_id: "c", renderer: "kt.card", interactive: true },
      { type: "widget_close", widget_id: "c" },
    ]
    for (const [index, row] of rows.entries()) {
      state = reduceAttention(state, { ...row, event_id: index + 1, history: true })
    }
    expect(state.pending).toEqual(new Set(["a"]))
  })

  it.each([
    { type: "widget_ack", kind: "message", status: "accepted" },
    { type: "widget_close", payload: { reason: "replied" } },
    { type: "widget_close", payload: { reason: "timeout" } },
  ])("resolves a history-restored widget with a live $type frame", (close) => {
    let state = restoreAttentionFromHistory([
      {
        type: "widget",
        event_id: 42,
        widget_id: "ask",
        renderer: "kt.ask_text",
        interactive: true,
      },
    ])
    expect(state.pending).toEqual(new Set(["ask"]))
    state = reduceAttention(state, { ...close, event_id: "ask" })
    expect(state.pending.size).toBe(0)
  })

  it("uses a legacy payload prompt id instead of the history row id", () => {
    const state = restoreAttentionFromHistory([
      { type: "ask_text", event_id: 1, interactive: true, payload: { event_id: "ask" } },
      { type: "ui_supersede", event_id: 2, payload: { event_id: "ask" } },
    ])
    expect(state.pending.size).toBe(0)
    expect(state.seen).toEqual(new Set(["ask"]))
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
