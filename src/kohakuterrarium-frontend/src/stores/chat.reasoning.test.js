import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

import { _convertHistory, _replayEvents, useChatStore } from "./chat"

beforeEach(() => {
  setActivePinia(createPinia())
})

function reasoningMessage() {
  return {
    role: "assistant",
    content: "answer 1answer 2",
    tool_calls: [
      {
        id: "call_1",
        type: "function",
        function: { name: "read", arguments: '{"path":"a.md"}' },
      },
    ],
    _kt_assistant_segments: [
      { type: "reasoning", source: "reasoning_content", text: "think 1" },
      { type: "text", text: "answer 1" },
      { type: "tool_call_ref", call_id: "call_1" },
      { type: "reasoning", source: "reasoning_content", text: "think 2" },
      { type: "text", text: "answer 2" },
    ],
  }
}

describe("_convertHistory reasoning segments", () => {
  it("builds ordered parts for assistant snapshots", () => {
    const messages = [
      { role: "user", content: "q" },
      reasoningMessage(),
      { role: "tool", content: "file contents", tool_call_id: "call_1" },
    ]
    const out = _convertHistory(messages)
    const assistant = out.find((message) => message.role === "assistant")
    expect(assistant.parts.map((part) => part.type)).toEqual([
      "reasoning",
      "text",
      "tool",
      "reasoning",
      "text",
    ])
    expect(assistant.parts[0].text).toBe("think 1")
    expect(assistant.parts[2].jobId).toBe("call_1")
  })
})

describe("reasoning segment ordering", () => {
  it("inserts persisted assistant_reasoning into the current message", () => {
    const events = [
      { type: "user_input", content: "q", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "processing_start", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", content: "answer 1", event_id: 3, turn_index: 1, branch_id: 1 },
      {
        type: "tool_call",
        name: "read",
        args: {},
        call_id: "call_1",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "tool_result",
        name: "read",
        output: "ok",
        call_id: "call_1",
        event_id: 5,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "assistant_reasoning",
        event_id: 6,
        turn_index: 1,
        branch_id: 1,
        _kt_assistant_segments: [
          { type: "reasoning", source: "reasoning_content", text: "think 1" },
          { type: "text", text: "answer 1" },
          { type: "tool_call_ref", call_id: "call_1" },
        ],
      },
      { type: "text_chunk", content: "answer 2", event_id: 7, turn_index: 1, branch_id: 1 },
      { type: "processing_end", event_id: 8, turn_index: 1, branch_id: 1 },
    ]
    const { messages } = _replayEvents([], events)
    const assistant = messages.find((message) => message.role === "assistant")
    expect(assistant.parts.map((part) => part.type)).toEqual(["reasoning", "text", "tool", "text"])
    expect(assistant.parts[0].text).toBe("think 1")
    expect(assistant.parts[2].jobId).toBe("call_1")
  })

  it("keeps multi-round reasoning interleaved with text and tools in arrival order", () => {
    const events = [
      { type: "user_input", content: "q", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "processing_start", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", content: "answer 1", event_id: 3, turn_index: 1, branch_id: 1 },
      {
        type: "tool_call",
        name: "read",
        args: {},
        call_id: "call_1",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "assistant_reasoning",
        event_id: 5,
        turn_index: 1,
        branch_id: 1,
        _kt_assistant_segments: [
          { type: "reasoning", source: "reasoning_content", text: "think 1" },
          { type: "text", text: "answer 1" },
          { type: "tool_call_ref", call_id: "call_1" },
        ],
      },
      {
        type: "tool_result",
        name: "read",
        output: "ok",
        call_id: "call_1",
        event_id: 6,
        turn_index: 1,
        branch_id: 1,
      },
      { type: "text_chunk", content: "answer 2", event_id: 7, turn_index: 1, branch_id: 1 },
      {
        type: "tool_call",
        name: "bash",
        args: {},
        call_id: "call_2",
        event_id: 8,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "assistant_reasoning",
        event_id: 9,
        turn_index: 1,
        branch_id: 1,
        _kt_assistant_segments: [
          { type: "reasoning", source: "reasoning_content", text: "think 2" },
          { type: "text", text: "answer 2" },
          { type: "tool_call_ref", call_id: "call_2" },
        ],
      },
      {
        type: "tool_result",
        name: "bash",
        output: "done",
        call_id: "call_2",
        event_id: 10,
        turn_index: 1,
        branch_id: 1,
      },
      { type: "processing_end", event_id: 11, turn_index: 1, branch_id: 1 },
    ]
    const { messages } = _replayEvents([], events)
    const assistant = messages.find((message) => message.role === "assistant")
    expect(assistant.parts.map((part) => part.type)).toEqual([
      "reasoning",
      "text",
      "tool",
      "reasoning",
      "text",
      "tool",
    ])
    expect(assistant.parts.map((part) => part.text || part.jobId)).toEqual([
      "think 1",
      undefined,
      "call_1",
      "think 2",
      undefined,
      "call_2",
    ])
    expect(assistant.parts[2].result).toBe("ok")
    expect(assistant.parts[5].result).toBe("done")
  })

  it("appends a trailing reasoning-only round after prior round parts", () => {
    const events = [
      { type: "user_input", content: "q", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "processing_start", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", content: "answer 1", event_id: 3, turn_index: 1, branch_id: 1 },
      {
        type: "assistant_reasoning",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
        _kt_assistant_segments: [
          { type: "reasoning", source: "reasoning_content", text: "think 1" },
          { type: "text", text: "answer 1" },
        ],
      },
      {
        type: "assistant_reasoning",
        event_id: 5,
        turn_index: 1,
        branch_id: 1,
        _kt_assistant_segments: [
          { type: "reasoning", source: "reasoning_content", text: "think 2" },
        ],
      },
      { type: "processing_end", event_id: 6, turn_index: 1, branch_id: 1 },
    ]
    const { messages } = _replayEvents([], events)
    const assistant = messages.find((message) => message.role === "assistant")
    expect(assistant.parts.map((part) => part.type)).toEqual(["reasoning", "text", "reasoning"])
    expect(assistant.parts.map((part) => part.text)).toEqual(["think 1", undefined, "think 2"])
  })
  it("keeps a reasoning round after an intervening round that emitted no reasoning", () => {
    const events = [
      { type: "user_input", content: "q", event_id: 1, turn_index: 1, branch_id: 1 },
      { type: "processing_start", event_id: 2, turn_index: 1, branch_id: 1 },
      { type: "text_chunk", content: "answer 1", event_id: 3, turn_index: 1, branch_id: 1 },
      {
        type: "tool_call",
        name: "read",
        args: {},
        call_id: "call_1",
        event_id: 4,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "token_usage",
        event_id: 5,
        turn_index: 1,
        branch_id: 1,
        prompt_tokens: 10,
        completion_tokens: 5,
        total_tokens: 15,
      },
      {
        type: "assistant_reasoning",
        event_id: 6,
        turn_index: 1,
        branch_id: 1,
        _kt_assistant_segments: [
          { type: "reasoning", source: "reasoning_content", text: "think 1" },
          { type: "text", text: "answer 1" },
          { type: "tool_call_ref", call_id: "call_1" },
        ],
      },
      {
        type: "tool_result",
        name: "read",
        output: "ok",
        call_id: "call_1",
        event_id: 7,
        turn_index: 1,
        branch_id: 1,
      },
      { type: "text_chunk", content: "answer 2", event_id: 8, turn_index: 1, branch_id: 1 },
      {
        type: "tool_call",
        name: "bash",
        args: {},
        call_id: "call_2",
        event_id: 9,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "token_usage",
        event_id: 10,
        turn_index: 1,
        branch_id: 1,
        prompt_tokens: 20,
        completion_tokens: 10,
        total_tokens: 30,
      },
      {
        type: "tool_result",
        name: "bash",
        output: "done",
        call_id: "call_2",
        event_id: 11,
        turn_index: 1,
        branch_id: 1,
      },
      { type: "text_chunk", content: "answer 3", event_id: 12, turn_index: 1, branch_id: 1 },
      {
        type: "tool_call",
        name: "grep",
        args: {},
        call_id: "call_3",
        event_id: 13,
        turn_index: 1,
        branch_id: 1,
      },
      {
        type: "token_usage",
        event_id: 14,
        turn_index: 1,
        branch_id: 1,
        prompt_tokens: 30,
        completion_tokens: 15,
        total_tokens: 45,
      },
      {
        type: "assistant_reasoning",
        event_id: 15,
        turn_index: 1,
        branch_id: 1,
        _kt_assistant_segments: [
          { type: "reasoning", source: "reasoning_content", text: "think 3" },
          { type: "text", text: "answer 3" },
          { type: "tool_call_ref", call_id: "call_3" },
        ],
      },
      {
        type: "tool_result",
        name: "grep",
        output: "hit",
        call_id: "call_3",
        event_id: 16,
        turn_index: 1,
        branch_id: 1,
      },
      { type: "processing_end", event_id: 17, turn_index: 1, branch_id: 1 },
    ]
    const { messages } = _replayEvents([], events)
    const assistant = messages.find((message) => message.role === "assistant")
    expect(assistant.parts.map((part) => part.type)).toEqual([
      "reasoning",
      "text",
      "tool",
      "text",
      "tool",
      "reasoning",
      "text",
      "tool",
    ])
    expect(assistant.parts.map((part) => part.text || part.jobId)).toEqual([
      "think 1",
      undefined,
      "call_1",
      undefined,
      "call_2",
      "think 3",
      undefined,
      "call_3",
    ])
  })

  it("keeps live reasoning after a no-reasoning round", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.activeTab = "main"

    const toolStart = (name, jobId, id) =>
      chat._handleActivity("main", {
        activity_type: "tool_start",
        name,
        job_id: jobId,
        id,
        args: {},
        background: false,
      })
    const tokenUsage = (tokens) =>
      chat._handleActivity("main", {
        activity_type: "token_usage",
        prompt_tokens: tokens,
        completion_tokens: tokens / 2,
        total_tokens: tokens * 1.5,
      })
    const reasoning = (id, text, segments) =>
      chat._handleActivity("main", {
        activity_type: "assistant_reasoning",
        id,
        _kt_assistant_segments: segments,
      })
    const toolDone = (name, jobId, output) =>
      chat._handleActivity("main", {
        activity_type: "tool_done",
        name,
        job_id: jobId,
        output,
      })

    chat._appendStreamChunk("main", "answer 1")
    toolStart("read", "call_1", "tc_1")
    tokenUsage(10)
    reasoning("reasoning_1", "", [
      { type: "reasoning", source: "reasoning_content", text: "think 1" },
      { type: "text", text: "answer 1" },
      { type: "tool_call_ref", call_id: "call_1" },
    ])
    toolDone("read", "call_1", "ok")

    chat._appendStreamChunk("main", "answer 2")
    toolStart("bash", "call_2", "tc_2")
    tokenUsage(20)
    toolDone("bash", "call_2", "done")

    chat._appendStreamChunk("main", "answer 3")
    toolStart("grep", "call_3", "tc_3")
    tokenUsage(30)
    reasoning("reasoning_3", "", [
      { type: "reasoning", source: "reasoning_content", text: "think 3" },
      { type: "text", text: "answer 3" },
      { type: "tool_call_ref", call_id: "call_3" },
    ])

    const assistant = chat.messagesByTab.main.find((message) => message.role === "assistant")
    expect(assistant.parts.map((part) => part.type)).toEqual([
      "reasoning",
      "text",
      "tool",
      "text",
      "tool",
      "reasoning",
      "text",
      "tool",
    ])
    expect(assistant.parts.map((part) => part.text || part.jobId)).toEqual([
      "think 1",
      undefined,
      "call_1",
      undefined,
      "call_2",
      "think 3",
      undefined,
      "call_3",
    ])
  })
})
