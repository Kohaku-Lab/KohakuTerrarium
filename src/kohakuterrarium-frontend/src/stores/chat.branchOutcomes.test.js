import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { agentAPI, terrariumAPI } from "@/utils/api"

import { _replayEvents, useChatStore } from "./chat.js"

const stores = []
beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
})
afterEach(() => {
  for (const chat of stores.splice(0)) chat._clearBranchResyncTimers()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

function events(content = "original", branch = 1) {
  const base = branch * 10
  return [
    { type: "user_input", event_id: base + 1, content, turn_index: 1, branch_id: branch },
    { type: "user_message", event_id: base + 2, content, turn_index: 1, branch_id: branch },
    { type: "processing_start", event_id: base + 3, turn_index: 1, branch_id: branch },
    {
      type: "text_chunk",
      event_id: base + 4,
      content: `${content}-answer`,
      turn_index: 1,
      branch_id: branch,
    },
    { type: "processing_end", event_id: base + 5, turn_index: 1, branch_id: branch },
  ]
}

function seed() {
  const chat = useChatStore()
  chat._instanceId = "g1"
  chat._instanceGraphId = "g1"
  chat._instanceGeneration = 1
  chat.tabs = ["main"]
  chat.activeTab = "main"
  chat.eventsByTab.main = events()
  chat.messagesByTab.main = _replayEvents([], events()).messages
  stores.push(chat)
  return chat
}

function invoke(chat, kind, text = "edit") {
  return kind === "editMessage"
    ? chat.editMessage(0, text, { turnIndex: 1, latestBranch: 1, userPosition: 0, tabId: "main" })
    : chat.regenerateLastResponse({ turnIndex: 1, tabId: "main" })
}

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

function snapshot(chat) {
  return JSON.parse(
    JSON.stringify({
      messages: chat.messagesByTab,
      events: chat.eventsByTab,
      branches: chat.branchViewByTab,
      errors: chat.branchOperationErrorByTab,
      processing: chat.processingByTab,
      operations: chat.branchOperationByTab,
      pending: chat._branchResyncPendingByTab,
    }),
  )
}

describe("branch outcome and resync ownership", () => {
  it("an exhausted old retry budget cannot discard a newer running edit", async () => {
    const chat = seed()
    const started = deferred()
    const current = deferred()
    vi.spyOn(agentAPI, "editMessage")
      .mockRejectedValueOnce(Object.assign(Error("response lost"), { mayHaveRun: true }))
      .mockImplementationOnce(() => {
        started.resolve()
        return current.promise
      })
    vi.spyOn(terrariumAPI, "getHistory").mockResolvedValue({
      events: events(),
      is_processing: true,
    })
    await invoke(chat, "editMessage", "older")
    chat._branchResyncPendingByTab.main.retries = 40
    chat._onMessage({ type: "idle", source: "main" })
    const pending = invoke(chat, "editMessage", "newer")
    await started.promise
    const before = snapshot(chat)
    try {
      await vi.advanceTimersByTimeAsync(350)
      expect(snapshot(chat)).toEqual(before)
      expect(chat.processingByTab.main).toBe(true)
    } finally {
      current.reject(Object.assign(Error("not started"), { mayHaveRun: false }))
      await pending
    }
  })

  it.each(["resolve", "reject"])(
    "a scheduled branch history %s after tab close cannot affect a reopened target",
    async (outcome) => {
      const chat = seed()
      const readback = deferred()
      const reading = deferred()
      vi.spyOn(agentAPI, "editMessage").mockRejectedValue(
        Object.assign(Error("response lost"), { mayHaveRun: true }),
      )
      vi.spyOn(terrariumAPI, "getHistory").mockImplementation(() => {
        reading.resolve()
        return readback.promise
      })
      await invoke(chat, "editMessage", "older")
      const tick = vi.advanceTimersByTimeAsync(350)
      await reading.promise
      chat.closeTab("main")
      chat.tabs = ["main"]
      chat.activeTab = "main"
      chat.messagesByTab.main = [{ role: "user", content: "reopened" }]
      chat._branchResyncPendingByTab.main = {
        active: true,
        retries: 0,
        expectedBranchByTurn: { 1: 7 },
      }
      const before = snapshot(chat)
      if (outcome === "resolve")
        readback.resolve({ events: [...events(), ...events("old-readback", 2)] })
      else readback.reject(Error("old read failed"))
      await tick
      expect(snapshot(chat)).toEqual(before)
      expect(Object.keys(chat._branchResyncTimers)).toEqual([])
    },
  )

  it.each([400, 401, 403, 404, 409, 422, 500, 503])(
    "HTTP %i remains a definite response after dispatch",
    (status) => {
      const chat = seed()
      chat.wsStatus = "open"
      expect(chat._requestMayStillBeRunning({ response: { status }, mayHaveRun: true })).toBe(false)
    },
  )

  for (const kind of ["editMessage", "regenerate"]) {
    it(`${kind}: superseded after dispatch retains the unresolved branch until history confirms it`, async () => {
      const chat = seed()
      vi.spyOn(agentAPI, kind).mockRejectedValue(
        Object.assign(Error("view changed"), { superseded: true, mayHaveRun: true }),
      )
      const result = await invoke(chat, kind)
      expect(chat.branchViewByTab.main[1]).toBe(2)
      expect(chat.eventsByTab.main.some((event) => event._optimistic)).toBe(true)
      expect(chat._branchResyncPendingByTab.main.active).toBe(true)
      expect(chat._branchResyncTimers.main).toBeDefined()
      expect(result).toMatchObject({ ok: true })
      expect(chat.branchOperationErrorByTab.main).toBeFalsy()
    })

    it(`${kind}: superseded before dispatch restores the view and releases its busy state`, async () => {
      const chat = seed()
      const previous = JSON.parse(JSON.stringify(chat.eventsByTab.main))
      vi.spyOn(agentAPI, kind).mockRejectedValue(
        Object.assign(Error("view changed"), { superseded: true, mayHaveRun: false }),
      )
      const result = await invoke(chat, kind)
      expect(chat.eventsByTab.main).toEqual(previous)
      expect(result).toMatchObject({ ok: false, superseded: true })
      expect(chat.branchOperationByTab.main).toBeFalsy()
      expect(chat._branchResyncPendingByTab.main).toBeUndefined()
    })

    it(`${kind}: an older response cannot restore state after a newer operation has finished`, async () => {
      const chat = seed()
      const old = deferred()
      const current = deferred()
      const oldStarted = deferred()
      const currentStarted = deferred()
      vi.spyOn(agentAPI, kind)
        .mockImplementationOnce(() => {
          oldStarted.resolve()
          return old.promise
        })
        .mockImplementationOnce(() => {
          currentStarted.resolve()
          return current.promise
        })
      const canonical = [...events(), ...events("newest", 3)]
      vi.spyOn(terrariumAPI, "getHistory").mockResolvedValue({
        events: canonical,
        is_processing: false,
      })
      const oldResult = invoke(chat, kind, "older")
      await oldStarted.promise
      chat._onMessage({ type: "idle", source: "main" })
      const currentResult = invoke(chat, kind, "newest")
      await currentStarted.promise
      chat._onMessage({ type: "idle", source: "main" })
      current.resolve({ status: "completed", turn_index: 1, branch_id: 3 })
      await currentResult
      expect(chat.branchViewByTab.main[1]).toBe(3)
      expect(JSON.stringify(chat.messagesByTab.main)).toContain("newest-answer")
      const expected = snapshot(chat)
      old.reject(
        Object.assign(Error("late conflict"), { response: { status: 409 }, mayHaveRun: true }),
      )
      expect(await oldResult).toMatchObject({ ok: false, superseded: true })
      expect(snapshot(chat)).toEqual(expected)
    })

    it(`${kind}: closing and reopening during readback discards the old canonical history`, async () => {
      const chat = seed()
      const readback = deferred()
      const reading = deferred()
      vi.spyOn(agentAPI, kind).mockResolvedValue({
        status: "completed",
        turn_index: 1,
        branch_id: 2,
      })
      vi.spyOn(terrariumAPI, "getHistory").mockImplementation(() => {
        reading.resolve()
        return readback.promise
      })
      const pending = invoke(chat, kind)
      await reading.promise
      chat.closeTab("main")
      expect(Object.keys(chat._branchRequestIdByTab)).toEqual([])
      chat.tabs = ["main"]
      chat.activeTab = "main"
      chat.messagesByTab.main = [{ role: "user", content: "reopened" }]
      const expected = snapshot(chat)
      readback.resolve({
        events: [...events(), ...events("old-readback", 2)],
        is_processing: false,
      })
      expect(await pending).toMatchObject({ ok: false, superseded: true })
      expect(snapshot(chat)).toEqual(expected)
      expect(Object.keys(chat._branchResyncTimers)).toEqual([])
    })

    it(`${kind}: a reset during completed-operation readback cannot schedule a retry in the new instance`, async () => {
      const chat = seed()
      const readback = deferred()
      const reading = deferred()
      vi.spyOn(agentAPI, kind).mockResolvedValue({
        status: "completed",
        turn_index: 1,
        branch_id: 2,
      })
      vi.spyOn(terrariumAPI, "getHistory").mockImplementation(() => {
        reading.resolve()
        return readback.promise
      })
      const pending = invoke(chat, kind)
      await reading.promise
      chat.resetForRouteSwitch()
      chat._instanceId = "g2"
      chat._instanceGraphId = "g2"
      chat.activeTab = "main"
      chat.tabs = ["main"]
      chat.messagesByTab.main = [{ role: "user", content: "new-instance" }]
      const expected = snapshot(chat)
      readback.reject(Error("old history read failed"))
      expect(await pending).toMatchObject({ ok: false, superseded: true })
      expect(snapshot(chat)).toEqual(expected)
      expect(Object.keys(chat._branchResyncTimers)).toEqual([])
    })
  }
})
