import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { _replayEvents, useChatStore } from "./chat.js"

beforeEach(() => {
  setActivePinia(createPinia())
})

describe("chat store — interrupted task handling", () => {
  it("replays interrupted tool_result as interrupted instead of running", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const messages = []
    const events = [
      { type: "processing_start" },
      { type: "tool_call", name: "bash", call_id: "job_1", args: { command: "sleep 10" } },
      {
        type: "tool_result",
        name: "bash",
        call_id: "job_1",
        output: "User manually interrupted this job.",
        error: "User manually interrupted this job.",
        interrupted: true,
        final_state: "interrupted",
      },
      { type: "processing_end" },
    ]

    const { messages: replayed, pendingJobs } = _replayEvents(messages, events)

    const tool = replayed[0].parts[0]
    expect(tool.status).toBe("interrupted")
    expect(tool.result).toBe("User manually interrupted this job.")
    expect(pendingJobs).toEqual({})
  })

  it("replays interrupted subagent_result as interrupted instead of running", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const messages = []
    const events = [
      { type: "processing_start" },
      { type: "subagent_call", name: "explore", job_id: "agent_explore_1", task: "find auth" },
      {
        type: "subagent_result",
        name: "explore",
        job_id: "agent_explore_1",
        output: "User manually interrupted this job.",
        error: "User manually interrupted this job.",
        interrupted: true,
        final_state: "interrupted",
      },
      { type: "processing_end" },
    ]

    const { messages: replayed, pendingJobs } = _replayEvents(messages, events)

    const tool = replayed[0].parts[0]
    expect(tool.status).toBe("interrupted")
    expect(pendingJobs).toEqual({})
  })

  it("live tool_error with interrupted metadata clears running job as interrupted", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [{ id: "m1", role: "assistant", parts: [] }] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "tool_start",
      name: "bash",
      job_id: "job_1",
      args: { command: "sleep 10" },
      background: false,
      id: "tc_1",
    })

    chat._handleActivity("main", {
      activity_type: "tool_error",
      name: "bash",
      job_id: "job_1",
      interrupted: true,
      final_state: "interrupted",
      error: "User manually interrupted this job.",
      result: "User manually interrupted this job.",
    })

    const tool = chat._findToolPart(chat.messagesByTab.main, "bash", "job_1")
    expect(tool.status).toBe("interrupted")
    expect(tool.result).toBe("User manually interrupted this job.")
    expect(chat.runningJobs.job_1).toBeUndefined()
  })
})

describe("chat store — edit/regen live branch resync", () => {
  it("keeps edit open and restores messages when target is invalid", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat._instanceType = "agent"
    chat.activeTab = "main"
    chat.messagesByTab = {
      main: [{ id: "a1", role: "assistant", parts: [{ type: "text", content: "reply" }] }],
    }

    const ok = await chat.editMessage(0, "edited")

    expect(ok).toBe(false)
    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].role).toBe("assistant")
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()
  })

  it("schedules a canonical replay after streaming branch mutations finish", async () => {
    vi.useFakeTimers()
    try {
      const chat = useChatStore()
      chat.activeTab = "main"
      chat.messagesByTab = { main: [{ id: "u1", role: "user", content: "hi" }] }
      chat._markBranchResyncPending("main")
      const resync = vi.spyOn(chat, "_resyncHistory").mockResolvedValue(true)

      chat._onMessage({ type: "processing_end", source: "main" })
      await vi.advanceTimersByTimeAsync(400)

      expect(resync).toHaveBeenCalledWith("main")
    } finally {
      vi.useRealTimers()
    }
  })
})

describe("chat store — refresh/reconnect running state", () => {
  it("restores running parts and processing flag from history payload", () => {
    const chat = useChatStore()
    chat.messagesByTab = {
      main: [
        {
          id: "m1",
          role: "assistant",
          parts: [
            {
              type: "tool",
              id: "tc_1",
              jobId: "job_1",
              name: "bash",
              kind: "tool",
              args: { command: "sleep 10" },
              status: "interrupted",
              result: "",
              children: [],
            },
          ],
        },
      ],
    }

    chat._restoreRunningState(
      "main",
      {
        job_1: { name: "bash", type: "tool", startedAt: 123 },
      },
      true,
    )

    expect(chat.processingByTab.main).toBe(true)
    expect(chat.runningJobs.job_1).toMatchObject({ name: "bash", type: "tool" })
    expect(chat.messagesByTab.main[0].parts[0].status).toBe("running")
    expect(chat.messagesByTab.main[0].parts[0].startedAt).toBe(123)
  })
})

describe("chat store — compact round handling", () => {
  it("replays compact start/complete as a single merged compact message", () => {
    const { messages: replayed } = _replayEvents(
      [],
      [
        { type: "compact_start", round: 9 },
        {
          type: "compact_complete",
          round: 9,
          summary: "summary text",
          messages_compacted: 7,
        },
      ],
    )

    expect(replayed).toHaveLength(1)
    expect(replayed[0]).toMatchObject({
      role: "compact",
      round: 9,
      summary: "summary text",
      status: "done",
      messagesCompacted: 7,
    })
  })

  it("merges live compact start/complete for the same round", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"

    chat._handleActivity("main", {
      activity_type: "compact_start",
      round: 2,
    })
    chat._handleActivity("main", {
      activity_type: "compact_complete",
      round: 2,
      summary: "merged summary",
      messages_compacted: 12,
    })

    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0]).toMatchObject({
      role: "compact",
      round: 2,
      summary: "merged summary",
      status: "done",
      messagesCompacted: 12,
    })
  })
})

describe("chat store — Wave C text_chunk events", () => {
  it("replays text_chunk events as assistant text (Wave C streaming format)", () => {
    const messages = []
    const events = [
      { type: "user_input", content: "hi" },
      { type: "processing_start" },
      { type: "text_chunk", content: "Hel", chunk_seq: 0, event_id: 1 },
      { type: "text_chunk", content: "lo!", chunk_seq: 1, event_id: 2 },
      { type: "processing_end" },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    expect(replayed).toHaveLength(2)
    expect(replayed[0]).toMatchObject({ role: "user", content: "hi" })
    expect(replayed[1].role).toBe("assistant")
    expect(replayed[1].parts[0]).toMatchObject({ type: "text", content: "Hello!" })
  })

  it("replays legacy text events alongside text_chunk (mixed v1/v2 stream)", () => {
    const messages = []
    const events = [
      { type: "user_input", content: "hi" },
      { type: "processing_start" },
      { type: "text", content: "v1 chunk", event_id: 1 },
      { type: "text_chunk", content: " then v2", chunk_seq: 0, event_id: 2 },
      { type: "processing_end" },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    expect(replayed[1].parts[0]).toMatchObject({
      type: "text",
      content: "v1 chunk then v2",
    })
  })
})

describe("chat store — turn/branch model (regen / edit+rerun)", () => {
  it("renders only the latest branch per turn by default", () => {
    const messages = []
    const events = [
      // Turn 1, branch 1 (original)
      {
        type: "user_input",
        content: "hi",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_start",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: "OLD reply",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_end",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      // Turn 1, branch 2 (regen — self-contained, mirrored user_input)
      {
        type: "user_input",
        content: "hi",
        event_id: 5,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_start",
        event_id: 6,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "text_chunk",
        content: "NEW reply",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_end",
        event_id: 8,
        turn_index: 1,
        branch_id: 2,
      },
    ]

    const { messages: replayed } = _replayEvents(messages, events)

    expect(replayed.filter((m) => m.role === "user")).toHaveLength(1)
    const assistantMsgs = replayed.filter((m) => m.role === "assistant")
    expect(assistantMsgs).toHaveLength(1)
    const flatText = assistantMsgs[0].parts
      .filter((p) => p.type === "text")
      .map((p) => p.content)
      .join("")
    expect(flatText).toBe("NEW reply")
    expect(flatText).not.toContain("OLD reply")
  })

  it("attaches branch metadata to assistant turn for the navigator", () => {
    const messages = []
    const events = [
      {
        type: "user_input",
        content: "hi",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_start",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: "first",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_end",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "user_input",
        content: "hi",
        event_id: 5,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_start",
        event_id: 6,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "text_chunk",
        content: "second",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_end",
        event_id: 8,
        turn_index: 1,
        branch_id: 2,
      },
    ]

    const { messages: replayed, branchMeta } = _replayEvents(messages, events)

    expect(branchMeta).toBeTruthy()
    expect(branchMeta.byTurn.get(1).branches).toEqual([1, 2])

    const assistant = replayed.find((m) => m.role === "assistant")
    expect(assistant.turnIndex).toBe(1)
    expect(assistant.branches).toEqual([1, 2])
    expect(assistant.currentBranch).toBe(2)
    expect(assistant.latestBranch).toBe(2)
  })

  it("respects branchView override to flip back to branch 1", () => {
    const messages = []
    const events = [
      {
        type: "user_input",
        content: "hi",
        event_id: 1,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_start",
        event_id: 2,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "text_chunk",
        content: "first",
        chunk_seq: 0,
        event_id: 3,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "processing_end",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "user_input",
        content: "hi",
        event_id: 5,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_start",
        event_id: 6,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "text_chunk",
        content: "second",
        chunk_seq: 0,
        event_id: 7,
        turn_index: 1,
        branch_id: 2,
      },
      {
        type: "processing_end",
        event_id: 8,
        turn_index: 1,
        branch_id: 2,
      },
    ]

    const { messages: replayed } = _replayEvents(messages, events, { 1: 1 })
    const assistant = replayed.find((m) => m.role === "assistant")
    const flatText = assistant.parts
      .filter((p) => p.type === "text")
      .map((p) => p.content)
      .join("")
    expect(flatText).toBe("first")
  })
})

describe("chat store — multimodal edit + branch resync", () => {
  it("dedupes live multimodal user echoes by full content signature", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }

    const payload = [
      { type: "text", text: "hello" },
      { type: "image_url", image_url: { url: "data:image/png;base64,abc", detail: "low" } },
    ]

    chat._handleUserInput("main", { content: payload })
    chat._handleUserInput("main", { content: payload })

    expect(chat.messagesByTab.main).toHaveLength(1)
    expect(chat.messagesByTab.main[0].contentParts).toHaveLength(2)
  })

  it("replay preserves tool result metadata for frontend truncation markers", () => {
    const { messages } = _replayEvents(
      [],
      [
        { type: "processing_start" },
        { type: "tool_call", name: "read", call_id: "job_1", args: { path: "foo.txt" } },
        {
          type: "tool_result",
          name: "read",
          call_id: "job_1",
          output: "trimmed output",
          output_meta: { truncated: true, omitted_text_bytes: 1234 },
        },
        { type: "processing_end" },
      ],
    )

    expect(messages[0].parts[0].resultMeta).toEqual({ truncated: true, omitted_text_bytes: 1234 })
  })

  it("resync rebuilds messages on every poll AND keeps retrying until expected edit branch becomes canonical", async () => {
    // Regression: earlier ``_resyncHistory`` bailed early without
    // rebuilding when the expected branch hadn't promoted yet, which
    // left the chat panel showing an empty list because
    // ``editMessage`` pre-splices the local messages before awaiting
    // the API call. The new behaviour rebuilds on every poll
    // (showing whatever events landed) and only the "promotion" of
    // the expected branch is gated by the retry loop.
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"
    chat._branchResyncPendingByTab.main = {
      active: true,
      expectedBranchByTurn: { 1: 2 },
    }

    const rebuildSpy = vi.spyOn(chat, "_rebuildMessages").mockImplementation(() => {})
    const scheduleSpy = vi.spyOn(chat, "_scheduleBranchResync").mockImplementation(() => {})
    const importActual = await vi.importActual("@/utils/api")
    const getHistory = vi
      .spyOn(importActual.terrariumAPI, "getHistory")
      .mockResolvedValueOnce({
        events: [
          { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
          { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
        ],
      })
      .mockResolvedValueOnce({
        events: [
          { type: "user_input", content: "hi", event_id: 1, turn_index: 1, branch_id: 1 },
          { type: "user_message", content: "hi", event_id: 2, turn_index: 1, branch_id: 1 },
          { type: "user_input", content: "hello", event_id: 3, turn_index: 1, branch_id: 2 },
          { type: "user_message", content: "hello", event_id: 4, turn_index: 1, branch_id: 2 },
        ],
      })

    // First poll: branch=2 events have not arrived yet, so the
    // promotion check fails. Returns false, schedules a retry, but
    // STILL rebuilds messages with what we have (branch=1 events).
    await expect(chat._resyncHistory("main")).resolves.toBe(false)
    expect(scheduleSpy).toHaveBeenCalledWith("main")
    expect(chat._branchResyncPendingByTab.main).toBeTruthy()
    expect(rebuildSpy).toHaveBeenCalledWith("main")
    rebuildSpy.mockClear()

    // Second poll: branch=2 events landed; promotion succeeds.
    await expect(chat._resyncHistory("main")).resolves.toBe(true)
    expect(rebuildSpy).toHaveBeenCalledWith("main")
    expect(chat._branchResyncPendingByTab.main).toBeUndefined()

    rebuildSpy.mockRestore()
    scheduleSpy.mockRestore()
    getHistory.mockRestore()
  })
})

describe("chat store — resetForRouteSwitch", () => {
  // Regression test for the bug where the SessionHistoryViewer leaves
  // the saved-session's tabs / messages / _instanceId in the chat
  // store, so navigating to a running instance afterwards renders the
  // viewer's content for the brief window between page mount and the
  // async ``initForInstance`` call.
  it("wipes viewer state so the next live-instance render starts clean", () => {
    const chat = useChatStore()

    // Simulate the SessionHistoryViewer state after loading a saved
    // session named ``my-saved-session`` with two recorded tabs.
    chat._instanceId = "session:my-saved-session"
    chat._instanceType = "terrarium"
    chat.tabs = ["root", "swe"]
    chat.activeTab = "root"
    chat.messagesByTab = {
      root: [{ id: "m1", role: "assistant", parts: [{ type: "text", text: "frozen reply" }] }],
      swe: [{ id: "m2", role: "assistant", parts: [{ type: "text", text: "frozen output" }] }],
    }
    chat.tokenUsage = { root: { prompt: 10, completion: 5, total: 15, cached: 0 } }
    chat.runningJobs = { jobX: { name: "bash", type: "tool", startedAt: 1 } }
    chat.unreadCounts = { swe: 3 }
    chat.queuedMessages = [{ id: "q1", content: "queued", timestamp: "now" }]
    chat.processingByTab = { root: true }
    chat.eventsByTab = { root: [{ type: "text_delta", text: "stale" }] }
    chat.branchViewByTab = { root: { 0: 1 } }
    chat.sessionInfo = {
      sessionId: "saved-session-id",
      model: "saved-model",
      llmName: "saved/llm",
      agentName: "saved-agent",
      compactThreshold: 999,
      maxContext: 1000,
    }

    chat.resetForRouteSwitch()

    expect(chat._instanceId).toBeNull()
    expect(chat._instanceType).toBeNull()
    expect(chat.tabs).toEqual([])
    expect(chat.activeTab).toBeNull()
    expect(chat.messagesByTab).toEqual({})
    expect(chat.tokenUsage).toEqual({})
    expect(chat.runningJobs).toEqual({})
    expect(chat.unreadCounts).toEqual({})
    expect(chat.queuedMessages).toEqual([])
    expect(chat.processingByTab).toEqual({})
    expect(chat.eventsByTab).toEqual({})
    expect(chat.branchViewByTab).toEqual({})
    expect(chat.sessionInfo.sessionId).toBe("")
    expect(chat.sessionInfo.model).toBe("")
    expect(chat.sessionInfo.llmName).toBe("")
    expect(chat.sessionInfo.agentName).toBe("")
    expect(chat.sessionInfo.compactThreshold).toBe(0)
    expect(chat.sessionInfo.maxContext).toBe(0)

    // ``currentMessages`` getter must return an empty list — this is
    // what ChatPanel reads, and the bug surfaced as "saved messages
    // shown on a live instance" via this exact getter.
    expect(chat.currentMessages).toEqual([])
  })

  it("bumps the instance generation so in-flight WS callbacks are ignored", () => {
    const chat = useChatStore()
    const before = chat._instanceGeneration

    chat._instanceId = "session:foo"
    chat.resetForRouteSwitch()

    expect(chat._instanceGeneration).toBeGreaterThan(before)
  })
})

describe("chat store — focus-return resync", () => {
  it("refreshHistory delegates to _resyncHistory and soft-fails", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"

    const resyncSpy = vi.spyOn(chat, "_resyncHistory").mockResolvedValueOnce(true)
    await expect(chat.refreshHistory("main")).resolves.toBe(true)
    expect(resyncSpy).toHaveBeenCalledWith("main")
    resyncSpy.mockRestore()
  })

  it("refreshHistory swallows network errors so the UI doesn't flap", async () => {
    const chat = useChatStore()
    chat._instanceId = "agent_1"
    chat.activeTab = "main"

    const resyncSpy = vi.spyOn(chat, "_resyncHistory").mockRejectedValueOnce(new Error("net"))
    await expect(chat.refreshHistory("main")).resolves.toBe(false)
    resyncSpy.mockRestore()
  })

  it("refreshHistory is a no-op when no instance is bound", async () => {
    const chat = useChatStore()
    chat._instanceId = null
    chat.activeTab = "main"
    const resyncSpy = vi.spyOn(chat, "_resyncHistory").mockResolvedValueOnce(true)
    await expect(chat.refreshHistory("main")).resolves.toBe(false)
    expect(resyncSpy).not.toHaveBeenCalled()
    resyncSpy.mockRestore()
  })
})
