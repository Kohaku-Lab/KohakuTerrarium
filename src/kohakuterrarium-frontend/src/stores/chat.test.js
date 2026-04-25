import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

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
