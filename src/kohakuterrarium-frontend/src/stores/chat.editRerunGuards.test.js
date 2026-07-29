import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { _replayEvents, useChatStore } from "./chat.js"

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("chat store — edit/rerun semantic guards", () => {
  it("does not let injected mid-turn rows shift canonical user locators", () => {
    const events = [
      { type: "user_input", event_id: 1, content: "first", turn_index: 1, branch_id: 1 },
      { type: "user_message", event_id: 2, content: "first", turn_index: 1, branch_id: 1 },
      {
        type: "user_input_injected",
        event_id: 3,
        content: "steer",
        turn_index: 1,
        branch_id: 1,
      },
      { type: "turn_end", event_id: 4, content: "a1", turn_index: 1, branch_id: 1 },
      { type: "user_input", event_id: 5, content: "second", turn_index: 2, branch_id: 1 },
      { type: "user_message", event_id: 6, content: "second", turn_index: 2, branch_id: 1 },
    ]

    const replayed = _replayEvents([], events).messages
    const users = replayed.filter((message) => message.role === "user")

    expect(users).toHaveLength(3)
    expect(users[0]).toMatchObject({ content: "first", turnIndex: 1, userPosition: 0 })
    expect(users[1]).toMatchObject({ content: "steer", turnIndex: 1, injectedMidTurn: true })
    expect(users[1].userPosition).toBeUndefined()
    expect(users[2]).toMatchObject({ content: "second", turnIndex: 2, userPosition: 1 })

    const chat = useChatStore()
    chat.messagesByTab = { main: replayed }
    const secondUserIdx = replayed.findIndex((message) => message.content === "second")
    expect(chat._conversationUserPosition("main", secondUserIdx)).toBe(1)
  })

  it("keeps optimistic edit and rollback on the owning tab after active-tab changes", async () => {
    const chat = useChatStore()
    chat._instanceId = "graph_1"
    chat._instanceGraphId = "graph_1"
    chat.tabs = ["main", "other", "observer"]
    chat.activeTab = "other"
    const originalEvents = [
      { type: "user_input", event_id: 1, content: "main draft", turn_index: 1, branch_id: 1 },
      {
        type: "user_message",
        event_id: 2,
        content: "main draft",
        turn_index: 1,
        branch_id: 1,
      },
      { type: "processing_end", event_id: 3, turn_index: 1, branch_id: 1 },
    ]
    const originalMainMessages = _replayEvents([], originalEvents).messages
    const expectedMainMessages = JSON.parse(JSON.stringify(originalMainMessages))
    chat.messagesByTab = {
      main: originalMainMessages,
      other: [{ id: "other-user", role: "user", content: "other draft", turnIndex: 1 }],
      observer: [{ id: "observer-user", role: "user", content: "observer draft", turnIndex: 1 }],
    }
    chat.eventsByTab = { main: [...originalEvents] }
    chat.branchViewByTab = { main: { 1: 1 } }
    chat.processingByTab = { main: false, other: false, observer: false }

    const { agentAPI } = await import("@/utils/api")
    let rejectEdit
    const editSpy = vi.spyOn(agentAPI, "editMessage").mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          rejectEdit = reject
        }),
    )

    const editPromise = chat.editMessage(0, "edited main draft", {
      tabId: "main",
      turnIndex: 1,
      userPosition: 0,
      latestBranch: 1,
    })

    await vi.waitFor(() => expect(editSpy).toHaveBeenCalledOnce())
    expect(editSpy).toHaveBeenCalledWith(
      "graph_1",
      "main",
      0,
      "edited main draft",
      expect.objectContaining({ turnIndex: 1, userPosition: 0 }),
    )
    expect(JSON.stringify(chat.messagesByTab.main)).toContain("edited main draft")
    expect(chat.messagesByTab.other[0].content).toBe("other draft")
    expect(chat.messagesByTab.observer[0].content).toBe("observer draft")

    chat.activeTab = "observer"
    rejectEdit(Object.assign(new Error("edit conflict"), { response: { status: 409 } }))
    const result = await editPromise

    expect(result.ok).toBe(false)
    expect(chat.messagesByTab.main).toEqual(expectedMainMessages)
    expect(chat.eventsByTab.main).toEqual(originalEvents)
    expect(chat.branchViewByTab.main).toEqual({ 1: 1 })
    expect(chat.processingByTab.main).toBe(false)
    expect(chat.messagesByTab.other[0].content).toBe("other draft")
    expect(chat.messagesByTab.observer[0].content).toBe("observer draft")
    expect(chat.branchOperationByTab.main).toBeNull()
    expect(chat.branchOperationErrorByTab.main).toBe("edit conflict")
    expect(chat.branchOperationByTab.other).toBeUndefined()

    editSpy.mockRestore()
    chat._clearBranchResyncTimers()
  })
})
