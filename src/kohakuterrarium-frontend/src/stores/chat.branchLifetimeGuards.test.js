import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { _replayEvents, useChatStore } from "./chat.js"

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
})

function originalEvents() {
  return [
    { type: "user_input", event_id: 1, content: "A", turn_index: 1, branch_id: 1 },
    { type: "user_message", event_id: 2, content: "A", turn_index: 1, branch_id: 1 },
    { type: "processing_start", event_id: 3, turn_index: 1, branch_id: 1 },
    { type: "text_chunk", event_id: 4, content: "old-to-A", turn_index: 1, branch_id: 1 },
    { type: "processing_end", event_id: 5, turn_index: 1, branch_id: 1 },
  ]
}

/** Replay-based message list with user locator metadata for turn 1. */
function userMessages() {
  const events = originalEvents()
  const { messages } = _replayEvents([], events)
  for (const m of messages) {
    if (m.role === "user") {
      m.turnIndex = 1
      m.latestBranch = 1
    }
  }
  return { events, messages }
}

function seedInstance(chat, tab = "main") {
  chat._instanceId = "graph_1"
  chat._instanceGraphId = "graph_1"
  chat._instanceGeneration = 1
  chat.activeTab = tab
  chat.tabs = [tab]
  const { events, messages } = userMessages()
  chat.eventsByTab = { [tab]: events }
  chat.messagesByTab = { [tab]: messages }
  chat.branchViewByTab = {}
}

describe("chat store — branch op owner/lifetime guards", () => {
  it("does not POST to a new instance when reset lands during the awaited import", async () => {
    const chat = useChatStore()
    seedInstance(chat)
    const { agentAPI } = await import("@/utils/api")
    const editSpy = vi.spyOn(agentAPI, "editMessage").mockResolvedValue({})

    // Start the op, then invalidate the instance before the dynamic import
    // resolves — the POST must never be dispatched at the new target.
    const pending = chat.editMessage(0, "edited", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })
    chat.resetForRouteSwitch()
    const result = await pending

    expect(editSpy).not.toHaveBeenCalled()
    expect(result).toMatchObject({ ok: false, superseded: true })
    chat._clearBranchResyncTimers()
  })

  it("does not POST to a new instance when reset races the awaited regenerate import", async () => {
    const chat = useChatStore()
    seedInstance(chat)
    const { agentAPI } = await import("@/utils/api")
    const regenSpy = vi.spyOn(agentAPI, "regenerate").mockResolvedValue({})

    const pending = chat.regenerateLastResponse({ turnIndex: 1 })
    chat.resetForRouteSwitch()
    const result = await pending

    expect(regenSpy).not.toHaveBeenCalled()
    expect(result).toMatchObject({ ok: false, superseded: true })
    chat._clearBranchResyncTimers()
  })

  it("ignores an old edit rejection after reset+new instance and never resyncs the new target", async () => {
    const chat = useChatStore()
    seedInstance(chat)
    const { agentAPI, terrariumAPI } = await import("@/utils/api")
    let rejectEdit
    const editSpy = vi
      .spyOn(agentAPI, "editMessage")
      .mockImplementation(() => new Promise((_resolve, reject) => (rejectEdit = reject)))

    const pending = chat.editMessage(0, "edited", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })
    await vi.waitFor(() => expect(editSpy).toHaveBeenCalledTimes(1))

    // New instance takes over the same tab name.
    chat.resetForRouteSwitch()
    chat._instanceId = "graph_2"
    chat._instanceGraphId = "graph_2"
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [{ id: "fresh", role: "user", content: "fresh" }] }
    chat.eventsByTab = { main: [] }
    chat.branchViewByTab = { main: {} }
    const getHistorySpy = vi.spyOn(terrariumAPI, "getHistory").mockResolvedValue({ events: [] })
    const resyncSpy = vi.spyOn(chat, "_resyncHistory").mockResolvedValue(true)

    rejectEdit(Object.assign(new Error("boom"), { code: "ECONNABORTED" }))
    const result = await pending

    expect(result).toMatchObject({ ok: false, superseded: true })
    expect(chat.messagesByTab.main).toEqual([{ id: "fresh", role: "user", content: "fresh" }])
    expect(resyncSpy).not.toHaveBeenCalled()
    expect(getHistorySpy).not.toHaveBeenCalled()
    chat._clearBranchResyncTimers()
  })

  it("late old op does not clear or roll back a newer op on the same tab", async () => {
    const chat = useChatStore()
    seedInstance(chat)
    chat._scheduleBranchResync = vi.fn()
    const { agentAPI } = await import("@/utils/api")
    let rejectA
    const editSpy = vi.spyOn(agentAPI, "editMessage")
    editSpy.mockImplementationOnce(() => new Promise((_resolve, reject) => (rejectA = reject)))
    editSpy.mockImplementationOnce(() => new Promise(() => {}))

    const pendingA = chat.editMessage(0, "A-edit", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })
    await vi.waitFor(() => expect(editSpy).toHaveBeenCalledTimes(1))
    // WS idle clears op A BEFORE its HTTP response (documented store behavior).
    chat._onMessage({ type: "idle", source: "main" })
    expect(chat.branchOperationByTab.main).toBeNull()

    chat.editMessage(0, "B-edit", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })
    await vi.waitFor(() => expect(editSpy).toHaveBeenCalledTimes(2))
    const operationB = chat.branchOperationByTab.main
    expect(operationB?.type).toBe("edit")

    // Old op A settles with a definite failure after B started.
    rejectA(Object.assign(new Error("conflict"), { response: { status: 409 } }))
    const resultA = await pendingA

    expect(resultA).toMatchObject({ ok: false, superseded: true })
    expect(chat.branchOperationByTab.main).toBe(operationB)
    // A must not have rolled back B's optimistic state.
    expect(JSON.stringify(chat.eventsByTab.main)).toContain("B-edit")
    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(chat.branchOperationErrorByTab.main).toBeFalsy()
    chat._clearBranchResyncTimers()
  })

  it("non-active explicit-tab op: WS idle before HTTP success still resyncs the right tab", async () => {
    const chat = useChatStore()
    seedInstance(chat)
    chat.tabs = ["main", "other"]
    chat.activeTab = "other"
    chat.messagesByTab.other = [{ id: "other-user", role: "user", content: "other draft" }]
    chat._scheduleBranchResync = vi.fn()
    const { agentAPI, terrariumAPI } = await import("@/utils/api")
    let resolveEdit
    const editSpy = vi
      .spyOn(agentAPI, "editMessage")
      .mockImplementation(() => new Promise((resolve) => (resolveEdit = resolve)))

    const pending = chat.editMessage(0, "main-edit", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })
    await vi.waitFor(() => expect(editSpy).toHaveBeenCalledTimes(1))
    // Turn ends over WS before the blocking POST returns.
    chat._onMessage({ type: "idle", source: "main" })
    expect(chat.branchOperationByTab.main).toBeNull()

    const canonical = [
      ...originalEvents(),
      { type: "user_input", event_id: 6, content: "main-edit", turn_index: 1, branch_id: 2 },
      { type: "user_message", event_id: 7, content: "main-edit", turn_index: 1, branch_id: 2 },
      { type: "text_chunk", event_id: 8, content: "new-branch", turn_index: 1, branch_id: 2 },
    ]
    const getHistorySpy = vi
      .spyOn(terrariumAPI, "getHistory")
      .mockResolvedValue({ events: canonical })

    resolveEdit({ branch_id: 2, turn_index: 1 })
    const result = await pending

    expect(result.ok).toBe(true)
    expect(getHistorySpy).toHaveBeenCalledWith("graph_1", "main")
    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(chat.activeTab).toBe("other")
    expect(chat.messagesByTab.other[0].content).toBe("other draft")
    chat._clearBranchResyncTimers()
  })

  it("non-active explicit-tab error applies only to the target tab", async () => {
    const chat = useChatStore()
    seedInstance(chat)
    chat.tabs = ["main", "other"]
    chat.activeTab = "other"
    chat.messagesByTab.other = [{ id: "other-user", role: "user", content: "other draft" }]
    chat._scheduleBranchResync = vi.fn()
    const { agentAPI } = await import("@/utils/api")
    let rejectEdit
    const editSpy = vi
      .spyOn(agentAPI, "editMessage")
      .mockImplementation(() => new Promise((_resolve, reject) => (rejectEdit = reject)))

    const pending = chat.editMessage(0, "main-edit", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })
    await vi.waitFor(() => expect(editSpy).toHaveBeenCalledTimes(1))
    rejectEdit(Object.assign(new Error("edit conflict"), { response: { status: 409 } }))
    const result = await pending

    expect(result.ok).toBe(false)
    expect(chat.branchOperationErrorByTab.main).toBe("edit conflict")
    expect(chat.branchOperationErrorByTab.other).toBeUndefined()
    expect(chat.messagesByTab.other[0].content).toBe("other draft")
    chat._clearBranchResyncTimers()
  })

  it("close+reopen of the same tab name supersedes the old op without touching other tabs", async () => {
    const chat = useChatStore()
    seedInstance(chat)
    chat.tabs = ["main", "other"]
    chat.activeTab = "other"
    chat.messagesByTab.other = [{ id: "other-user", role: "user", content: "other draft" }]
    chat._scheduleBranchResync = vi.fn()
    const otherMessages = chat.messagesByTab.other
    const { agentAPI } = await import("@/utils/api")
    let rejectEdit
    const editSpy = vi
      .spyOn(agentAPI, "editMessage")
      .mockImplementation(() => new Promise((_resolve, reject) => (rejectEdit = reject)))

    const pending = chat.editMessage(0, "main-edit", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })
    await vi.waitFor(() => expect(editSpy).toHaveBeenCalledTimes(1))

    chat.closeTab("main")
    expect(chat._branchRequestIdByTab.main).toBeUndefined()
    expect(chat._branchRequestIdByTab.other).toBeUndefined()
    expect(chat.branchOperationByTab.main).toBeUndefined()

    // Reopen the same tab name with fresh state.
    chat.tabs = ["other", "main"]
    chat.messagesByTab.main = [{ id: "reopened", role: "user", content: "fresh" }]

    rejectEdit(Object.assign(new Error("stale"), { response: { status: 409 } }))
    const result = await pending

    expect(result).toMatchObject({ ok: false, superseded: true })
    expect(chat.messagesByTab.main).toEqual([{ id: "reopened", role: "user", content: "fresh" }])
    expect(chat.messagesByTab.other).toBe(otherMessages)
    expect(chat._branchRequestIdByTab.other).toBeUndefined()
    chat._clearBranchResyncTimers()
  })
})

describe("chat store — branch error classification (mayHaveRun)", () => {
  it("classifies 409 as definite, 502/504 as uncertain, and honours mayHaveRun", () => {
    const chat = useChatStore()
    chat.wsStatus = "closed"
    // 409 wins over a generic mayHaveRun=true hint.
    expect(chat._requestMayStillBeRunning({ response: { status: 409 }, mayHaveRun: true })).toBe(
      false,
    )
    // Gateway errors stay uncertain regardless of mayHaveRun=false.
    expect(chat._requestMayStillBeRunning({ response: { status: 502 }, mayHaveRun: false })).toBe(
      true,
    )
    expect(chat._requestMayStillBeRunning({ response: { status: 504 } })).toBe(true)
    // Lost response after dispatch: uncertain even with a closed WS.
    expect(chat._requestMayStillBeRunning({ mayHaveRun: true })).toBe(true)
    // Explicit pre-admission rejection: definite even with an open WS.
    chat.wsStatus = "open"
    expect(chat._requestMayStillBeRunning({ mayHaveRun: false })).toBe(false)
    // Absent phase falls back to the dashboard heuristics.
    expect(chat._requestMayStillBeRunning({ code: "ECONNABORTED" })).toBe(true)
    expect(chat._requestMayStillBeRunning({ code: "ETIMEDOUT" })).toBe(true)
    expect(chat._requestMayStillBeRunning({})).toBe(true)
    expect(chat._requestMayStillBeRunning({ response: { status: 500 } })).toBe(false)
  })

  it("409 with mayHaveRun=true restores only the current optimism", async () => {
    const chat = useChatStore()
    seedInstance(chat)
    const expected = JSON.parse(JSON.stringify(chat.messagesByTab.main))
    const expectedEvents = JSON.parse(JSON.stringify(chat.eventsByTab.main))
    const { agentAPI } = await import("@/utils/api")
    vi.spyOn(agentAPI, "editMessage").mockRejectedValue(
      Object.assign(new Error("conflict"), { response: { status: 409 }, mayHaveRun: true }),
    )

    const result = await chat.editMessage(0, "edited", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(result.ok).toBe(false)
    expect(result.superseded).toBeFalsy()
    expect(chat.messagesByTab.main).toEqual(expected)
    expect(chat.eventsByTab.main).toEqual(expectedEvents)
    chat._clearBranchResyncTimers()
  })

  it("502 keeps the optimism even when mayHaveRun is false", async () => {
    const chat = useChatStore()
    seedInstance(chat)
    chat._scheduleBranchResync = vi.fn()
    const { agentAPI } = await import("@/utils/api")
    vi.spyOn(agentAPI, "editMessage").mockRejectedValue(
      Object.assign(new Error("gateway"), { response: { status: 502 }, mayHaveRun: false }),
    )

    const result = await chat.editMessage(0, "edited", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(result.ok).toBe(true)
    expect(chat.branchViewByTab.main[1]).toBe(2)
    expect(JSON.stringify(chat.messagesByTab.main)).not.toContain("old-to-A")
    chat._clearBranchResyncTimers()
  })

  it("a pre-dispatch supersession restores optimism and yields the dead-view result", async () => {
    const chat = useChatStore()
    seedInstance(chat)
    const expectedMessages = JSON.parse(JSON.stringify(chat.messagesByTab.main))
    chat._scheduleBranchResync = vi.fn()
    const { agentAPI } = await import("@/utils/api")
    vi.spyOn(agentAPI, "editMessage").mockRejectedValue(
      Object.assign(new Error("superseded elsewhere"), { superseded: true, mayHaveRun: false }),
    )

    const result = await chat.editMessage(0, "edited", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    expect(result).toMatchObject({ ok: false, superseded: true })
    expect(result.error).toBeNull()
    expect(chat.messagesByTab.main).toEqual(expectedMessages)
    expect(chat.processingByTab.main).toBe(false)
    chat._clearBranchResyncTimers()
  })
})
