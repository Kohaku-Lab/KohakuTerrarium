import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, expect, it } from "vitest"
import { useChatStore } from "./chat"

let chat
const frame = (overrides = {}) => ({
  type: "model_recovery",
  source: "worker",
  request_id: "r1",
  request_started_at: 10,
  sequence: 1,
  phase: "waiting",
  turn_index: 4,
  branch_id: 2,
  ...overrides,
})
beforeEach(() => {
  setActivePinia(createPinia())
  chat = useChatStore()
})
afterEach(() => chat._cleanup())

it("keeps recovery outside history, isolates creatures and follows the viewed branch", () => {
  chat._onMessage(frame())
  expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
  chat._onMessage({
    type: "activity",
    activity_type: "session_info",
    source: "worker",
    ts: 9,
    model_recovery: null,
  })
  expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
  expect(chat.modelRecoveryForTab("other")).toBeNull()
  expect(chat.messagesByTab.worker || []).toEqual([])
  chat.branchViewByTab.worker = { 4: 1 }
  expect(chat.modelRecoveryForTab("worker")).toBeNull()
  chat.branchViewByTab.worker = { 4: 2 }
  expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
})

it("rejects stale frames from old attempts, requests and completed turns", () => {
  chat._onMessage(frame({ sequence: 2, phase: null }))
  chat._onMessage(frame())
  expect(chat.modelRecoveryForTab("worker")).toBeNull()
  chat._onMessage(frame({ request_id: "r2", request_started_at: 11, phase: "reconnecting" }))
  chat._onMessage(frame({ sequence: 99 }))
  expect(chat.modelRecoveryForTab("worker")).toBe("reconnecting")
  chat._onMessage({ type: "processing_end", source: "worker", turn_index: 4, branch_id: 2 })
  chat._onMessage(frame({ request_id: "r2", request_started_at: 11, sequence: 3 }))
  expect(chat.modelRecoveryForTab("worker")).toBeNull()
  chat._onMessage(frame({ request_id: "r3", request_started_at: 12, turn_index: 5 }))
  chat._onMessage({ type: "idle", source: "worker", turn_index: 4, branch_id: 2 })
  expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
})

it("restores a snapshot on attach and does not regress to older queued events", () => {
  chat._onMessage({
    type: "activity",
    activity_type: "session_info",
    source: "worker",
    model_recovery: frame({ sequence: 3 }),
  })
  expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
  chat._onMessage(frame({ sequence: 2, phase: null }))
  expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
  chat._onMessage(frame({ sequence: 4, phase: null }))
  expect(chat.modelRecoveryForTab("worker")).toBeNull()
  chat._onMessage(frame({ sequence: 5 }))
  chat._cleanup()
  expect(chat.modelRecoveryForTab("worker")).toBeNull()
})

it("clears on errors without producing status messages", () => {
  chat._onMessage(frame())
  chat._onMessage({ type: "error", source: "worker", content: "Interrupted" })
  expect(chat.modelRecoveryForTab("worker")).toBeNull()
  expect(chat.messagesByTab.worker.map((m) => m.role)).toEqual(["error"])
})

it("rejects an unseen request queued before its turn ended", () => {
  chat._onMessage(frame())
  chat._onMessage({ type: "processing_end", source: "worker", turn_index: 4, branch_id: 2, ts: 12 })
  chat._onMessage(frame({ request_id: "late", request_started_at: 11 }))
  expect(chat.modelRecoveryForTab("worker")).toBeNull()
})

it("accepts a newer request after editing an earlier turn", () => {
  chat._onMessage({
    type: "processing_end",
    source: "worker",
    turn_index: 4,
    branch_id: 0,
    ts: 100,
  })
  chat._onMessage({
    type: "processing_start",
    source: "worker",
    turn_index: 1,
    branch_id: 2,
    ts: 101,
  })
  const rerun = frame({ request_id: "rerun", request_started_at: 102, turn_index: 1 })
  chat._onMessage(rerun)
  expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
  chat._onMessage(frame({ request_id: "old", request_started_at: 99, sequence: 99 }))
  chat._onMessage({ type: "idle", source: "worker", turn_index: 4, branch_id: 0, ts: 100 })
  expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
  chat._onMessage({
    type: "processing_end",
    source: "worker",
    turn_index: 1,
    branch_id: 2,
    ts: 103,
  })
  chat._onMessage({ ...rerun, sequence: 2 })
  expect(chat.modelRecoveryForTab("worker")).toBeNull()
  chat._onMessage(
    frame({ request_id: "follow-up", request_started_at: 104, turn_index: 2, branch_id: 0 }),
  )
  expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
})

it.each(["processing_start", "processing_end", "idle", "error"])(
  "keeps an attach snapshot when an older %s arrives on the same branch",
  (type) => {
    const current = frame({ request_id: "current", request_started_at: 102, sequence: 2 })
    chat._onMessage({
      type: "activity",
      activity_type: "session_info",
      source: "worker",
      model_recovery: current,
    })
    chat._onMessage({ type, source: "worker", turn_index: 4, branch_id: 2, ts: 101 })
    expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
    chat._onMessage({ ...current, sequence: 3, phase: "reconnecting" })
    expect(chat.modelRecoveryForTab("worker")).toBe("reconnecting")
    chat._onMessage({
      type: "processing_end",
      source: "worker",
      turn_index: 4,
      branch_id: 2,
      ts: 103,
    })
    expect(chat.modelRecoveryForTab("worker")).toBeNull()
  },
)

it("seals the old request when a newer batch starts on the same branch", () => {
  chat._onMessage(frame())
  chat._onMessage({
    type: "processing_start",
    source: "worker",
    turn_index: 4,
    branch_id: 2,
    ts: 11,
  })
  expect(chat.modelRecoveryForTab("worker")).toBeNull()
  chat._onMessage(frame({ sequence: 99 }))
  expect(chat.modelRecoveryForTab("worker")).toBeNull()
  chat._onMessage(frame({ request_id: "next", request_started_at: 12 }))
  expect(chat.modelRecoveryForTab("worker")).toBe("waiting")
})
