import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

import { useChatStore } from "./chat.js"

describe("chat branch operation state", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it("reconciles a predicted branch collision from processing_start atomically", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.eventsByTab = {
      main: [
        {
          type: "processing_start",
          turn_index: 1,
          branch_id: 2,
          _optimistic: true,
        },
      ],
    }
    chat.branchViewByTab = { main: { 1: 2 } }
    chat._streamingBranchByTab = { main: { turnIndex: 1, branchId: 2 } }
    chat._branchResyncPendingByTab = {
      main: { active: true, expectedBranchByTurn: { 1: 2 } },
    }
    chat.branchOperationByTab.main = {
      type: "edit",
      phase: "starting",
      turnIndex: 1,
      predictedBranch: 2,
      requestId: "request-1",
    }

    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 1,
      branch_id: 3,
    })

    expect(chat.branchViewByTab.main).toEqual({ 1: 3 })
    expect(chat._streamingBranchByTab.main).toEqual({ turnIndex: 1, branchId: 3 })
    expect(chat._branchResyncPendingByTab.main.expectedBranchByTurn).toEqual({ 1: 3 })
    expect(chat.eventsByTab.main[0]).toMatchObject({ turn_index: 1, branch_id: 3 })
    expect(chat.branchOperationByTab.main).toMatchObject({ phase: "accepted", branchId: 3 })
  })

  it("ignores processing_start from another request id", () => {
    const chat = useChatStore()
    chat._instanceGeneration = 2
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchOperationByTab.main = {
      type: "edit",
      phase: "starting",
      turnIndex: 1,
      predictedBranch: 2,
      requestId: "request-1",
      instanceGeneration: 2,
    }

    chat._onMessage({
      type: "processing_start",
      source: "main",
      turn_index: 1,
      branch_id: 3,
      request_id: "request-2",
    })

    expect(chat.branchOperationByTab.main).toMatchObject({
      phase: "starting",
      requestId: "request-1",
    })
  })

  it("clears branch operations on idle, reset, and connection errors", () => {
    const chat = useChatStore()
    chat.activeTab = "main"
    chat.tabs = ["main"]
    chat.messagesByTab = { main: [] }
    chat.branchOperationByTab.main = { type: "edit", phase: "accepted" }

    chat._onMessage({ type: "idle", source: "main" })
    expect(chat.branchOperationByTab.main).toBeNull()

    chat.branchOperationByTab.main = { type: "edit", phase: "starting" }
    chat._onMessage({ type: "error", source: "main", content: "failed" })
    expect(chat.branchOperationByTab.main).toBeNull()
    expect(chat.branchOperationErrorByTab.main).toBe("failed")

    chat.branchOperationByTab.main = { type: "edit", phase: "starting" }
    chat.resetForRouteSwitch()
    expect(chat.branchOperationByTab).toEqual({})
  })

  it("keeps operations independent across creature tabs", () => {
    const chat = useChatStore()
    chat._setBranchOperation("alpha", { type: "edit", phase: "starting" })
    chat._setBranchOperation("beta", { type: "regenerate", phase: "starting" })

    expect(chat.branchOperationByTab.alpha.type).toBe("edit")
    expect(chat.branchOperationByTab.beta.type).toBe("regenerate")

    chat._failBranchOperation("alpha", new Error("collision"))
    expect(chat.branchOperationByTab.alpha).toBeNull()
    expect(chat.branchOperationErrorByTab.alpha).toBe("collision")
    expect(chat.branchOperationByTab.beta.type).toBe("regenerate")
  })
})
